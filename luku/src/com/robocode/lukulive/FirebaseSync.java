package com.robocode.lukulive;

import android.content.Context;
import android.os.Build;
import android.provider.Settings;

import org.json.JSONObject;

import java.net.URLEncoder;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Firebase backend integration over the official REST APIs:
 *  - Firebase Authentication (anonymous sign-in via Identity Toolkit, token refresh via Secure Token)
 *  - Firebase Realtime Database (conversation transcripts, tool/robot action log, session metadata)
 * Data layout:  /luku/users/{uid}/sessions/{sessionId}/meta  and  .../messages/{pushId}
 */
final class FirebaseSync {

    interface StatusListener { void onFirebaseStatus(String status, boolean ok); }

    private final Context ctx;
    private final Prefs prefs;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Set<String> sessionsWithMeta = new HashSet<String>();
    private volatile StatusListener listener;

    FirebaseSync(Context ctx, Prefs prefs) {
        this.ctx = ctx.getApplicationContext();
        this.prefs = prefs;
    }

    void setListener(StatusListener l) { listener = l; }

    boolean isConfigured() { return prefs.firebaseDbUrl().length() > 0; }

    /** Queue a transcript entry. role = user | assistant | tool | error. */
    void log(final String sessionId, final String role, final String text, final String model) {
        if (!isConfigured()) return;
        executor.execute(new Runnable() {
            @Override public void run() {
                try {
                    writeEntry(sessionId, role, text, model);
                    notifyStatus("Firebase synced", true);
                } catch (Exception e) {
                    notifyStatus("Firebase: " + e.getMessage(), false);
                }
            }
        });
    }

    /** Blocking connectivity test used by the Settings screen. */
    String test() {
        if (!isConfigured()) return "Enter your Realtime Database URL first.";
        try {
            String auth = authToken();
            String path = base() + "/luku/users/" + enc(uid()) + "/lastTest.json" + authQuery(auth);
            JSONObject body = new JSONObject().put("ts", new JSONObject().put(".sv", "timestamp")).put("device", Build.MODEL);
            Http.Resp r = send("PUT", path, body.toString());
            if (r.code == 200) {
                return "Connected. Writing to /luku/users/" + uid() + (auth == null ? " (no auth)" : " (signed in anonymously)");
            }
            return "Database returned HTTP " + r.code + ": " + trim(r.body);
        } catch (Exception e) {
            return "Failed: " + e.getMessage();
        }
    }

    // ------------------------------------------------------------------ internals

    private void writeEntry(String sessionId, String role, String text, String model) throws Exception {
        String auth = authToken();
        String sessionPath = base() + "/luku/users/" + enc(uid()) + "/sessions/" + enc(sessionId);
        if (!sessionsWithMeta.contains(sessionId)) {
            JSONObject meta = new JSONObject()
                    .put("startedAt", new JSONObject().put(".sv", "timestamp"))
                    .put("device", Build.MANUFACTURER + " " + Build.MODEL)
                    .put("model", model)
                    .put("instructionUrl", prefs.instructionUrl());
            Http.Resp m = send("PATCH", sessionPath + "/meta.json" + authQuery(auth), meta.toString());
            check(m);
            sessionsWithMeta.add(sessionId);
        }
        JSONObject entry = new JSONObject()
                .put("role", role)
                .put("text", text)
                .put("ts", new JSONObject().put(".sv", "timestamp"));
        Http.Resp r = send("POST", sessionPath + "/messages.json" + authQuery(auth), entry.toString());
        check(r);
        JSONObject touch = new JSONObject().put("updatedAt", new JSONObject().put(".sv", "timestamp"))
                .put("lastMessage", text.length() > 140 ? text.substring(0, 140) : text);
        send("PATCH", sessionPath + "/meta.json" + authQuery(auth), touch.toString());
    }

    private void check(Http.Resp r) throws Exception {
        if (r.code == 401 || r.code == 403) {
            prefs.put("fb_id_token", "");
            throw new Exception("permission denied - check database rules / Anonymous sign-in");
        }
        if (r.code != 200) throw new Exception("HTTP " + r.code + " " + trim(r.body));
    }

