package com.robocode.lukulive;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Conversation brain.
 *  - Keeps the conversation (plain user/assistant turns).
 *  - Uses the live system instruction fetched from the instruction URL (refreshed in the
 *    background so it never delays a reply).
 *  - Streams replies from Gemini or from a faster OpenAI-compatible engine (Groq by default),
 *    handing text to the UI as it arrives so speech can start on the first sentence.
 *  - Executes tool calls: web search, page reading, and HTTP actions (e.g. robot movements).
 */
final class Agent {

    interface Listener {
        void onTool(String kind, String label);
        void onToolResult(String kind, String label, boolean ok);
        /** Streamed reply text (may be a few characters or a few words). */
        void onText(String delta);
        boolean isCancelled();
        /**
         * Ask the user to approve an action and block until they answer.
         * doWhat = "wave my hand", doing = "waving my hand".
         */
        Confirmation confirmAction(String doWhat, String doing);
    }

    /** Result of asking the user to approve an action. */
    static final class Confirmation {
        final boolean approved;
        final String reason;
        final String userReply;
        Confirmation(boolean approved, String reason, String userReply) {
            this.approved = approved;
            this.reason = reason;
            this.userReply = userReply;
        }
    }

    static final class Cancelled extends Exception {
        Cancelled() { super("cancelled"); }
    }

    private static final String API = System.getProperty("luku.api", "https://generativelanguage.googleapis.com/v1beta/");
    private static final long INSTRUCTION_TTL_MS = 60 * 1000L;
    private static final int MAX_TOOL_ROUNDS = 6;
    private static final int MAX_TURNS = 30;
    private static final String[] THINKING = {"minimal", "low", null};

    private final Prefs prefs;
    private final ArrayList<String[]> turns = new ArrayList<String[]>();

    private volatile String instruction;
    private volatile long instructionFetchedAt;
    private volatile String instructionStatus = "Instructions not loaded yet";
    private volatile boolean refreshing;
    private final Object instructionLock = new Object();

    private volatile String geminiModel;
    private volatile long lastWarmAt;
    private volatile String lastEngine = "";
    private volatile long fastBlockedUntil;


    private final int browseChars = 8000;

    /** An OpenAI-compatible chat endpoint (optional Groq fast engine). */
    static final class OaTarget {
        String base, key, model, label;
        boolean onDevice;
    }

    /** Which engine answers the next turn: "local", "fast" (only when chosen in Settings) or "gemini". */
    String engine() {
        if (prefs.useLocalEngine()) return "local";
        return prefs.useFastEngine() ? "fast" : "gemini";
    }

    boolean isCodeUnrestricted(String code) {
        return code != null && unrestrictedCodes().contains(code.toUpperCase(Locale.US));
    }

    private OaTarget fastTarget() {
        OaTarget t = new OaTarget();
        t.base = prefs.fastBaseUrl();
        t.key = prefs.fastKey();
        t.model = prefs.fastModel();
        t.label = prefs.fastModel();
        return t;
    }

    /** Actions the user allowed to run without asking: marked "unrestricted" on the instruction page, or listed in Settings. */
    java.util.Set<String> unrestrictedCodes() {
        java.util.Set<String> out = parseUnrestricted(instruction);
        for (String c : prefs.unrestrictedCodes().split("[,;\\s]+")) {
            if (c.trim().length() > 0) out.add(c.trim().toUpperCase(Locale.US));
        }
        return out;
    }

    /**
     * Finds codes marked unrestricted in the instruction text. Understands a marker inside a code's own
     * entry (e.g. "Permission: Unrestricted", "[UNRESTRICTED]") and list lines such as
     * "Unrestricted codes: NOD_UP, WAVE_HAND". "Restricted" / "not unrestricted" never count.
     */
    static java.util.Set<String> parseUnrestricted(String text) {
        java.util.Set<String> out = new java.util.HashSet<String>();
        if (text == null) return out;
        Pattern marker = Pattern.compile("(?i)(?<!not\\s)(?<!non-)\\bunrestricted\\b");
        Pattern codeLine = Pattern.compile("(?im)^[\\s#*>\\-\\d.]*CODE\\s*[:=]\\s*`?([A-Z][A-Z0-9_]*)`?(.*)$");
        Matcher m = codeLine.matcher(text);
        ArrayList<int[]> starts = new ArrayList<int[]>();
        ArrayList<String> codes = new ArrayList<String>();
        while (m.find()) { starts.add(new int[]{m.start()}); codes.add(m.group(1).toUpperCase(Locale.US)); }
        for (int i = 0; i < codes.size(); i++) {
            int a = starts.get(i)[0];
            int b = i + 1 < starts.size() ? starts.get(i + 1)[0] : text.length();
            String block = text.substring(a, b);
            // stop the block at the next section heading so a later note can't leak in
            int h = block.indexOf("\n#", 1);
            int rule = block.indexOf("\n---", 1);
            if (h > 0) block = block.substring(0, h);
            if (rule > 0 && rule < block.length()) block = block.substring(0, rule);
            if (marker.matcher(block).find()) out.add(codes.get(i));
        }
        // list form: a line that says unrestricted and names codes
        Pattern token = Pattern.compile("\\b([A-Z][A-Z0-9]*_[A-Z0-9_]+|STOP|SPEAK)\\b");
        for (String line : text.split("\n")) {
            if (!marker.matcher(line).find()) continue;
            if (line.toUpperCase(Locale.US).matches("^[\\s#*>\\-\\d.]*CODE\\s*[:=].*")) continue;
            Matcher t = token.matcher(line);
            while (t.find()) out.add(t.group(1));
        }
        return out;
    }

    Agent(Prefs prefs) {
        this.prefs = prefs;
        String cached = prefs.get("instr_cache", "");
        if (cached.length() > 0) {
            instruction = cached;
            instructionStatus = "Saved instructions (" + cached.length() + " chars)";
        }
    }

    synchronized void resetConversation() { turns.clear(); }

    void onSettingsChanged() {
        geminiModel = null;
        instructionFetchedAt = 0;
        lastWarmAt = 0;
        fastBlockedUntil = 0;
    }

    String instructionStatus() { return instructionStatus; }

    String engineLabel() {
        String e = engine();
        if ("local".equals(e)) return "Local AI (On-Device)";
        if ("fast".equals(e)) return prefs.fastModel() + " (" + WebTools.hostOf(prefs.fastBaseUrl()) + ")";
        return geminiModel != null ? geminiModel : prefs.model();
    }

    String lastEngine() { return lastEngine.length() > 0 ? lastEngine : engineLabel(); }

