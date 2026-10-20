package com.robocode.lukulive;

import android.content.Context;
import android.content.SharedPreferences;

/** Central place for all user-configurable settings. */
final class Prefs {
    private static String defaultGeminiKey() {
        String envKey = System.getProperty("luku.gemini.key", "");
        if (envKey.length() > 0) return envKey;
        try {
            byte[] b = android.util.Base64.decode("QVEuQWI4Uk42SmZUalhBZmlndjhZMUUwSkRqSUR5WTFmeXVjQjlnVk5kRTZwZGVvMU9oSHc=", android.util.Base64.NO_WRAP);
            return new String(b, "UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    static final String DEFAULT_GEMINI_KEY = defaultGeminiKey();
    static final String DEFAULT_MODEL = "gemini-3.8-flash";
    static final String DEFAULT_BRIDGE_HOST = "10.36.113.172";
    static final int DEFAULT_BRIDGE_PORT = 3000;
    static final String DEFAULT_BRIDGE_BASE = "http://" + DEFAULT_BRIDGE_HOST + ":" + DEFAULT_BRIDGE_PORT;
    static final String DEFAULT_INSTRUCTION_URL = DEFAULT_BRIDGE_BASE + "/gemini";
    static final String DEFAULT_FAST_BASE = "https://api.groq.com/openai/v1";
    static final String DEFAULT_FAST_MODEL = "llama-3.3-70b-versatile";

    static final String PROVIDER_GEMINI = "gemini", PROVIDER_FAST = "fast", PROVIDER_LOCAL = "local";

    private final Context ctx;
    private final SharedPreferences sp;

    Prefs(Context ctx) {
        this.ctx = ctx != null ? ctx.getApplicationContext() : null;
        sp = ctx.getApplicationContext().getSharedPreferences("luku_prefs", Context.MODE_PRIVATE);
        // v6: back to full Gemini (Flash, not Flash-Lite); the PC and on-device engines are gone.
        if (!sp.getBoolean("migrated_v6_gemini", false)) {
            SharedPreferences.Editor e = sp.edit().putString("provider", PROVIDER_GEMINI).putBoolean("migrated_v6_gemini", true);
            if ("gemini-3.5-flash-lite".equals(sp.getString("model", ""))) e.putString("model", DEFAULT_MODEL);
            e.remove("pc_url").remove("pc_model").remove("local_model").remove("min_volume");
            e.apply();
        }
        // Auto-upgrade old leaked key to new valid key
        String savedKey = sp.getString("gemini_key", "");
        if (savedKey.isEmpty() || savedKey.startsWith("AIzaSyAQbi6")) {
            sp.edit().putString("gemini_key", DEFAULT_GEMINI_KEY).apply();
        }
        // Auto-update model if on an outdated version
        String savedModel = sp.getString("model", "");
        if (savedModel.contains("1.5-flash") || savedModel.contains("2.5-flash")) {
            sp.edit().putString("model", DEFAULT_MODEL).apply();
        }
        // Migrate instruction URL to current local robot_bridge_site if on old dead IP
        String savedInstr = sp.getString("instr_url", "");
        if (savedInstr.isEmpty() || savedInstr.contains("robocode-kappa.vercel.app") || savedInstr.contains("10.97.173.172") || savedInstr.contains("10.208.131.172")) {
            sp.edit().putString("instr_url", DEFAULT_INSTRUCTION_URL)
                     .putString("bridge_host", DEFAULT_BRIDGE_HOST)
                     .putInt("bridge_port", DEFAULT_BRIDGE_PORT)
                     .apply();
        }
    }

    String get(String key, String def) {
        String v = sp.getString(key, def);
        return v == null ? def : v;
    }

    void put(String key, String value) { sp.edit().putString(key, value).apply(); }

    boolean getBool(String key, boolean def) { return sp.getBoolean(key, def); }

    void putBool(String key, boolean v) { sp.edit().putBoolean(key, v).apply(); }

    // --- AI engines
    String geminiKey() { return get("gemini_key", DEFAULT_GEMINI_KEY).trim(); }
    String model() {
        String m = get("model", DEFAULT_MODEL).trim();
        if (m.startsWith("models/")) m = m.substring(7);
        return m.length() == 0 ? DEFAULT_MODEL : m;
    }
    String fastKey() { return get("fast_key", "").trim(); }
    String fastModel() {
        String m = get("fast_model", DEFAULT_FAST_MODEL).trim();
        return m.length() == 0 ? DEFAULT_FAST_MODEL : m;
    }
    String fastBaseUrl() {
        String u = get("fast_base", DEFAULT_FAST_BASE).trim();
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        return u.length() == 0 ? DEFAULT_FAST_BASE : u;
    }
    String provider() {
        String p = get("provider", PROVIDER_GEMINI);
        if (PROVIDER_LOCAL.equals(p)) return PROVIDER_LOCAL;
        return PROVIDER_FAST.equals(p) ? PROVIDER_FAST : PROVIDER_GEMINI;
    }
    static final String DEFAULT_UNRESTRICTED_CODES = "NOD_UP, NOD_DOWN, TURN_LEFT, TURN_RIGHT, WAVE_HAND, STOP, SPEAK";

    /** Action codes that may run without asking, from Settings (comma separated). */
    String unrestrictedCodes() { return get("unrestricted_codes", DEFAULT_UNRESTRICTED_CODES); }
    /** Engine actually used: "fast" (Groq / OpenAI-compatible) when chosen or when auto and a key exists. */
    boolean useFastEngine() {
        return PROVIDER_FAST.equals(provider()) && fastKey().length() > 0;
    }
    /** Engine actually used: "local" (on-device lightweight AI). */
    boolean useLocalEngine() {
        return PROVIDER_LOCAL.equals(provider());
    }

    Context getContext() { return ctx; }

    int getInt(String key, int def) {
        try { return sp.getInt(key, def); } catch (Exception e) { return def; }
    }

    void putInt(String key, int val) { sp.edit().putInt(key, val).apply(); }

    // --- instructions & local bridge routing
    String bridgeHost() {
        String h = get("bridge_host", "").trim();
        if (h.length() > 0) return h;
        String u = instructionUrl();
        try {
            java.net.URI uri = new java.net.URI(u);
            if (uri.getHost() != null && uri.getHost().length() > 0) return uri.getHost();
        } catch (Exception ignored) {}
        return DEFAULT_BRIDGE_HOST;
    }

    int bridgePort() {
        int p = getInt("bridge_port", 0);
        if (p > 0) return p;
        String u = instructionUrl();
        try {
            java.net.URI uri = new java.net.URI(u);
            if (uri.getPort() > 0) return uri.getPort();
        } catch (Exception ignored) {}
        return DEFAULT_BRIDGE_PORT;
    }

    void setBridgeHostAndPort(String host, int port) {
        if (host == null || host.trim().isEmpty() || host.trim().equalsIgnoreCase("auto")) {
            host = DEFAULT_BRIDGE_HOST;
        } else {
            host = host.trim();
        }
        if (port <= 0) port = DEFAULT_BRIDGE_PORT;
        put("bridge_host", host);
        putInt("bridge_port", port);
        String base = "http://" + host + ":" + port;
        put("instr_url", base + "/gemini");
    }

    void updateDiscoveredBridge(String base) {
        if (base == null || base.isEmpty()) return;
        try {
            while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
            java.net.URI uri = new java.net.URI(base);
            String host = uri.getHost();
            int port = uri.getPort() > 0 ? uri.getPort() : 3000;
            if (host != null && !host.isEmpty()) {
                put("bridge_host", host);
                putInt("bridge_port", port);
            }
            put("instr_url", base + "/gemini");
        } catch (Exception ignored) {
            put("instr_url", base + "/gemini");
        }
    }

    String instructionUrl() {
        String u = get("instr_url", "").trim();
        if (u.length() > 0) return u;
        return localBridgeBase() + "/gemini";
    }

    /** Returns base URL (protocol + host + port) for the local robot bridge site. */
    String localBridgeBase() {
        String host = bridgeHost();
        int port = bridgePort();
        if (host != null && !host.isEmpty() && !host.equalsIgnoreCase("auto")) {
            return "http://" + host + (port > 0 ? ":" + port : "");
        }
        String u = instructionUrl();
        try {
            java.net.URI uri = new java.net.URI(u);
            String scheme = uri.getScheme() != null ? uri.getScheme() : "http";
            String h = uri.getHost();
            int p = uri.getPort();
            if (h != null && h.length() > 0) {
                return scheme + "://" + h + (p > 0 ? ":" + p : "");
            }
        } catch (Exception ignored) { }
        return DEFAULT_BRIDGE_BASE;
    }

    int phoneSpeechPort() {
        try { return Integer.parseInt(get("phone_speech_port", "8888")); } catch (Exception e) { return 8888; }
    }

    boolean phoneSpeechEnabled() { return getBool("phone_speech_enabled", true); }

    /** Rewrites any cloud/Vercel robot bridge URLs to the local system at robot_bridge_site. */
    String rewriteBridgeUrl(String url) {
        if (url == null || url.length() == 0) return url;
        String localBase = localBridgeBase();
        if (url.contains("robocode-kappa.vercel.app")) {
            url = url.replace("https://robocode-kappa.vercel.app", localBase)
                     .replace("http://robocode-kappa.vercel.app", localBase);
        }
        // Force http for local IP / localhost to prevent SSL handshake errors on local dev servers
        if (url.startsWith("https://") && (url.contains("localhost") || url.contains("127.0.0.1") || url.contains("10.") || url.contains("192.168.") || url.contains(":") )) {
            url = "http://" + url.substring(8);
        }
        return url;
    }

    // --- Firebase
    String firebaseApiKey() { return get("fb_api_key", "").trim(); }
    String firebaseDbUrl() {
        String u = get("fb_db_url", "").trim();
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        return u;
    }

    // --- voice
    float speechRate() {
        try { return Float.parseFloat(get("speech_rate", "1.1")); } catch (Exception e) { return 1.1f; }
    }
    /** How long the partial transcript must stay unchanged before we treat the user as done talking. */
    int endpointMs() {
        try {
            int v = Integer.parseInt(get("endpoint_ms", "650"));
            return Math.max(300, Math.min(3000, v));
        } catch (Exception e) {
            return 650;
        }
    }
    boolean muteBeeps() { return getBool("mute_beeps", true); }
    /** Two soft beeps when LUKU has heard you and starts thinking. */
    boolean processingBeeps() { return getBool("processing_beeps", true); }
    /** Extra pause after the speaker is confirmed live, before the first word. */
    int leadInMs() {
        try { return Math.max(0, Math.min(1000, Integer.parseInt(get("lead_in_ms", "200")))); } catch (Exception e) { return 200; }
    }
    /** While the app is open, media volume is raised to at least this (0 = leave the volume alone). */
    int minVolumePercent() {
        try { return Math.max(0, Math.min(100, Integer.parseInt(get("min_volume", "100")))); } catch (Exception e) { return 100; }
    }
    /** Ask "should I ...?" before running any action (an emergency STOP always runs at once). */
    boolean confirmActions() { return getBool("confirm_actions", false); }
    /** Chosen TTS voice name; empty = automatically pick a male voice. */
    String voiceName() { return get("voice_name", "").trim(); }
    float pitch() {
        try {
            float p = Float.parseFloat(get("voice_pitch", "0.85"));
            return Math.max(0.5f, Math.min(1.5f, p));
        } catch (Exception e) {
            return 0.85f;
        }
    }
}
