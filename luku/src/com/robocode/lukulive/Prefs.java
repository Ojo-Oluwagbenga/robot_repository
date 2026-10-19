package com.robocode.lukulive;

import android.content.Context;
import android.content.SharedPreferences;

/** Central place for all user-configurable settings. */
final class Prefs {
    static final String DEFAULT_GEMINI_KEY = "AIzaSyAQbi6-wt-GsRH4RbQzSedhDF8P6tEzJGU";
    static final String DEFAULT_MODEL = "gemini-3.5-flash";
    static final String DEFAULT_BRIDGE_BASE = "http://10.97.173.172:3000";
    static final String DEFAULT_INSTRUCTION_URL = DEFAULT_BRIDGE_BASE + "/gemini";
    static final String DEFAULT_FAST_BASE = "https://api.groq.com/openai/v1";
    static final String DEFAULT_FAST_MODEL = "llama-3.3-70b-versatile";

    static final String PROVIDER_GEMINI = "gemini", PROVIDER_FAST = "fast";

    private final SharedPreferences sp;

    Prefs(Context ctx) {
        sp = ctx.getApplicationContext().getSharedPreferences("luku_prefs", Context.MODE_PRIVATE);
        // v6: back to full Gemini (Flash, not Flash-Lite); the PC and on-device engines are gone.
        if (!sp.getBoolean("migrated_v6_gemini", false)) {
            SharedPreferences.Editor e = sp.edit().putString("provider", PROVIDER_GEMINI).putBoolean("migrated_v6_gemini", true);
            if ("gemini-3.5-flash-lite".equals(sp.getString("model", ""))) e.putString("model", DEFAULT_MODEL);
            e.remove("pc_url").remove("pc_model").remove("local_model").remove("min_volume");
            e.apply();
        }
        // Migrate instruction URL from Vercel to local robot_bridge_site
        String savedInstr = sp.getString("instr_url", "");
        if (savedInstr.isEmpty() || savedInstr.contains("robocode-kappa.vercel.app")) {
            sp.edit().putString("instr_url", DEFAULT_INSTRUCTION_URL).apply();
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
        return PROVIDER_FAST.equals(p) ? PROVIDER_FAST : PROVIDER_GEMINI;
    }
    static final String DEFAULT_UNRESTRICTED_CODES = "NOD_UP, NOD_DOWN, TURN_LEFT, TURN_RIGHT, WAVE_HAND, STOP, SPEAK";

    /** Action codes that may run without asking, from Settings (comma separated). */
    String unrestrictedCodes() { return get("unrestricted_codes", DEFAULT_UNRESTRICTED_CODES); }
    /** Engine actually used: "fast" (Groq / OpenAI-compatible) when chosen or when auto and a key exists. */
    boolean useFastEngine() {
        return PROVIDER_FAST.equals(provider()) && fastKey().length() > 0;
    }

    // --- instructions & local bridge routing
    String instructionUrl() { return get("instr_url", DEFAULT_INSTRUCTION_URL).trim(); }

    /** Returns base URL (protocol + host + port) for the local robot bridge site. */
    String localBridgeBase() {
        String u = instructionUrl();
        try {
            java.net.URI uri = new java.net.URI(u);
            String scheme = uri.getScheme() != null ? uri.getScheme() : "http";
            String host = uri.getHost();
            int port = uri.getPort();
            if (host != null && host.length() > 0) {
                return scheme + "://" + host + (port > 0 ? ":" + port : "");
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
        if (url.startsWith("https://") && (url.contains("localhost") || url.contains("127.0.0.1") || url.contains("10.") || url.contains("192.168.") || url.contains(":3000"))) {
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