    private Http.Resp send(String method, String url, String json) throws Exception {
        Map<String, String> h = new HashMap<String, String>();
        h.put("Content-Type", "application/json; charset=utf-8");
        String m = method;
        if ("PATCH".equals(method)) {
            // HttpURLConnection does not support PATCH; the Firebase REST API accepts an override header.
            m = "POST";
            h.put("X-HTTP-Method-Override", "PATCH");
        }
        return Http.send(m, url, h, json.getBytes("UTF-8"), 15000, 200000);
    }

    private String base() {
        String u = prefs.firebaseDbUrl();
        if (!u.startsWith("http")) u = "https://" + u;
        return u;
    }

    private String uid() {
        String id = prefs.get("fb_uid", "");
        if (id.length() > 0) return id;
        String android = Settings.Secure.getString(ctx.getContentResolver(), Settings.Secure.ANDROID_ID);
        return "device-" + (android == null ? "unknown" : android);
    }

    private static String authQuery(String token) {
        return token == null ? "" : "?auth=" + token;
    }

    /** Returns a valid Firebase ID token (anonymous user) or null when no API key is configured. */
    private synchronized String authToken() throws Exception {
        String apiKey = prefs.firebaseApiKey();
        if (apiKey.length() == 0) return null;
        String token = prefs.get("fb_id_token", "");
        long exp = 0;
        try { exp = Long.parseLong(prefs.get("fb_token_exp", "0")); } catch (Exception ignored) { }
        if (token.length() > 0 && System.currentTimeMillis() < exp - 120000) return token;

        String refresh = prefs.get("fb_refresh", "");
        if (refresh.length() > 0 && prefs.get("fb_key_used", "").equals(apiKey)) {
            Map<String, String> h = new HashMap<String, String>();
            h.put("Content-Type", "application/x-www-form-urlencoded");
            String form = "grant_type=refresh_token&refresh_token=" + URLEncoder.encode(refresh, "UTF-8");
            Http.Resp r = Http.send("POST", "https://securetoken.googleapis.com/v1/token?key=" + apiKey,
                    h, form.getBytes("UTF-8"), 15000, 100000);
            if (r.code == 200) {
                JSONObject o = new JSONObject(r.body);
                saveToken(o.optString("id_token"), o.optString("refresh_token"), o.optString("user_id"),
                        o.optString("expires_in", "3600"), apiKey);
                return o.optString("id_token");
            }
        }
        Map<String, String> h = new HashMap<String, String>();
        h.put("Content-Type", "application/json");
        Http.Resp r = Http.send("POST", "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=" + apiKey,
                h, "{\"returnSecureToken\":true}".getBytes("UTF-8"), 15000, 100000);
        if (r.code != 200) {
            String msg = r.body;
            try { msg = new JSONObject(r.body).getJSONObject("error").optString("message", msg); } catch (Exception ignored) { }
            throw new Exception("sign-in failed (" + trim(msg) + "). Enable Anonymous sign-in in Firebase Authentication.");
        }
        JSONObject o = new JSONObject(r.body);
        saveToken(o.optString("idToken"), o.optString("refreshToken"), o.optString("localId"),
                o.optString("expiresIn", "3600"), apiKey);
        return o.optString("idToken");
    }

    private void saveToken(String id, String refresh, String uid, String expiresIn, String apiKey) {
        long secs = 3600;
        try { secs = Long.parseLong(expiresIn); } catch (Exception ignored) { }
        prefs.put("fb_id_token", id);
        prefs.put("fb_refresh", refresh);
        if (uid != null && uid.length() > 0) prefs.put("fb_uid", uid);
        prefs.put("fb_token_exp", String.valueOf(System.currentTimeMillis() + secs * 1000));
        prefs.put("fb_key_used", apiKey);
    }

    private void notifyStatus(String s, boolean ok) {
        StatusListener l = listener;
        if (l != null) l.onFirebaseStatus(s, ok);
    }

    private static String enc(String s) {
        try { return URLEncoder.encode(s, "UTF-8"); } catch (Exception e) { return s; }
    }

    private static String trim(String s) {
        if (s == null) return "";
        s = s.replaceAll("\\s+", " ").trim();
        return s.length() > 160 ? s.substring(0, 160) + "..." : s;
    }
}
