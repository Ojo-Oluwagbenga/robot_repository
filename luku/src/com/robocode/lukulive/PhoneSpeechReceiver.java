package com.robocode.lukulive;

import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.Charset;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/**
 * Phone Speech Receiver.
 * Enables the Raspberry Pi to dispatch text to the phone app so it gets
 * voiced out loud over the speaker connected to the phone.
 *
 * Provides two concurrent channels:
 *  1. Bridge Queue Poller: Polls /api/phone_speech?pending=true from the Robot Bridge Site
 *     every 500ms and immediately acknowledges packets upon receipt.
 *  2. Direct LAN HTTP Listener: Runs an embedded lightweight HTTP server on port 8888
 *     for instantaneous, low-latency direct push (e.g. POST /speak) from the Pi.
 */
public final class PhoneSpeechReceiver {
    private static final String TAG = "PhoneSpeechReceiver";
    private static final Charset UTF8 = Charset.forName("UTF-8");

    public interface Listener {
        /**
         * Called when incoming speech arrives from the Raspberry Pi or Bridge.
         * Must be safe to marshal to the UI thread for vocalization.
         */
        void onIncomingSpeech(String id, String text, String source);
    }

    private final Prefs prefs;
    private final Listener listener;

    private ScheduledExecutorService pollerExecutor;
    private ScheduledFuture<?> pollerFuture;
    private Thread serverThread;
    private ServerSocket serverSocket;
    private volatile boolean running;

