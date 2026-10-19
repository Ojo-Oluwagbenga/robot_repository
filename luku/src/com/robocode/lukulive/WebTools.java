package com.robocode.lukulive;

import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Web search + page reading used by the assistant while talking. */
final class WebTools {
    private WebTools() {}

    private static final int FLAGS = Pattern.CASE_INSENSITIVE | Pattern.DOTALL;
    private static final Pattern ANCHOR = Pattern.compile("<a\\b([^>]*)>(.*?)</a>", FLAGS);
    private static final Pattern HREF = Pattern.compile("href\\s*=\\s*\"([^\"]*)\"", FLAGS);
    private static final Pattern TITLE = Pattern.compile("<title[^>]*>(.*?)</title>", FLAGS);
    private static final Pattern META_DESC = Pattern.compile(
            "<meta[^>]+(?:name|property)\\s*=\\s*\"(?:description|og:description)\"[^>]*content\\s*=\\s*\"([^\"]*)\"", FLAGS);

    // ---------------------------------------------------------------- search

    static JSONArray search(String query, int max) throws Exception {
        JSONArray res = new JSONArray();
        try { res = duckDuckGo(query, max); } catch (Exception ignored) { }
        if (res.length() == 0) {
            try { res = bing(query, max); } catch (Exception ignored) { }
        }
        if (res.length() == 0) {
            res = wikipedia(query, max);
        }
        return res;
    }

    private static JSONArray duckDuckGo(String query, int max) throws Exception {
        byte[] body = ("q=" + URLEncoder.encode(query, "UTF-8") + "&kl=wt-wt").getBytes("UTF-8");
        Map<String, String> h = new HashMap<String, String>();
        h.put("Content-Type", "application/x-www-form-urlencoded");
        h.put("Accept", "text/html");
        Http.Resp r = Http.send("POST", "https://html.duckduckgo.com/html/", h, body, 12000, 900000);
        JSONArray out = new JSONArray();
        if (r.code != 200) return out;
        Matcher m = ANCHOR.matcher(r.body);
        JSONObject cur = null;
        Set<String> seen = new HashSet<String>();
        while (m.find()) {
            String attrs = m.group(1);
            if (attrs.contains("result__a")) {
                String href = attr(attrs);
                if (href == null || href.contains("duckduckgo.com/y.js")) { cur = null; continue; }
                String url = decodeDdg(href);
                if (url == null || !seen.add(url)) { cur = null; continue; }
                if (out.length() >= max) break;
                cur = new JSONObject();
                cur.put("title", clean(m.group(2)));
                cur.put("url", url);
                cur.put("snippet", "");
                out.put(cur);
            } else if (attrs.contains("result__snippet") && cur != null) {
                cur.put("snippet", clean(m.group(2)));
            }
        }
        return out;
    }

    private static String decodeDdg(String href) throws Exception {
        href = decodeEntities(href);
        if (href.startsWith("//")) href = "https:" + href;
        int i = href.indexOf("uddg=");
        if (i >= 0) {
            String v = href.substring(i + 5);
            int amp = v.indexOf('&');
            if (amp >= 0) v = v.substring(0, amp);
            return URLDecoder.decode(v, "UTF-8");
        }
        return href.startsWith("http") ? href : null;
    }

    private static JSONArray bing(String query, int max) throws Exception {
        String url = "https://www.bing.com/search?setlang=en&q=" + URLEncoder.encode(query, "UTF-8");
        Map<String, String> h = new HashMap<String, String>();
        h.put("Accept", "text/html");
        Http.Resp r = Http.send("GET", url, h, null, 12000, 1500000);
        JSONArray out = new JSONArray();
        if (r.code != 200) return out;
        String[] blocks = r.body.split("<li class=\"b_algo\"");
        Pattern h2 = Pattern.compile("<h2[^>]*>\\s*<a\\b([^>]*)>(.*?)</a>", FLAGS);
        Pattern p = Pattern.compile("<p\\b[^>]*>(.*?)</p>", FLAGS);
        for (int i = 1; i < blocks.length && out.length() < max; i++) {
            Matcher m = h2.matcher(blocks[i]);
            if (!m.find()) continue;
            String href = attr(m.group(1));
            if (href == null) continue;
            href = decodeBing(decodeEntities(href));
            if (href == null) continue;
            JSONObject o = new JSONObject();
            o.put("title", clean(m.group(2)));
            o.put("url", href);
            Matcher pm = p.matcher(blocks[i]);
            o.put("snippet", pm.find() ? clean(pm.group(1)) : "");
            out.put(o);
        }
        return out;
    }

    private static String decodeBing(String href) {
        if (!href.contains("bing.com/ck/a")) return href.startsWith("http") ? href : null;
        int i = href.indexOf("&u=");
        if (i < 0) i = href.indexOf("?u=");
        if (i < 0) return null;
        String v = href.substring(i + 3);
        int amp = v.indexOf('&');
        if (amp >= 0) v = v.substring(0, amp);
        if (v.startsWith("a1")) v = v.substring(2);
        try {
            return new String(Base64.decode(v, Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP), "UTF-8");
        } catch (Exception e) {
            return null;
        }
    }

