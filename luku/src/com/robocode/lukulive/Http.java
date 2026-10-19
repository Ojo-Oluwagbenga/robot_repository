package com.robocode.lukulive;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.Charset;
import java.util.Map;

/**
 * Minimal HTTP client on top of HttpURLConnection (no third-party libraries).
 * Connections are deliberately NOT disconnect()ed after a complete read so the
 * platform keeps the TLS socket alive in its pool - this saves a full handshake
 * (often 300-800 ms on mobile) on every AI request.
 */
final class Http {
    static final String UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            + "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36";
    private static final Charset UTF8 = Charset.forName("UTF-8");

    static final class Resp {
        int code;
        String body;
        String contentType;
        String finalUrl;
    }

    interface LineHandler {
        void onLine(String line) throws Exception;
        boolean cancelled();
    }

    static final class CancelledException extends IOException {
        CancelledException() { super("cancelled"); }
    }

    private Http() {}

    private static HttpURLConnection open(String method, String url, Map<String, String> headers,
                                          int connectMs, int readMs) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setInstanceFollowRedirects(false);
        c.setConnectTimeout(connectMs);
        c.setReadTimeout(readMs);
        c.setRequestMethod(method);
        c.setRequestProperty("User-Agent", UA);
        c.setRequestProperty("Accept-Language", "en-US,en;q=0.9");
        if (headers != null) {
            for (Map.Entry<String, String> e : headers.entrySet()) c.setRequestProperty(e.getKey(), e.getValue());
        }
        return c;
    }

    private static void write(HttpURLConnection c, byte[] body) throws IOException {
        if (body == null) return;
        c.setDoOutput(true);
        c.setFixedLengthStreamingMode(body.length);
        OutputStream os = c.getOutputStream();
        os.write(body);
        os.close();
    }

    static Resp send(String method, String url, Map<String, String> headers, byte[] body,
                     int timeoutMs, int maxBytes) throws IOException {
        String current = url;
        for (int hop = 0; hop < 7; hop++) {
            HttpURLConnection c = open(method, current, headers, Math.min(timeoutMs, 10000), timeoutMs);
            write(c, body);
            int code = c.getResponseCode();
            if (code >= 300 && code < 400 && code != 304) {
                String loc = c.getHeaderField("Location");
                drain(c, code);
                if (loc == null) throw new IOException("Redirect without location");
                current = new URL(new URL(current), loc).toString();
                if (code == 303 || ((code == 301 || code == 302) && "POST".equals(method))) {
                    method = "GET";
                    body = null;
                }
                continue;
            }
            Resp r = new Resp();
            r.code = code;
            r.contentType = c.getContentType();
            r.finalUrl = current;
            InputStream in = stream(c, code);
            r.body = in == null ? "" : readAll(in, maxBytes, charsetOf(r.contentType), c);
            return r;
        }
        throw new IOException("Too many redirects");
    }

    /**
     * POST and feed the response line by line (Server-Sent Events). On a non-200 status the
     * error body is returned in Resp.body and no lines are delivered.
     */
    static Resp stream(String url, Map<String, String> headers, byte[] body, int connectMs, int readMs,
                       LineHandler handler) throws IOException {
        HttpURLConnection c = open("POST", url, headers, connectMs, readMs);
        write(c, body);
        int code = c.getResponseCode();
        Resp r = new Resp();
        r.code = code;
        r.contentType = c.getContentType();
        r.finalUrl = url;
        if (code != 200) {
            InputStream in = stream(c, code);
            r.body = in == null ? "" : readAll(in, 200000, UTF8, c);
            return r;
        }
        BufferedReader br = new BufferedReader(new InputStreamReader(c.getInputStream(), UTF8), 8192);
        try {
            String line;
            while ((line = br.readLine()) != null) {
                if (handler.cancelled()) {
                    c.disconnect();
                    throw new CancelledException();
                }
                try {
                    handler.onLine(line);
                } catch (IOException e) {
                    throw e;
                } catch (Exception e) {
                    throw new IOException(e.getMessage() == null ? e.toString() : e.getMessage(), e);
                }
            }
        } finally {
            try { br.close(); } catch (IOException ignored) { }
        }
        r.body = "";
        return r;
    }

    private static InputStream stream(HttpURLConnection c, int code) {
        try {
            return code >= 400 ? c.getErrorStream() : c.getInputStream();
        } catch (IOException e) {
            return c.getErrorStream();
        }
    }

    private static void drain(HttpURLConnection c, int code) {
        InputStream in = stream(c, code);
        if (in == null) return;
        try { readAll(in, 64000, UTF8, c); } catch (IOException ignored) { }
    }

    private static Charset charsetOf(String contentType) {
        if (contentType != null) {
            int i = contentType.toLowerCase().indexOf("charset=");
            if (i >= 0) {
                String cs = contentType.substring(i + 8).trim();
                int semi = cs.indexOf(';');
                if (semi >= 0) cs = cs.substring(0, semi);
                cs = cs.replace("\"", "").trim();
                try { return Charset.forName(cs); } catch (Exception ignored) { }
            }
        }
        return UTF8;
    }

    private static String readAll(InputStream in, int maxBytes, Charset cs, HttpURLConnection c) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int n;
        boolean truncated = false;
        try {
            while ((n = in.read(buf)) > 0) {
                int room = maxBytes - bos.size();
                if (room <= 0) { truncated = true; break; }
                bos.write(buf, 0, Math.min(n, room));
            }
        } finally {
            try { in.close(); } catch (IOException ignored) { }
            // A partially read body can't be reused for keep-alive; drop that socket.
            if (truncated) c.disconnect();
        }
        return new String(bos.toByteArray(), cs);
    }
}