    // LRU Set of processed IDs to guarantee each message is voiced exactly once
    private final Set<String> processedIds = Collections.synchronizedSet(
            Collections.newSetFromMap(new LinkedHashMap<String, Boolean>(64, 0.75f, true) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, Boolean> eldest) {
                    return size() > 300;
                }
            })
    );

    public PhoneSpeechReceiver(Prefs prefs, Listener listener) {
        this.prefs = prefs;
        this.listener = listener;
    }

    /**
     * Start background listening and polling services.
     */
    public synchronized void start() {
        if (running) return;
        running = true;

        // 1. Start Bridge Site polling service
        pollerExecutor = Executors.newSingleThreadScheduledExecutor();
        pollerFuture = pollerExecutor.scheduleWithFixedDelay(new Runnable() {
            @Override
            public void run() {
                try {
                    pollBridgeQueue();
                } catch (Throwable t) {
                    // Suppress and continue polling on next tick
                }
            }
        }, 500, 500, TimeUnit.MILLISECONDS);

        // 2. Start direct LAN HTTP server on port 8888
        serverThread = new Thread(new Runnable() {
            @Override
            public void run() {
                runDirectHttpServer();
            }
        }, "LukuDirectSpeechServer");
        serverThread.setDaemon(true);
        serverThread.start();

        Log.i(TAG, "PhoneSpeechReceiver started. Listening for Pi speech packets.");
    }

    /**
     * Stop background listening and polling services.
     */
    public synchronized void stop() {
        running = false;
        if (pollerFuture != null) {
            pollerFuture.cancel(true);
            pollerFuture = null;
        }
        if (pollerExecutor != null) {
            pollerExecutor.shutdownNow();
            pollerExecutor = null;
        }
        if (serverSocket != null) {
            try {
                serverSocket.close();
            } catch (Exception ignored) { }
            serverSocket = null;
        }
        if (serverThread != null) {
            serverThread.interrupt();
            serverThread = null;
        }
        Log.i(TAG, "PhoneSpeechReceiver stopped.");
    }

    // ------------------------------------------------------------------ Channel 1: Bridge Queue Poller

    private void pollBridgeQueue() {
        if (!running) return;
        String base = prefs.localBridgeBase();
        if (base == null || base.isEmpty()) return;

        try {
            String url = base + "/api/phone_speech?pending=true";
            Http.Resp r = Http.send("GET", url, null, null, 3000, 200000);
            if (r.code != 200 || r.body == null || r.body.isEmpty()) return;

            JSONObject json = new JSONObject(r.body);
            JSONArray array = json.optJSONArray("speech");
            if (array == null || array.length() == 0) return;

            for (int i = 0; i < array.length(); i++) {
                JSONObject item = array.optJSONObject(i);
                if (item == null) continue;

                String id = item.optString("id");
                String text = item.optString("text");
                String source = item.optString("source", "pi");

                if (id.isEmpty()) id = "speech_" + System.currentTimeMillis() + "_" + i;

                if (text != null && !text.trim().isEmpty() && processedIds.add(id)) {
                    Log.i(TAG, "Received speech packet via bridge: " + id + " -> \"" + text + "\"");
                    if (listener != null) {
                        listener.onIncomingSpeech(id, text.trim(), source);
                    }
                    // Acknowledge immediately so it is not resent
                    ackBridgePacket(base, id);
                }
            }
        } catch (Exception ignored) {
            // Network hiccups or bridge offline, will retry on next poll interval
        }
    }

    private void ackBridgePacket(String base, String id) {
        try {
            String ackUrl = base + "/api/phone_speech?ack=" + URLEncoder.encode(id, "UTF-8");
            Http.send("GET", ackUrl, null, null, 3000, 10000);
        } catch (Exception e) {
            Log.w(TAG, "Failed to send speech ack for ID: " + id);
        }
    }

    // ------------------------------------------------------------------ Channel 2: Direct LAN HTTP Server

    private void runDirectHttpServer() {
        int port = prefs.phoneSpeechPort();
        try {
            serverSocket = new ServerSocket(port);
            Log.i(TAG, "Direct speech HTTP server listening on port " + port);
        } catch (Exception e) {
            try {
                port = 8889;
                serverSocket = new ServerSocket(port);
                Log.i(TAG, "Direct speech HTTP server fallback listening on port " + port);
            } catch (Exception ex) {
                Log.w(TAG, "Could not bind direct speech server socket: " + ex.getMessage());
                return;
            }
        }

        while (running && serverSocket != null && !serverSocket.isClosed()) {
            Socket client = null;
            try {
                client = serverSocket.accept();
                client.setSoTimeout(4000);
                handleDirectClient(client);
            } catch (Exception e) {
                if (!running) break;
            } finally {
                if (client != null) {
                    try { client.close(); } catch (Exception ignored) { }
                }
            }
        }
    }

    private void handleDirectClient(Socket client) throws Exception {
        InputStream in = client.getInputStream();
        OutputStream out = client.getOutputStream();
        BufferedReader reader = new BufferedReader(new InputStreamReader(in, UTF8));

        String requestLine = reader.readLine();
        if (requestLine == null || requestLine.isEmpty()) return;

        String[] parts = requestLine.split(" ");
        if (parts.length < 2) return;
        String method = parts[0].toUpperCase();
        String path = parts[1];

        int contentLength = 0;
        String line;
        while ((line = reader.readLine()) != null && !line.isEmpty()) {
            String lower = line.toLowerCase();
            if (lower.startsWith("content-length:")) {
                try {
                    contentLength = Integer.parseInt(line.substring(15).trim());
                } catch (Exception ignored) { }
            }
        }

        String body = "";
        if (contentLength > 0 && contentLength < 65536) {
            char[] buf = new char[contentLength];
            int read = 0;
            while (read < contentLength) {
                int r = reader.read(buf, read, contentLength - read);
                if (r < 0) break;
                read += r;
            }
            body = new String(buf, 0, read);
        }

        // Handle OPTIONS (CORS Preflight)
        if ("OPTIONS".equals(method)) {
            sendResponse(out, 204, "No Content", "text/plain", "");
            return;
        }

        // Handle /ping or /status
        if (path.startsWith("/ping") || path.startsWith("/status")) {
            JSONObject resp = new JSONObject()
                    .put("status", "online")
                    .put("service", "luku_phone_speaker")
                    .put("timestamp", System.currentTimeMillis());
            sendResponse(out, 200, "OK", "application/json", resp.toString());
            return;
        }

        // Handle /speak
        if (path.startsWith("/speak")) {
            String text = "";
            String source = "pi_direct";

            // Case 1: Query param ?text=...
            int queryIdx = path.indexOf('?');
            if (queryIdx >= 0) {
                String query = path.substring(queryIdx + 1);
                for (String param : query.split("&")) {
                    int eq = param.indexOf('=');
                    if (eq > 0) {
                        String k = param.substring(0, eq);
                        String v = URLDecoder.decode(param.substring(eq + 1), "UTF-8");
                        if ("text".equals(k) || "val".equals(k) || "msg".equals(k)) text = v;
                        if ("source".equals(k)) source = v;
                    }
                }
            }

            // Case 2: Body JSON or text
            if (text.isEmpty() && !body.isEmpty()) {
                try {
                    JSONObject obj = new JSONObject(body);
                    text = obj.optString("text", obj.optString("message", obj.optString("val", "")));
                    source = obj.optString("source", source);
                } catch (Exception ignored) {
                    text = body.trim();
                }
            }

            if (!text.trim().isEmpty()) {
                String id = "direct_" + System.currentTimeMillis();
                processedIds.add(id);
                Log.i(TAG, "Received direct LAN speech: \"" + text + "\" from " + client.getInetAddress());

                if (listener != null) {
                    listener.onIncomingSpeech(id, text.trim(), source);
                }

                JSONObject resp = new JSONObject()
                        .put("success", true)
                        .put("spoken", true)
                        .put("id", id)
                        .put("text", text.trim());
                sendResponse(out, 200, "OK", "application/json", resp.toString());
                return;
            } else {
                JSONObject resp = new JSONObject().put("success", false).put("error", "Empty text parameter");
                sendResponse(out, 400, "Bad Request", "application/json", resp.toString());
                return;
            }
        }

        // Default 404
        JSONObject resp = new JSONObject().put("error", "Endpoint not found");
        sendResponse(out, 404, "Not Found", "application/json", resp.toString());
    }

    private void sendResponse(OutputStream out, int code, String msg, String contentType, String body) throws Exception {
        byte[] bytes = body.getBytes(UTF8);
        String header = "HTTP/1.1 " + code + " " + msg + "\r\n"
                + "Content-Type: " + contentType + "; charset=utf-8\r\n"
                + "Content-Length: " + bytes.length + "\r\n"
                + "Access-Control-Allow-Origin: *\r\n"
                + "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                + "Access-Control-Allow-Headers: Content-Type\r\n"
                + "Connection: close\r\n\r\n";
        out.write(header.getBytes(UTF8));
        if (bytes.length > 0) {
            out.write(bytes);
        }
        out.flush();
    }
}