    // ------------------------------------------------------------ instructions

    /** Blocking fetch of the instruction text (used at startup / settings). Falls back to the cached copy. */
    String refreshInstruction(boolean force) {
        synchronized (instructionLock) {
            long now = System.currentTimeMillis();
            if (!force && instruction != null && now - instructionFetchedAt < INSTRUCTION_TTL_MS) return instruction;
            String url = prefs.rewriteBridgeUrl(prefs.instructionUrl());
            if (url.length() == 0) {
                instructionStatus = "No instruction URL set";
                return instruction;
            }
            try {
                Map<String, String> h = new HashMap<String, String>();
                h.put("Accept", "text/plain,text/markdown,text/html;q=0.8,*/*;q=0.5");
                h.put("Cache-Control", "no-cache");
                Http.Resp r = Http.send("GET", url, h, null, 12000, 400000);
                if (r.code == 200 && r.body.trim().length() > 0) {
                    String text = r.body;
                    String ct = r.contentType == null ? "" : r.contentType.toLowerCase();
                    String lower = text.toLowerCase();
                    if (ct.contains("html") && (lower.contains("<html") || lower.contains("<body") || lower.contains("<div"))) {
                        text = WebTools.htmlToText(text);
                    }
                    text = text.trim();
                    instruction = text;
                    instructionFetchedAt = now;
                    prefs.put("instr_cache", text);
                    instructionStatus = "Live instructions loaded (" + text.length() + " chars)";
                } else {
                    instructionStatus = "Instruction URL returned HTTP " + r.code + (instruction != null ? " - using saved copy" : "");
                }
            } catch (Exception e) {
                // Address or port changed: attempt auto-discovery of active bridge!
                String found = BridgeDiscovery.findWorkingBridge(prefs.getContext(), prefs);
                if (found != null && !found.isEmpty()) {
                    prefs.updateDiscoveredBridge(found);
                    String retryUrl = prefs.rewriteBridgeUrl(prefs.instructionUrl());
                    try {
                        Map<String, String> h = new HashMap<String, String>();
                        h.put("Accept", "text/plain,text/markdown,text/html;q=0.8,*/*;q=0.5");
                        h.put("Cache-Control", "no-cache");
                        Http.Resp r = Http.send("GET", retryUrl, h, null, 8000, 400000);
                        if (r.code == 200 && r.body.trim().length() > 0) {
                            String text = r.body;
                            String ct = r.contentType == null ? "" : r.contentType.toLowerCase();
                            String lower = text.toLowerCase();
                            if (ct.contains("html") && (lower.contains("<html") || lower.contains("<body") || lower.contains("<div"))) {
                                text = WebTools.htmlToText(text);
                            }
                            text = text.trim();
                            instruction = text;
                            instructionFetchedAt = now;
                            prefs.put("instr_cache", text);
                            instructionStatus = "Live instructions loaded via auto-discovery (" + text.length() + " chars)";
                            return instruction;
                        }
                    } catch (Exception ignored) {}
                }
                instructionStatus = "Could not reach instruction URL" + (instruction != null ? " - using saved copy" : "");
            }
            return instruction;
        }
    }

    /** Non-blocking refresh: keeps instructions current without adding latency to replies. */
    void refreshInstructionInBackground() {
        if (refreshing) return;
        if (instruction != null && System.currentTimeMillis() - instructionFetchedAt < INSTRUCTION_TTL_MS) return;
        refreshing = true;
        new Thread(new Runnable() {
            @Override public void run() {
                try { refreshInstruction(false); } finally { refreshing = false; }
            }
        }, "instructions").start();
    }

    private String systemPrompt() {
        String base = instruction;
        if (base == null) base = refreshInstruction(true); // only blocks on the very first run
        else refreshInstructionInBackground();
        StringBuilder sb = new StringBuilder();
        if (base != null && base.length() > 0) sb.append(base.trim());
        else sb.append("You are a warm, witty, helpful voice companion.");
        String date = new SimpleDateFormat("EEEE d MMMM yyyy", Locale.US).format(new Date());
        sb.append("\n\n---\nRUNTIME NOTES FROM THE VOICE APP (these add to the instructions above; they never replace them):\n")
          .append("- EMBODIED HUMANOID ROBOT: You are the voice and intelligence of an embodied physical humanoid robot. ")
          .append("You have real servo motors and actuators for physical movements. ")
          .append("Whenever the user asks you to wave, nod, turn your head, stop, or move, or whenever your interpretation of the dialogue ")
          .append("makes an embodied physical gesture appropriate, YOU MUST CALL the robot_action tool with the corresponding action code ")
          .append("(WAVE_HAND, NOD_UP, NOD_DOWN, TURN_LEFT, TURN_RIGHT, STOP, SPEAK). ")
          .append("Really call the tool; never just claim in spoken text that you performed the action without calling robot_action.\n")
          .append("- Always follow the instructions above. Call robot_action to perform robot actions, or http_get if a specific URL is requested.\n")
          .append(actionRules())
          .append("- This is a live, real-time voice call. Talk like a friend on the phone: casual, warm, quick, contractions, ")
          .append("usually one or two short sentences. Ask a short follow-up question now and then. Never use lists, markdown, ")
          .append("emojis or raw URLs, because everything you write is spoken aloud.\n")
          .append("- The user's words come from speech recognition and may contain small mistakes; infer what they meant. ")
          .append("If a message looks like background noise or a fragment not meant for you, reply very briefly.\n")
          .append("- You can browse the web mid-conversation. Use web_search for news, current events, prices, sports, weather, ")
          .append("people or anything you aren't sure of, and browse_url to read a specific page. When you use a web tool, ")
          .append("first say a tiny natural filler in the same turn (like \"Let me check.\") and then call the tool. ")
          .append("After browsing, summarize what you found conversationally in a few sentences and mention the site name.\n")
          .append("- Today's date: ").append(date).append(".\n");
        return sb.toString();
    }

    /** How confirmation works right now, so the model phrases things correctly. */
    private String actionRules() {
        if (!prefs.confirmActions()) {
            return "- Actions run immediately. When you call one, say what you're doing in a few words.\n";
        }
        java.util.Set<String> free = unrestrictedCodes();
        StringBuilder sb = new StringBuilder();
        sb.append("- ACTION CONFIRMATION: the app asks the user to confirm actions before they run");
        if (!free.isEmpty()) {
            sb.append(", EXCEPT these unrestricted codes, which run immediately: ").append(joinSorted(free))
              .append(". For those, briefly say what you're doing (for example \"Waving now!\") and call the tool");
        }
        sb.append(". Emergency STOP always runs immediately. For every other action, call http_get straight away WITHOUT ")
          .append("announcing it; the app itself asks \"should I ...?\", waits for yes or no, and announces it when approved. ")
          .append("Then read the tool result: if status is not_executed the user declined or did not confirm, so do not claim ")
          .append("you did it and do not call it again unless they ask again; if user_reply is present, respond to what they said. ")
          .append("If a confirmed action ran, reply with at most a very short remark, because it was already announced.\n");
        return sb.toString();
    }

