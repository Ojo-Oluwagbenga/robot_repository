package com.robocode.lukulive;

import android.content.Context;
import android.net.DhcpInfo;
import android.net.wifi.WifiManager;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.net.URI;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Automatically discovers the Robot Bridge server on the local Wi-Fi network
 * when its IP address or port changes.
 */
public final class BridgeDiscovery {
    private static final String TAG = "BridgeDiscovery";

    public static final int[] COMMON_PORTS = new int[]{3000, 3001, 3002, 8080, 8000, 5000, 80};
    private static final int SOCKET_TIMEOUT_MS = 400;
    private static final int HTTP_TIMEOUT_MS = 1000;

    public interface DiscoveryCallback {
        void onFound(String host, int port, String fullUrl);
        void onFailed();
    }

    private BridgeDiscovery() {}

    /**
     * Checks if a candidate URL currently responds as a valid Robot Bridge.
     */
    public static boolean testBridge(String baseUrl, int timeoutMs) {
        if (baseUrl == null || baseUrl.isEmpty()) return false;
        try {
            while (baseUrl.endsWith("/")) baseUrl = baseUrl.substring(0, baseUrl.length() - 1);
            String testUrl = baseUrl + "/api/pi_instructions?limit=1";
            HttpURLConnection c = (HttpURLConnection) new URL(testUrl).openConnection();
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            c.setRequestMethod("GET");
            c.setRequestProperty("User-Agent", Http.UA);
            c.setRequestProperty("Accept", "application/json,text/plain,*/*");
            int code = c.getResponseCode();
            if (code == 200) {
                BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()), 1024);
                String line = r.readLine();
                r.close();
                c.disconnect();
                if (line != null && (line.contains("pi_instructions") || line.contains("success") || line.contains("code") || line.contains("timestamp"))) {
                    return true;
                }
            }
            c.disconnect();
        } catch (Exception ignored) {}

        // Fallback check on /gemini
        try {
            String testUrl = baseUrl + "/gemini";
            HttpURLConnection c = (HttpURLConnection) new URL(testUrl).openConnection();
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            c.setRequestMethod("GET");
            c.setRequestProperty("User-Agent", Http.UA);
            int code = c.getResponseCode();
            if (code == 200) {
                BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()), 1024);
                String line = r.readLine();
                r.close();
                c.disconnect();
                if (line != null && (line.contains("ROBOT") || line.contains("NOD") || line.contains("WAVE") || line.contains("INSTRUCTION") || line.length() > 20)) {
                    return true;
                }
            }
            c.disconnect();
        } catch (Exception ignored) {}

        return false;
    }

    /**
     * Quick socket connectivity check before initiating HTTP handshake.
     */
    private static boolean isPortOpen(String host, int port, int timeoutMs) {
        try {
            Socket socket = new Socket();
            socket.connect(new InetSocketAddress(host, port), timeoutMs);
            socket.close();
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * Retrieves the device's local IPv4 address on the active Wi-Fi interface.
     */
    public static String getDeviceIp() {
        try {
            for (Enumeration<NetworkInterface> en = NetworkInterface.getNetworkInterfaces(); en.hasMoreElements();) {
                NetworkInterface intf = en.nextElement();
                if (!intf.isUp() || intf.isLoopback()) continue;
                for (Enumeration<InetAddress> enumIpAddr = intf.getInetAddresses(); enumIpAddr.hasMoreElements();) {
                    InetAddress inetAddress = enumIpAddr.nextElement();
                    if (!inetAddress.isLoopbackAddress() && inetAddress instanceof Inet4Address) {
                        return inetAddress.getHostAddress();
                    }
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    /**
     * Retrieves the default Wi-Fi gateway IP (typically the router or hotspot host).
     */
    public static String getGatewayIp(Context ctx) {
        if (ctx == null) return null;
        try {
            WifiManager wm = (WifiManager) ctx.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm != null) {
                DhcpInfo dhcp = wm.getDhcpInfo();
                if (dhcp != null && dhcp.gateway != 0) {
                    int g = dhcp.gateway;
                    return (g & 0xFF) + "." + ((g >> 8) & 0xFF) + "." + ((g >> 16) & 0xFF) + "." + ((g >> 24) & 0xFF);
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    /**
     * Compiles a prioritized list of candidate hostnames and IP addresses to probe.
     */
    public static List<String> getCandidateHosts(Context ctx, Prefs prefs) {
        Set<String> hosts = new LinkedHashSet<String>();

        // 1. Configured host in settings
        if (prefs != null) {
            String savedHost = prefs.bridgeHost();
            if (savedHost != null && !savedHost.isEmpty() && !savedHost.equals("auto")) {
                hosts.add(savedHost.trim());
            }
            try {
                URI uri = new URI(prefs.instructionUrl());
                if (uri.getHost() != null && !uri.getHost().isEmpty()) {
                    hosts.add(uri.getHost());
                }
            } catch (Exception ignored) {}
        }

        // 2. Gateway IP (the hotspot host / PC running the hotspot)
        String gateway = getGatewayIp(ctx);
        if (gateway != null && !gateway.isEmpty() && !gateway.equals("0.0.0.0")) {
            hosts.add(gateway);
        }

        // 3. Subnet-based candidate hosts based on device's current Wi-Fi IP
        String devIp = getDeviceIp();
        if (devIp != null && devIp.contains(".")) {
            String prefix = devIp.substring(0, devIp.lastIndexOf('.') + 1);
            // Common robot / PC static and DHCP assignments
            hosts.add(prefix + "172"); // PC host common IP
            hosts.add(prefix + "92");  // Raspberry Pi common IP
            hosts.add(prefix + "1");   // Router / Hotspot
            hosts.add(prefix + "68");  // Current gateway subnet offset
            hosts.add(prefix + "100");
            hosts.add(prefix + "101");
            hosts.add(prefix + "2");
        }

        // 4. Known historical robot & PC IP addresses
        hosts.add("10.36.113.172");
        hosts.add("10.208.131.172");
        hosts.add("10.208.131.92");
        hosts.add("10.36.113.92");

        // 5. Localhost and mDNS names
        hosts.add("raspberrypi.local");
        hosts.add("robot.local");
        hosts.add("localhost");
        hosts.add("10.0.2.2"); // Android emulator host

        return new ArrayList<String>(hosts);
    }

    /**
     * Compiles a prioritized list of candidate ports to probe.
     */
    public static List<Integer> getCandidatePorts(Prefs prefs) {
        Set<Integer> ports = new LinkedHashSet<Integer>();
        if (prefs != null) {
            int savedPort = prefs.bridgePort();
            if (savedPort > 0) ports.add(savedPort);
        }
        for (int p : COMMON_PORTS) ports.add(p);
        return new ArrayList<Integer>(ports);
    }

    /**
     * Synchronously locates a working bridge server by probing candidates in parallel.
     * If currently configured bridge works, returns immediately.
     * Updates Prefs if a new working bridge is found.
     *
     * @return Working base URL, e.g. "http://10.36.113.172:3000", or null if none reachable.
     */
    public static String findWorkingBridge(Context ctx, Prefs prefs) {
        // Fast path: test current bridge base first
        if (prefs != null) {
            String current = prefs.localBridgeBase();
            if (testBridge(current, 800)) {
                Log.i(TAG, "Current bridge base is working: " + current);
                return current;
            }
        }

        Log.i(TAG, "Current bridge base unreachable. Scanning local network for new address/port...");
        List<String> hosts = getCandidateHosts(ctx, prefs);
        List<Integer> ports = getCandidatePorts(prefs);

        final AtomicReference<String> foundBase = new AtomicReference<String>(null);
        final AtomicBoolean stopScan = new AtomicBoolean(false);

        // Build list of (host, port) pairs
        List<Pair> targets = new ArrayList<Pair>();
        for (String h : hosts) {
            for (int p : ports) {
                targets.add(new Pair(h, p));
            }
        }

        ExecutorService executor = Executors.newFixedThreadPool(12);
        final CountDownLatch latch = new CountDownLatch(targets.size());

        for (final Pair t : targets) {
            executor.execute(new Runnable() {
                @Override public void run() {
                    try {
                        if (stopScan.get()) return;

                        // 1. Quick TCP socket test
                        if (!isPortOpen(t.host, t.port, SOCKET_TIMEOUT_MS)) return;
                        if (stopScan.get()) return;

                        // 2. HTTP validation
                        String candidate = "http://" + t.host + ":" + t.port;
                        if (testBridge(candidate, HTTP_TIMEOUT_MS)) {
                            if (stopScan.compareAndSet(false, true)) {
                                foundBase.set(candidate);
                                Log.i(TAG, "[DISCOVERY SUCCESS] Found active Robot Bridge: " + candidate);
                            }
                        }
                    } finally {
                        latch.countDown();
                    }
                }
            });
        }

        try {
            // Wait up to 3 seconds for scan to complete or until found
            long deadline = System.currentTimeMillis() + 3000;
            while (System.currentTimeMillis() < deadline && !stopScan.get()) {
                if (latch.await(100, TimeUnit.MILLISECONDS)) break;
            }
        } catch (InterruptedException ignored) {}

        executor.shutdownNow();

        String result = foundBase.get();
        if (result != null && prefs != null) {
            prefs.updateDiscoveredBridge(result);
        }
        return result;
    }

    /**
     * Asynchronous discovery for UI triggers.
     */
    public static void discoverAsync(final Context ctx, final Prefs prefs, final DiscoveryCallback callback) {
        final Handler mainHandler = new Handler(Looper.getMainLooper());
        new Thread(new Runnable() {
            @Override public void run() {
                final String found = findWorkingBridge(ctx, prefs);
                mainHandler.post(new Runnable() {
                    @Override public void run() {
                        if (found != null && callback != null) {
                            try {
                                URI uri = new URI(found);
                                callback.onFound(uri.getHost(), uri.getPort() > 0 ? uri.getPort() : 3000, found);
                            } catch (Exception e) {
                                callback.onFound(found, 3000, found);
                            }
                        } else if (callback != null) {
                            callback.onFailed();
                        }
                    }
                });
            }
        }, "BridgeDiscovery").start();
    }

    private static final class Pair {
        final String host;
        final int port;
        Pair(String host, int port) { this.host = host; this.port = port; }
    }
}