    private static JSONArray wikipedia(String query, int max) throws Exception {
        String url = "https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit="
                + max + "&srsearch=" + URLEncoder.encode(query, "UTF-8");
        Http.Resp r = Http.send("GET", url, null, null, 12000, 400000);
        JSONArray out = new JSONArray();
        if (r.code != 200) return out;
        JSONArray arr = new JSONObject(r.body).optJSONObject("query").optJSONArray("search");
        for (int i = 0; arr != null && i < arr.length(); i++) {
            JSONObject s = arr.getJSONObject(i);
            JSONObject o = new JSONObject();
            String title = s.optString("title");
            o.put("title", title + " - Wikipedia");
            o.put("url", "https://en.wikipedia.org/wiki/" + URLEncoder.encode(title.replace(' ', '_'), "UTF-8"));
            o.put("snippet", clean(s.optString("snippet")));
            out.put(o);
        }
        return out;
    }

    // ---------------------------------------------------------------- browse

    static JSONObject browse(String url, int maxChars) throws Exception {
        url = url.trim();
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        Map<String, String> h = new HashMap<String, String>();
        h.put("Accept", "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.5");
        Http.Resp r = Http.send("GET", url, h, null, 15000, 1800000);
        JSONObject o = new JSONObject();
        o.put("url", r.finalUrl);
        o.put("status", r.code);
        String ct = r.contentType == null ? "" : r.contentType.toLowerCase();
        String text;
        boolean looksHtml = ct.contains("html") || r.body.trim().startsWith("<");
        if (ct.contains("json") || (ct.startsWith("text/") && !looksHtml)) {
            text = r.body;
        } else if (looksHtml) {
            Matcher t = TITLE.matcher(r.body);
            if (t.find()) o.put("title", clean(t.group(1)));
            Matcher d = META_DESC.matcher(r.body);
            if (d.find()) o.put("description", decodeEntities(d.group(1)).trim());
            text = htmlToText(r.body);
        } else {
            o.put("error", "Unsupported content type: " + ct);
            return o;
        }
        if (text.length() > maxChars) {
            text = text.substring(0, maxChars) + " ...[truncated]";
        }
        o.put("text", text);
        return o;
    }

    static String htmlToText(String html) {
        String s = html;
        s = s.replaceAll("(?is)<!--.*?-->", " ");
        s = s.replaceAll("(?is)<(script|style|noscript|svg|template|iframe|head|nav|footer|form)\\b[^>]*>.*?</\\1\\s*>", " ");
        s = s.replaceAll("(?i)<(br|hr)\\b[^>]*>", "\n");
        s = s.replaceAll("(?i)</(p|div|li|h[1-6]|tr|section|article|header|blockquote|pre|table|ul|ol|dd|dt)\\s*>", "\n");
        s = s.replaceAll("(?i)<(h[1-6]|li)\\b[^>]*>", "\n");
        s = s.replaceAll("(?s)<[^>]*>", " ");
        s = decodeEntities(s);
        StringBuilder out = new StringBuilder();
        String last = "";
        for (String line : s.split("\n")) {
            String t = line.replaceAll("[ \\t\\x0B\\f\\r\\u00A0]+", " ").trim();
            if (t.length() < 2 || t.equals(last)) continue;
            out.append(t).append('\n');
            last = t;
        }
        return out.toString().trim();
    }

    // ---------------------------------------------------------------- helpers

    static String hostOf(String url) {
        try {
            String h = new URI(url.trim()).getHost();
            if (h == null) return url;
            return h.startsWith("www.") ? h.substring(4) : h;
        } catch (Exception e) {
            return url;
        }
    }

    private static String attr(String attrs) {
        Matcher m = HREF.matcher(attrs);
        return m.find() ? m.group(1) : null;
    }

    private static String clean(String html) {
        return decodeEntities(html.replaceAll("(?s)<[^>]*>", "")).replaceAll("\\s+", " ").trim();
    }

    static String decodeEntities(String s) {
        if (s.indexOf('&') < 0) return s;
        StringBuilder sb = new StringBuilder(s.length());
        int i = 0;
        while (i < s.length()) {
            char c = s.charAt(i);
            if (c == '&') {
                int semi = s.indexOf(';', i);
                if (semi > i && semi - i <= 10) {
                    String ent = s.substring(i + 1, semi);
                    String rep = entity(ent);
                    if (rep != null) {
                        sb.append(rep);
                        i = semi + 1;
                        continue;
                    }
                }
            }
            sb.append(c);
            i++;
        }
        return sb.toString();
    }

    private static String entity(String e) {
        try {
            if (e.startsWith("#x") || e.startsWith("#X")) return new String(Character.toChars(Integer.parseInt(e.substring(2), 16)));
            if (e.startsWith("#")) return new String(Character.toChars(Integer.parseInt(e.substring(1))));
        } catch (Exception ex) {
            return null;
        }
        String l = e.toLowerCase();
        if (l.equals("amp")) return "&";
        if (l.equals("lt")) return "<";
        if (l.equals("gt")) return ">";
        if (l.equals("quot")) return "\"";
        if (l.equals("apos")) return "'";
        if (l.equals("nbsp")) return " ";
        if (l.equals("mdash")) return "\u2014";
        if (l.equals("ndash")) return "\u2013";
        if (l.equals("hellip")) return "\u2026";
        if (l.equals("rsquo") || l.equals("lsquo")) return "'";
        if (l.equals("ldquo") || l.equals("rdquo")) return "\"";
        if (l.equals("copy")) return "\u00A9";
        if (l.equals("reg")) return "\u00AE";
        if (l.equals("trade")) return "\u2122";
        if (l.equals("deg")) return "\u00B0";
        if (l.equals("euro")) return "\u20AC";
        if (l.equals("pound")) return "\u00A3";
        if (l.equals("middot")) return "\u00B7";
        if (l.equals("bull")) return "\u2022";
        return null;
    }
}
