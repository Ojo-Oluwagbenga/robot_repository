package com.robocode.lukulive;

import android.speech.tts.TextToSpeech;
import android.speech.tts.Voice;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Finds and applies a male (masculine) text-to-speech voice. */
final class Voices {
    private Voices() {}

    /** Google Speech Services voice ids known to be male, e.g. "en-us-x-iom-local". */
    private static final String[] GOOGLE_MALE = {
            "iol", "iom", "tpd", "gbb", "gbd", "rjs", "aub", "aud", "end"
    };
    private static final String[] GOOGLE_FEMALE = {
            "sfg", "iob", "iog", "tpc", "tpf", "gba", "gbc", "gbg", "aua", "auc", "ahp", "cxx", "ene"
    };

    static boolean isMale(Voice v) {
        String n = v.getName().toLowerCase(Locale.US);
        if (n.contains("female")) return false;
        if (n.contains("male")) return true;
        if (n.contains("smtm")) return true;   // Samsung male voices
        if (n.contains("smtf")) return false;  // Samsung female voices
        Set<String> f = v.getFeatures();
        if (f != null) {
            for (String s : f) {
                String x = s.toLowerCase(Locale.US);
                if (x.contains("female")) return false;
                if (x.equals("male") || x.contains("gender=male")) return true;
            }
        }
        for (String id : GOOGLE_FEMALE) if (n.contains("-x-" + id + "-") || n.endsWith("-x-" + id)) return false;
        for (String id : GOOGLE_MALE) if (n.contains("-x-" + id + "-") || n.endsWith("-x-" + id)) return true;
        return false;
    }

    static boolean usable(Voice v) {
        Set<String> f = v.getFeatures();
        return f == null || !f.contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED);
    }

    /** English voices, male first, then offline before online, device country first. */
    static List<Voice> englishVoices(TextToSpeech tts) {
        List<Voice> out = new ArrayList<Voice>();
        Set<Voice> all;
        try { all = tts.getVoices(); } catch (Exception e) { return out; }
        if (all == null) return out;
        for (Voice v : all) {
            if (v.getLocale() != null && "en".equals(v.getLocale().getLanguage()) && usable(v)) out.add(v);
        }
        final String country = Locale.getDefault().getCountry();
        Collections.sort(out, new Comparator<Voice>() {
            @Override public int compare(Voice a, Voice b) {
                int s = score(b, country) - score(a, country);
                return s != 0 ? s : a.getName().compareTo(b.getName());
            }
        });
        return out;
    }

    private static int score(Voice v, String country) {
        int s = 0;
        if (isMale(v)) s += 1000;
        if (!v.isNetworkConnectionRequired()) s += 200;          // offline voices start speaking faster
        String c = v.getLocale().getCountry();
        if (c.equalsIgnoreCase(country)) s += 100;
        else if (c.equalsIgnoreCase("US")) s += 60;
        else if (c.equalsIgnoreCase("GB")) s += 50;
        s += Math.max(0, v.getQuality() / 100);
        return s;
    }

    /** Applies the chosen voice (or the best male one) plus pitch. Returns a short description. */
    static String apply(TextToSpeech tts, Prefs prefs) {
        tts.setSpeechRate(prefs.speechRate());
        tts.setPitch(prefs.pitch());
        List<Voice> voices = englishVoices(tts);
        Voice chosen = null;
        String wanted = prefs.voiceName();
        if (wanted.length() > 0) {
            for (Voice v : voices) if (v.getName().equals(wanted)) { chosen = v; break; }
        }
        if (chosen == null) {
            for (Voice v : voices) if (isMale(v)) { chosen = v; break; }
        }
        if (chosen == null) {
            // No male voice installed: keep the default voice but deepen it a little more.
            tts.setPitch(Math.min(prefs.pitch(), 0.75f));
            return "default voice (deepened) — install a male voice in Speech Services for better results";
        }
        try { tts.setVoice(chosen); } catch (Exception ignored) { }
        return label(chosen);
    }

    static String label(Voice v) {
        Locale l = v.getLocale();
        return (isMale(v) ? "♂ " : "") + l.getDisplayCountry(Locale.US) + " · " + v.getName()
                + (v.isNetworkConnectionRequired() ? " (online)" : "");
    }
}