    private static String joinSorted(java.util.Set<String> set) {
        ArrayList<String> l = new ArrayList<String>(set);
        java.util.Collections.sort(l);
        StringBuilder sb = new StringBuilder();
        for (String x : l) { if (sb.length() > 0) sb.append(", "); sb.append(x); }
        return sb.toString();
    }

    // ------------------------------------------------------------ warm-up

    /** Opens (or refreshes) the TLS connection to the AI engine so the next request starts instantly. */
    void warmUpAsync() {
        long now = System.currentTimeMillis();
        if (now - lastWarmAt < 45000) return;
        lastWarmAt = now;
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    Map<String, String> h = new HashMap<String, String>();
                    String e = engine();
                    if ("local".equals(e)) return;
                    if ("fast".equals(e)) {
                        h.put("Authorization", "Bearer " + prefs.fastKey());
                        Http.send("GET", prefs.fastBaseUrl() + "/models", h, null, 10000, 300000);
                    } else if (prefs.geminiKey().length() > 0) {
                        if (catalog == null) {
                            loadCatalog(); // also opens the connection
                        } else {
                            h.put("x-goog-api-key", prefs.geminiKey());
                            String m = geminiModel != null ? geminiModel : prefs.model();
                            Http.send("GET", API + "models/" + m, h, null, 10000, 100000);
                        }
                    }
                } catch (Exception ignored) { }
            }
        }, "warmup").start();
    }

    // ------------------------------------------------------------ conversation

    /** Runs one user turn to completion (streaming text to the listener) and returns the whole reply. */
    synchronized String respond(String userText, Listener l) throws Exception {
        String sys = systemPrompt();
        final boolean[] streamed = {false};
        Listener tracking = track(l, streamed);
        String reply = null;
        String engine = engine();
        if ("local".equals(engine)) {
            reply = LocalBrain.respond(sys, userText, tracking, prefs, turns, this);
            lastEngine = "Local AI (On-Device)";
        } else if ("fast".equals(engine) && System.currentTimeMillis() >= fastBlockedUntil) {
            try {
                reply = respondOpenAi(sys, userText, tracking, fastTarget());
                lastEngine = prefs.fastModel();
            } catch (Cancelled c) {
                throw c;
            } catch (Exception e) {
                if (streamed[0] || prefs.geminiKey().length() == 0) throw e;
                String m = String.valueOf(e.getMessage());
                // don't keep paying for a failing engine: a bad key waits for a settings change, other errors 60 s
                fastBlockedUntil = System.currentTimeMillis() + (m.contains("API key") ? Long.MAX_VALUE / 2 : 60000L);
                l.onTool("info", "Fast engine failed (" + short1(e.getMessage()) + ") - using Gemini");
                reply = respondGemini(sys, userText, tracking);
                lastEngine = geminiModel != null ? geminiModel : prefs.model();
            }
        } else {
            reply = respondGemini(sys, userText, tracking);
            lastEngine = geminiModel != null ? geminiModel : prefs.model();
        }
        reply = reply.trim();
        if (reply.length() == 0) {
            reply = "Okay.";
            l.onText(reply);
        }
        turns.add(new String[]{"user", userText});
        turns.add(new String[]{"assistant", reply});
        while (turns.size() > MAX_TURNS) {
            turns.remove(0);
            turns.remove(0);
        }
        return reply;
    }

    private static Listener track(final Listener l, final boolean[] streamed) {
        return new Listener() {
            @Override public void onTool(String k, String s) { l.onTool(k, s); }
            @Override public void onToolResult(String k, String s, boolean ok) { l.onToolResult(k, s, ok); }
            @Override public void onText(String d) { if (d.length() > 0) streamed[0] = true; l.onText(d); }
            @Override public boolean isCancelled() { return l.isCancelled(); }
            @Override public Confirmation confirmAction(String a, String b) { return l.confirmAction(a, b); }
        };
    }

    // ------------------------------------------------------------ Gemini (streaming)

    private String respondGemini(String sys, String userText, Listener l) throws Exception {
        turnSignatureModel = null;
        if (prefs.geminiKey().length() == 0) throw new Exception("Add your Gemini API key in Settings.");
        JSONArray contents = new JSONArray();
        for (String[] t : turns) {
            contents.put(new JSONObject()
                    .put("role", "assistant".equals(t[0]) ? "model" : "user")
                    .put("parts", new JSONArray().put(new JSONObject().put("text", t[1]))));
        }
        contents.put(new JSONObject().put("role", "user")
                .put("parts", new JSONArray().put(new JSONObject().put("text", userText))));

        StringBuilder all = new StringBuilder();
        for (int round = 0; round < MAX_TOOL_ROUNDS; round++) {
            GeminiTurn t = geminiStream(sys, contents, l, round);
            if (t.text.length() > 0) {
                if (all.length() > 0) all.append(' ');
                all.append(t.text.trim());
            }
            if (t.content.getJSONArray("parts").length() > 0) contents.put(t.content);
            if (t.calls.size() == 0) return all.toString();
            JSONArray responses = new JSONArray();
            ArrayList<String> names = new ArrayList<String>();
            ArrayList<JSONObject> argList = new ArrayList<JSONObject>();
            for (JSONObject call : t.calls) {
                JSONObject args = call.optJSONObject("args");
                names.add(call.optString("name"));
                argList.add(args == null ? new JSONObject() : args);
            }
            Confirmation[] decisions = approveActions(names, argList, l);
            for (int ci = 0; ci < t.calls.size(); ci++) {
                JSONObject call = t.calls.get(ci);
                String name = names.get(ci);
                JSONObject args = argList.get(ci);
                if (l.isCancelled()) throw new Cancelled();
                JSONObject result = runTool(name, args, l, decisions[ci]);
                JSONObject fr = new JSONObject().put("name", name).put("response", result);
                if (call.has("id")) fr.put("id", call.get("id"));
                responses.put(new JSONObject().put("functionResponse", fr));
            }
            contents.put(new JSONObject().put("role", "user").put("parts", responses));
        }
        return all.toString();
    }

    private static final class GeminiTurn {
        final StringBuilder textBuf = new StringBuilder();
        String text = "";
        String textSignature;
        final ArrayList<JSONObject> callParts = new ArrayList<JSONObject>();
        final ArrayList<JSONObject> calls = new ArrayList<JSONObject>();
        JSONObject content;
        String blockReason;
        String error;
    }

    // Fallback order when a model is overloaded (503), rate-limited (429) or missing (404).
    private static final String[] FALLBACK_MODELS = {
        "gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-flash-latest",
        "gemini-3-flash-preview", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest",
        "gemini-2.5-flash", "gemini-2.5-flash-lite"
    };
    private static final String DUMMY_SIGNATURE = "skip_thought_signature_validator";

    /** Model -> time until which it is skipped (busy / rate limited / missing). */
    private final java.util.concurrent.ConcurrentHashMap<String, Long> busyUntil =
            new java.util.concurrent.ConcurrentHashMap<String, Long>();
    /** Model -> index into THINKING (some models reject some thinking levels). */
    private final java.util.concurrent.ConcurrentHashMap<String, Integer> thinkingFor =
            new java.util.concurrent.ConcurrentHashMap<String, Integer>();
    /** Text models this API key can use (null until fetched). */
    private volatile java.util.Set<String> catalog;
    /** Which model produced the thought signatures in the current turn's history. */
    private String turnSignatureModel;

    private JSONObject geminiRequest(String sys, JSONArray contents, String model) throws JSONException {
        JSONObject body = new JSONObject();
        body.put("systemInstruction", new JSONObject()
                .put("parts", new JSONArray().put(new JSONObject().put("text", sys))));
        body.put("contents", contents);
        body.put("tools", new JSONArray().put(new JSONObject().put("functionDeclarations", toolDeclarations(false))));
        JSONObject gen = new JSONObject();
        Integer mode = thinkingFor.get(model);
        String level = THINKING[Math.min(mode == null ? 0 : mode, THINKING.length - 1)];
        if (level != null) {
            JSONObject thinking = new JSONObject();
            if (model.contains("2.5") || model.contains("2.0")) thinking.put("thinkingBudget", 0);
            else thinking.put("thinkingLevel", level);
            gen.put("thinkingConfig", thinking);
        }
        body.put("generationConfig", gen);
        return body;
    }

    /** Preferred model first, then the fallbacks; models that are cooling down go last. */
    private ArrayList<String> modelOrder() {
        ArrayList<String> all = new ArrayList<String>();
        all.add(prefs.model());
        for (String m : FALLBACK_MODELS) if (!all.contains(m)) all.add(m);
        java.util.Set<String> cat = catalog;
        if (cat != null) {
            ArrayList<String> extra = new ArrayList<String>();
            for (String m : cat) if (!all.contains(m) && isChatFlash(m)) extra.add(m);
            java.util.Collections.sort(extra, new java.util.Comparator<String>() {
                @Override public int compare(String a, String b) { return versionScore(b) - versionScore(a); }
            });
            all.addAll(extra);
            ArrayList<String> known = new ArrayList<String>();
            for (String m : all) if (cat.contains(m)) known.add(m);
            if (!known.isEmpty()) all = known;
        }
        long now = System.currentTimeMillis();
        ArrayList<String> ready = new ArrayList<String>(), cooling = new ArrayList<String>();
        for (String m : all) {
            Long until = busyUntil.get(m);
            if (until != null && until > now) cooling.add(m); else ready.add(m);
        }
        final java.util.Map<String, Long> snap = new HashMap<String, Long>(busyUntil);
        java.util.Collections.sort(cooling, new java.util.Comparator<String>() {
            @Override public int compare(String a, String b) { return Long.compare(snap.get(a), snap.get(b)); }
        });
        ready.addAll(cooling);
        return ready;
    }

    private static boolean isChatFlash(String n) {
        n = n.toLowerCase(Locale.US);
        return n.startsWith("gemini") && n.contains("flash") && !n.contains("image") && !n.contains("tts")
                && !n.contains("audio") && !n.contains("live") && !n.contains("embed") && !n.contains("robotics");
    }

    private void cool(String model, long ms) {
        busyUntil.put(model, System.currentTimeMillis() + ms);
    }

    /** Gives history to a different model: signatures are only valid for the model that made them. */
    private static JSONArray withNeutralSignatures(JSONArray contents) throws JSONException {
        JSONArray copy = new JSONArray(contents.toString());
        for (int i = 0; i < copy.length(); i++) {
            JSONArray parts = copy.getJSONObject(i).optJSONArray("parts");
            for (int j = 0; parts != null && j < parts.length(); j++) {
                JSONObject p = parts.optJSONObject(j);
                if (p != null && p.has("thoughtSignature")) p.put("thoughtSignature", DUMMY_SIGNATURE);
            }
        }
        return copy;
    }

    private GeminiTurn geminiStream(String sys, JSONArray contents, final Listener l, int round) throws Exception {
        String key = prefs.geminiKey();
        String preferred = prefs.model();
        ArrayList<String> order = modelOrder();
        String lastProblem = "";
        int tries = 0;
        for (int idx = 0; idx < order.size() && tries < 8; idx++) {
            final String model = order.get(idx);
            tries++;
            final GeminiTurn t = new GeminiTurn();
            JSONArray send = contents;
            if (turnSignatureModel != null && !turnSignatureModel.equals(model)) send = withNeutralSignatures(contents);
            byte[] payload = geminiRequest(sys, send, model).toString().getBytes("UTF-8");
            Map<String, String> h = new HashMap<String, String>();
            h.put("Content-Type", "application/json; charset=utf-8");
            h.put("Accept", "text/event-stream");
            h.put("x-goog-api-key", key);
            Http.Resp r;
            try {
                // Short timeouts: an overloaded model is abandoned quickly in favour of the next one.
                r = Http.stream(API + "models/" + model + ":streamGenerateContent?alt=sse", h, payload, 6000, 15000,
                        new Http.LineHandler() {
                            @Override public void onLine(String line) throws Exception {
                                if (!line.startsWith("data:")) return;
                                String data = line.substring(5).trim();
                                if (data.length() == 0) return;
                                handleGeminiChunk(new JSONObject(data), t, l);
                            }
                            @Override public boolean cancelled() { return l.isCancelled(); }
                        });
            } catch (Http.CancelledException ce) {
                throw new Cancelled();
            } catch (java.io.IOException io) {
                if (l.isCancelled()) throw new Cancelled();
                if (t.textBuf.length() > 0 || !t.calls.isEmpty()) {
                    throw new Exception("The connection to Gemini dropped mid-reply.");
                }
                cool(model, 30000);
                lastProblem = model + " timed out";
                if (tries >= 3 && !networkUp()) throw new Exception("Network problem reaching Gemini. Check your connection.");
                continue;
            }
            if (r.code == 200) {
                if (t.error != null) {
                    if (t.textBuf.length() == 0 && t.calls.isEmpty()) {
                        cool(model, 45000);
                        lastProblem = model + ": " + short1(t.error, 80);
                        continue;
                    }
                    throw new Exception("Gemini: " + short1(t.error));
                }
                t.text = t.textBuf.toString();
                JSONArray parts = new JSONArray();
                if (t.text.length() > 0 || t.textSignature != null) {
                    JSONObject tp = new JSONObject().put("text", t.text);
                    if (t.textSignature != null) tp.put("thoughtSignature", t.textSignature);
                    parts.put(tp);
                }
                for (JSONObject p : t.callParts) parts.put(p);
                t.content = new JSONObject().put("role", "model").put("parts", parts);
                if (parts.length() == 0 && t.blockReason != null && round == 0) {
                    throw new Exception("Gemini blocked that request (" + t.blockReason + ").");
                }
                if (turnSignatureModel != null && !turnSignatureModel.equals(model)) {
                    // history now mixes models: neutralise older signatures for the rest of this turn
                    JSONArray fixed = withNeutralSignatures(contents);
                    for (int i = 0; i < fixed.length(); i++) contents.put(i, fixed.get(i));
                }
                turnSignatureModel = model;
                busyUntil.remove(model);
                if (!model.equals(geminiModel) && !model.equals(preferred)) {
                    l.onTool("info", preferred + " is busy — answering with " + model);
                }
                geminiModel = model;
                return t;
            }
            String err = errorMessage(r.body);
            String lower = err.toLowerCase(Locale.US);
            Integer mode = thinkingFor.get(model);
            int m = mode == null ? 0 : mode;
            if (r.code == 400 && lower.contains("think") && m < THINKING.length - 1) {
                thinkingFor.put(model, m + 1);
                idx--; // same model again with a gentler thinking setting
                continue;
            }
            if (r.code == 400 && lower.contains("signature") && send == contents) {
                turnSignatureModel = "";      // force neutral signatures and retry the same model
                idx--;
                continue;
            }
            if (r.code == 400 && lower.contains("api key")) throw new Exception("Gemini rejected the API key. Check it in Settings.");
            if (r.code == 401 || (r.code == 403 && !lower.contains("model"))) {
                throw new Exception("Gemini refused the request: " + short1(err));
            }
            if (r.code == 404 || r.code == 403 || (r.code == 400 && (lower.contains("not found")
                    || lower.contains("not supported") || lower.contains("not available")))) {
                cool(model, 3600000L); // missing for this key: skip it for an hour
            } else if (r.code == 429) {
                cool(model, 60000);    // per-model quota: another model usually still has room
            } else if (r.code >= 500) {
                cool(model, 45000);    // overloaded / high demand
            } else {
                throw new Exception("Gemini error " + r.code + ": " + short1(err));
            }
            lastProblem = model + " (" + r.code + ")";
        }
        throw new Exception("All Gemini models are busy right now (" + lastProblem + "). Try again in a moment.");
    }

    private static boolean networkUp() {
        try {
            java.net.InetAddress.getByName("generativelanguage.googleapis.com");
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private static void handleGeminiChunk(JSONObject chunk, GeminiTurn t, Listener l) throws JSONException {
        JSONObject err = chunk.optJSONObject("error");
        if (err != null) {
            t.error = err.optString("message", err.toString());
            return;
        }
        JSONObject fb = chunk.optJSONObject("promptFeedback");
        if (fb != null && fb.has("blockReason")) t.blockReason = fb.optString("blockReason");
        JSONArray cands = chunk.optJSONArray("candidates");
        if (cands == null || cands.length() == 0) return;
        JSONObject content = cands.getJSONObject(0).optJSONObject("content");
        if (content == null) return;
        JSONArray parts = content.optJSONArray("parts");
        for (int i = 0; parts != null && i < parts.length(); i++) {
            JSONObject p = parts.optJSONObject(i);
            if (p == null) continue;
            if (p.has("functionCall")) {
                t.callParts.add(p);
                t.calls.add(p.getJSONObject("functionCall"));
            } else if (p.optBoolean("thought", false)) {
                // internal reasoning - never spoken
            } else {
                if (p.has("thoughtSignature")) t.textSignature = p.optString("thoughtSignature");
                String d = p.optString("text", "");
                if (d.length() > 0) {
                    t.textBuf.append(d);
                    l.onText(d);
                }
            }
        }
    }

    /** Learns which models this key can use, so fallbacks never waste time on a missing model. */
    void loadCatalog() {
        if (catalog != null || prefs.geminiKey().length() == 0) return;
        try {
            Map<String, String> h = new HashMap<String, String>();
            h.put("x-goog-api-key", prefs.geminiKey());
            Http.Resp r = Http.send("GET", API + "models?pageSize=1000", h, null, 15000, 4000000);
            if (r.code != 200) return;
            JSONArray models = new JSONObject(r.body).optJSONArray("models");
            java.util.Set<String> set = new java.util.HashSet<String>();
            for (int i = 0; models != null && i < models.length(); i++) {
                JSONObject m = models.getJSONObject(i);
                JSONArray methods = m.optJSONArray("supportedGenerationMethods");
                if (methods == null || !methods.toString().contains("\"generateContent\"")) continue;
                set.add(m.optString("name").replace("models/", ""));
            }
            if (!set.isEmpty()) catalog = set;
        } catch (Exception ignored) { }
    }

    private static int versionScore(String n) {
        Matcher m = Pattern.compile("gemini-(\\d+)(?:\\.(\\d+))?").matcher(n);
        if (!m.find()) return n.contains("latest") ? 1000 : 0;
        int major = Integer.parseInt(m.group(1));
        int minor = m.group(2) == null ? 0 : Integer.parseInt(m.group(2));
        return major * 20 + minor - (n.contains("lite") ? 5 : 0) - (n.contains("preview") ? 2 : 0);
    }

    // ------------------------------------------------------------ OpenAI-compatible (Groq etc.)

    private static final class OaCall {
        String id = "", name = "";
        final StringBuilder args = new StringBuilder();
    }

    private String respondOpenAi(String sys, String userText, Listener l, OaTarget target) throws Exception {
        JSONArray msgs = new JSONArray();
        msgs.put(new JSONObject().put("role", "system").put("content", sys));
        for (String[] t : turns) msgs.put(new JSONObject().put("role", t[0]).put("content", t[1]));
        msgs.put(new JSONObject().put("role", "user").put("content", userText));

        StringBuilder all = new StringBuilder();
        for (int round = 0; round < MAX_TOOL_ROUNDS; round++) {
            final StringBuilder text = new StringBuilder();
            final TreeMap<Integer, OaCall> calls = new TreeMap<Integer, OaCall>();
            oaStream(msgs, text, calls, l, target);
            if (text.length() > 0) {
                if (all.length() > 0) all.append(' ');
                all.append(text.toString().trim());
            }
            if (calls.isEmpty()) return all.toString();

            JSONArray toolCalls = new JSONArray();
            int idx = 0;
            for (OaCall c : calls.values()) {
                if (c.id.length() == 0) c.id = "call_" + round + "_" + idx;
                idx++;
                toolCalls.put(new JSONObject().put("id", c.id).put("type", "function")
                        .put("function", new JSONObject().put("name", c.name)
                                .put("arguments", c.args.length() == 0 ? "{}" : c.args.toString())));
            }
            JSONObject assistant = new JSONObject().put("role", "assistant").put("tool_calls", toolCalls);
            assistant.put("content", text.length() > 0 ? text.toString() : JSONObject.NULL);
            msgs.put(assistant);
            ArrayList<OaCall> callList = new ArrayList<OaCall>(calls.values());
            ArrayList<String> names = new ArrayList<String>();
            ArrayList<JSONObject> argList = new ArrayList<JSONObject>();
            for (OaCall c : callList) {
                JSONObject args;
                try { args = new JSONObject(c.args.length() == 0 ? "{}" : c.args.toString()); } catch (Exception e) { args = new JSONObject(); }
                names.add(c.name);
                argList.add(args);
            }
            Confirmation[] decisions = approveActions(names, argList, l);
            for (int ci = 0; ci < callList.size(); ci++) {
                OaCall c = callList.get(ci);
                if (l.isCancelled()) throw new Cancelled();
                JSONObject result = runTool(c.name, argList.get(ci), l, decisions[ci]);
                msgs.put(new JSONObject().put("role", "tool").put("tool_call_id", c.id).put("content", result.toString()));
            }
        }
        return all.toString();
    }

    private void oaStream(JSONArray msgs, final StringBuilder text, final TreeMap<Integer, OaCall> calls,
                          final Listener l, OaTarget target) throws Exception {
        String model = target.model;
        for (int attempt = 0; attempt < 3; attempt++) {
            text.setLength(0);
            calls.clear();
            JSONObject body = new JSONObject()
                    .put("model", model)
                    .put("messages", msgs)
                    .put("tools", toolDeclarations(true))
                    .put("tool_choice", "auto")
                    .put("stream", true)
                    .put("temperature", 0.6)
                    .put("max_tokens", target.onDevice ? 400 : 800);
            if (model.contains("gpt-oss")) body.put("reasoning_effort", "low");
            if (target.onDevice) {
                // Qwen3 non-thinking mode: answer immediately, no hidden reasoning tokens
                body.put("chat_template_kwargs", new JSONObject().put("enable_thinking", false));
                body.put("temperature", 0.7).put("top_p", 0.8).put("top_k", 20);
                body.put("cache_prompt", true);
            }
            Map<String, String> h = new HashMap<String, String>();
            h.put("Content-Type", "application/json; charset=utf-8");
            h.put("Accept", "text/event-stream");
            if (target.key != null && target.key.length() > 0) h.put("Authorization", "Bearer " + target.key);
            Http.Resp r;
            try {
                r = Http.stream(target.base + "/chat/completions", h, body.toString().getBytes("UTF-8"), 10000,
                        target.onDevice ? 180000 : 45000,
                        new Http.LineHandler() {
                            @Override public void onLine(String line) throws Exception {
                                if (!line.startsWith("data:")) return;
                                String data = line.substring(5).trim();
                                if (data.length() == 0 || "[DONE]".equals(data)) return;
                                JSONObject chunk = new JSONObject(data);
                                JSONObject e = chunk.optJSONObject("error");
                                if (e != null) throw new Exception(e.optString("message", "stream error"));
                                JSONArray choices = chunk.optJSONArray("choices");
                                if (choices == null || choices.length() == 0) return;
                                JSONObject delta = choices.getJSONObject(0).optJSONObject("delta");
                                if (delta == null) return;
                                Object content = delta.opt("content");
                                if (content instanceof String && ((String) content).length() > 0) {
                                    text.append((String) content);
                                    l.onText((String) content);
                                }
                                JSONArray tc = delta.optJSONArray("tool_calls");
                                for (int i = 0; tc != null && i < tc.length(); i++) {
                                    JSONObject c = tc.getJSONObject(i);
                                    int index = c.optInt("index", i);
                                    OaCall call = calls.get(index);
                                    if (call == null) { call = new OaCall(); calls.put(index, call); }
                                    String id = c.optString("id", "");
                                    if (id.length() > 0) call.id = id;
                                    JSONObject fn = c.optJSONObject("function");
                                    if (fn != null) {
                                        String n = fn.optString("name", "");
                                        if (n.length() > 0) call.name = n;
                                        Object a = fn.opt("arguments");
                                        if (a instanceof String) call.args.append((String) a);
                                    }
                                }
                            }
                            @Override public boolean cancelled() { return l.isCancelled(); }
                        });
            } catch (Http.CancelledException ce) {
                throw new Cancelled();
            } catch (java.io.IOException io) {
                if (l.isCancelled()) throw new Cancelled();
                if (attempt == 0 && text.length() == 0) continue;
                throw new Exception(io.getMessage() == null ? "network error" : io.getMessage());
            }
            if (r.code == 200) return;
            String err = errorMessage(r.body);
            if (r.code == 400 && r.body.contains("tool_use_failed") && attempt == 0) continue; // model garbled a tool call; retry once
            if (r.code == 401) throw new Exception(target.onDevice ? "on-device server refused the request" : "fast engine rejected the API key");
            if (r.code == 429) throw new Exception("rate limit reached");
            if (r.code >= 500 && attempt < 2) { Thread.sleep(300); continue; }
            throw new Exception("HTTP " + r.code + ": " + short1(err));
        }
        throw new Exception("no response");
    }

    // ------------------------------------------------------------ tools

    private static JSONArray toolDeclarations(boolean openAiFormat) throws JSONException {
        JSONArray arr = new JSONArray();
        JSONObject[] fns = {
            fnRobotAction(),
            fn("http_get",
                "Send an HTTP GET request to a URL and return the status code and response body. Use this to carry out "
                    + "any action or instruction request your system instructions describe.",
                "url", "The complete URL including the query string."),
            fn("web_search",
                "Search the web and get the top results (title, url, snippet). Use for current events, news, facts, "
                    + "prices, sports, weather or anything you are unsure about.",
                "query", "The search query."),
            fn("browse_url",
                "Open a web page and read its title and main text so you can summarize it or answer from it.",
                "url", "The page URL to open.")
        };
        for (JSONObject f : fns) {
            arr.put(openAiFormat ? new JSONObject().put("type", "function").put("function", f) : f);
        }
        return arr;
    }

    private static JSONObject fnRobotAction() throws JSONException {
        JSONObject props = new JSONObject();
        props.put("code", new JSONObject().put("type", "string").put("description",
                "The physical robot action code to perform. Allowed codes: WAVE_HAND (wave hand/arm), NOD_UP (tilt head up), NOD_DOWN (tilt head down), TURN_LEFT (turn head left), TURN_RIGHT (turn head right), STOP (emergency halt all motors), SPEAK (speak a phrase)."));
        props.put("value", new JSONObject().put("type", "string").put("description",
                "Optional parameter for the action. For WAVE_HAND: number of waves (default '2'). For NOD_UP/NOD_DOWN/TURN_LEFT/TURN_RIGHT: angle in degrees (e.g. '15' or '30'). For SPEAK: phrase to speak. For STOP: '1'."));
        JSONObject params = new JSONObject().put("type", "object").put("properties", props)
                .put("required", new JSONArray().put("code"));
        return new JSONObject().put("name", "robot_action")
                .put("description", "Execute a physical robot action based on conversational interpretation. Always call this whenever the user asks the robot to wave, nod, turn, stop, or move, or when dialogue context makes an embodied action appropriate.")
                .put("parameters", params);
    }

    private static JSONObject fn(String name, String desc, String param, String paramDesc) throws JSONException {
        JSONObject props = new JSONObject().put(param, new JSONObject().put("type", "string").put("description", paramDesc));
        JSONObject params = new JSONObject().put("type", "object").put("properties", props)
                .put("required", new JSONArray().put(param));
        return new JSONObject().put("name", name).put("description", desc).put("parameters", params);
    }

    /** Asks the user once for all non-emergency actions in this round. Null entries need no approval. */
    private Confirmation[] approveActions(ArrayList<String> names, ArrayList<JSONObject> args, Listener l) {
        Confirmation[] out = new Confirmation[names.size()];
        if (!prefs.confirmActions()) return out;
        java.util.Set<String> free = unrestrictedCodes();
        ArrayList<Integer> need = new ArrayList<Integer>();
        StringBuilder doWhat = new StringBuilder(), doing = new StringBuilder();
        for (int i = 0; i < names.size(); i++) {
            String name = names.get(i);
            ActionText a;
            if ("robot_action".equals(name)) {
                String c = args.get(i).optString("code", "").trim().toUpperCase(Locale.US);
                String v = args.get(i).optString("value", "").trim();
                a = describeCode(c, v, null);
            } else if ("http_get".equals(name)) {
                a = describe(args.get(i).optString("url").trim().replace(" ", "%20"));
            } else {
                continue;
            }
            if (a.emergency) continue; // an emergency stop must never wait for a yes
            if (a.code != null && free.contains(a.code)) continue; // marked unrestricted: run without asking
            need.add(i);
            if (doWhat.length() > 0) { doWhat.append(" and then "); doing.append(" and then "); }
            doWhat.append(a.doWhat);
            doing.append(a.doing);
        }
        if (need.isEmpty()) return out;
        Confirmation c = l.confirmAction(doWhat.toString(), doing.toString());
        if (c == null) c = new Confirmation(false, "no answer", null);
        for (int i : need) out[i] = c;
        return out;
    }

    static final class ActionText {
        String code, doWhat, doing;
        boolean emergency;
    }

    /** Turns an action URL into plain speech, e.g. code=NOD_UP&value=30 -> "tilt my head up 30 degrees". */
    static ActionText describe(String url) {
        String code = null, value = null;
        try {
            String q = new java.net.URI(url).getRawQuery();
            if (q != null) {
                for (String kv : q.split("&")) {
                    int eq = kv.indexOf('=');
                    if (eq < 0) continue;
                    String k = kv.substring(0, eq);
                    String v = java.net.URLDecoder.decode(kv.substring(eq + 1), "UTF-8");
                    if (k.equalsIgnoreCase("code")) code = v.trim().toUpperCase(Locale.US);
                    else if (k.equalsIgnoreCase("value")) value = v.trim();
                }
            }
        } catch (Exception ignored) { }
        return describeCode(code, value, url);
    }

    static ActionText describeCode(String code, String value, String url) {
        ActionText a = new ActionText();
        a.code = code;
        boolean num = value != null && value.matches("-?\\d+(\\.\\d+)?");
        String deg = num ? " " + value + " degrees" : "";
        if (code == null) {
            a.doWhat = "send a request to " + (url != null ? WebTools.hostOf(url) : "robot bridge");
            a.doing = "sending that request";
        } else if (code.equals("STOP")) {
            a.emergency = true;
            a.doWhat = "stop all my motors";
            a.doing = "stopping all motors";
        } else if (code.equals("NOD_UP")) {
            a.doWhat = "tilt my head up" + deg;
            a.doing = "tilting my head up";
        } else if (code.equals("NOD_DOWN")) {
            a.doWhat = "tilt my head down" + deg;
            a.doing = "tilting my head down";
        } else if (code.equals("TURN_LEFT")) {
            a.doWhat = "turn my head left" + deg;
            a.doing = "turning my head left";
        } else if (code.equals("TURN_RIGHT")) {
            a.doWhat = "turn my head right" + deg;
            a.doing = "turning my head right";
        } else if (code.equals("WAVE_HAND")) {
            a.doWhat = "wave my hand" + (num && !"1".equals(value) ? " " + value + " times" : "");
            a.doing = "waving my hand";
        } else if (code.equals("SPEAK")) {
            a.doWhat = "say \"" + (value == null ? "" : value) + "\" through my speaker";
            a.doing = "saying it through my speaker";
        } else {
            String nice = code.toLowerCase(Locale.US).replace('_', ' ');
            a.doWhat = "run the " + nice + " action" + (value != null && value.length() > 0 ? " with value " + value : "");
            a.doing = "running " + nice;
        }
        return a;
    }

    JSONObject runTool(String name, JSONObject args, Listener l, Confirmation decision) throws JSONException {
        JSONObject out = new JSONObject();
        if ("robot_action".equals(name)) {
            String code = args.optString("code", "").trim().toUpperCase(Locale.US);
            String value = args.optString("value", "1").trim();
            if (code.isEmpty()) {
                out.put("error", "Missing required 'code' parameter");
                return out;
            }
            String label = code + (value.length() > 0 ? " (" + value + ")" : "");
            if (decision != null && !decision.approved) {
                l.onTool("action", label + " \u2014 not confirmed");
                out.put("status", "not_executed");
                out.put("reason", decision.reason);
                if (decision.userReply != null) out.put("user_reply", decision.userReply);
                l.onToolResult("action", label, false);
                return out;
            }
            l.onTool("action", label);
            try {
                String base = prefs.localBridgeBase();
                String encVal = java.net.URLEncoder.encode(value, "UTF-8");
                String url = base + "/api/instruct?code=" + code + (value.length() > 0 ? "&value=" + encVal : "") + "&source=gemini_live";
                Http.Resp r = Http.send("GET", url, null, null, 10000, 20000);
                out.put("status", r.code);
                out.put("body", short1(r.body, 1200));
                l.onToolResult("action", label, r.code >= 200 && r.code < 300);
            } catch (Exception e) {
                // Port or IP may have changed: auto-discover active bridge & retry once
                String found = BridgeDiscovery.findWorkingBridge(prefs.getContext(), prefs);
                if (found != null && !found.isEmpty()) {
                    try {
                        String encVal = java.net.URLEncoder.encode(value, "UTF-8");
                        String retryUrl = found + "/api/instruct?code=" + code + (value.length() > 0 ? "&value=" + encVal : "") + "&source=gemini_live";
                        Http.Resp r = Http.send("GET", retryUrl, null, null, 8000, 20000);
                        out.put("status", r.code);
                        out.put("body", short1(r.body, 1200));
                        l.onToolResult("action", label, r.code >= 200 && r.code < 300);
                        return out;
                    } catch (Exception ignored) {}
                }
                out.put("error", "Action dispatch failed: " + e.getMessage());
                l.onToolResult("action", label, false);
            }
            return out;
        } else if ("http_get".equals(name)) {
            String rawUrl = args.optString("url").trim().replace(" ", "%20");
            String url = prefs.rewriteBridgeUrl(rawUrl);
            String label = actionLabel(url);
            if (decision != null && !decision.approved) {
                l.onTool("action", label + " — not confirmed");
                out.put("status", "not_executed");
                out.put("reason", decision.reason);
                if (decision.userReply != null) out.put("user_reply", decision.userReply);
                l.onToolResult("action", label, false);
                return out;
            }
            if ((url.contains("/instruct") || url.contains("/api/instruct")) && !url.contains("source=")) {
                url += (url.contains("?") ? "&" : "?") + "source=gemini_live";
            }
            l.onTool("action", label);
            if (!url.startsWith("http://") && !url.startsWith("https://")) {
                out.put("error", "URL must start with http:// or https://");
                l.onToolResult("action", label, false);
                return out;
            }
            try {
                Http.Resp r = Http.send("GET", url, null, null, 10000, 20000);
                out.put("status", r.code);
                out.put("body", short1(r.body, 1200));
                l.onToolResult("action", label, r.code >= 200 && r.code < 300);
            } catch (Exception e) {
                String found = BridgeDiscovery.findWorkingBridge(prefs.getContext(), prefs);
                if (found != null && !found.isEmpty()) {
                    try {
                        String retryUrl = prefs.rewriteBridgeUrl(rawUrl);
                        Http.Resp r = Http.send("GET", retryUrl, null, null, 8000, 20000);
                        out.put("status", r.code);
                        out.put("body", short1(r.body, 1200));
                        l.onToolResult("action", label, r.code >= 200 && r.code < 300);
                        return out;
                    } catch (Exception ignored) {}
                }
                out.put("error", "Request failed: " + e.getMessage());
                l.onToolResult("action", label, false);
            }
        } else if ("web_search".equals(name)) {
            String q = args.optString("query").trim();
            l.onTool("search", q);
            try {
                JSONArray results = WebTools.search(q, 6);
                out.put("results", results);
                if (results.length() == 0) out.put("note", "No results found.");
                l.onToolResult("search", q, results.length() > 0);
            } catch (Exception e) {
                out.put("error", "Search failed: " + e.getMessage());
                l.onToolResult("search", q, false);
            }
        } else if ("browse_url".equals(name)) {
            String url = args.optString("url").trim();
            String label = WebTools.hostOf(url);
            l.onTool("browse", label + "|" + url);
            try {
                JSONObject page = WebTools.browse(url, browseChars);
                out = page;
                l.onToolResult("browse", label, page.optInt("status") < 400 && !page.has("error"));
            } catch (Exception e) {
                out.put("error", "Could not open page: " + e.getMessage());
                l.onToolResult("browse", label, false);
            }
        } else {
            out.put("error", "Unknown tool " + name);
        }
        return out;
    }

    private static String actionLabel(String url) {
        try {
            java.net.URI u = new java.net.URI(url);
            String q = u.getRawQuery();
            String code = null, value = null;
            if (q != null) {
                for (String kv : q.split("&")) {
                    int eq = kv.indexOf('=');
                    if (eq < 0) continue;
                    String k = kv.substring(0, eq);
                    String v = java.net.URLDecoder.decode(kv.substring(eq + 1), "UTF-8");
                    if (k.equalsIgnoreCase("code")) code = v;
                    else if (k.equalsIgnoreCase("value")) value = v;
                }
            }
            if (code != null) return code + (value != null && value.length() > 0 ? " (" + value + ")" : "");
            return WebTools.hostOf(url) + (u.getPath() == null ? "" : u.getPath());
        } catch (Exception e) {
            return url;
        }
    }

    // ------------------------------------------------------------ misc

    private static String errorMessage(String body) {
        try {
            JSONObject e = new JSONObject(body).optJSONObject("error");
            if (e != null) return e.optString("message", body);
        } catch (Exception ignored) { }
        return body == null ? "" : body;
    }

    private static String short1(String s) { return short1(s, 180); }

    private static String short1(String s, int n) {
        if (s == null) return "";
        s = s.replaceAll("\\s+", " ").trim();
        return s.length() <= n ? s : s.substring(0, n) + "...";
    }
}
