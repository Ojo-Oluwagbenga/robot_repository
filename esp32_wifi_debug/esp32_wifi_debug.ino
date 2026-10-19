/**
 * ESP32 Wi-Fi Hotspot Connection Diagnostics & Debugger
 *
 * This tool performs:
 * 1. 2.4 GHz Wi-Fi environment scan (lists all networks, signal RSSI, channels).
 * 2. Targeted search for your hotspot SSID ("el_Johannu").
 * 3. Detailed connection attempt with ESP32 Wi-Fi Event Listeners.
 * 4. Human-readable decode of all disconnect reasons (NO_AP_FOUND, AUTH_FAIL, etc.).
 * 5. Full network configuration report (IP, Gateway, DNS, MAC, RSSI).
 *
 * Baud rate: 115200
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>

// =============================================================================
// CREDENTIALS TO TEST (Matches your config.h)
// =============================================================================
const char* TARGET_SSID = "el_Johannu";
const char* TARGET_PASS = "12345678";

// =============================================================================
// Helper: Decode ESP32 Disconnect Reason Code
// =============================================================================
const char* getDisconnectReasonName(uint8_t reason) {
    switch (reason) {
        case 1:   return "UNSPECIFIED (General failure)";
        case 2:   return "AUTH_EXPIRE (Authentication expired)";
        case 3:   return "AUTH_LEAVE (Deauthenticated by device)";
        case 4:   return "ASSOC_EXPIRE (Association expired)";
        case 5:   return "ASSOC_TOOMANY (Too many devices on hotspot)";
        case 6:   return "NOT_AUTHED (Not authenticated)";
        case 7:   return "NOT_ASSOCED (Not associated)";
        case 8:   return "ASSOC_LEAVE";
        case 9:   return "ASSOC_NOT_AUTHED";
        case 10:  return "DISASSOC_PWRCAP_BAD";
        case 11:  return "DISASSOC_SUPCHAN_BAD";
        case 12:  return "BSS_TRANSITION_DISASSOC";
        case 13:  return "IE_INVALID";
        case 14:  return "MIC_FAILURE (WPA passphrase mismatch / corruption)";
        case 15:  return "4WAY_HANDSHAKE_TIMEOUT (Wrong password or handshake timeout)";
        case 16:  return "GROUP_KEY_UPDATE_TIMEOUT";
        case 17:  return "IE_IN_4WAY_DIFFERS";
        case 18:  return "GROUP_CIPHER_INVALID";
        case 19:  return "PAIRWISE_CIPHER_INVALID";
        case 20:  return "AKMP_INVALID";
        case 21:  return "UNSUPP_RSN_IE_VERSION";
        case 22:  return "INVALID_RSN_IE_CAP";
        case 23:  return "802_1X_AUTH_FAILED";
        case 24:  return "CIPHER_SUITE_REJECTED";
        case 200: return "BEACON_TIMEOUT (Signal lost / hotspot out of range)";
        case 201: return "NO_AP_FOUND (Hotspot SSID not found in 2.4 GHz band)";
        case 202: return "AUTH_FAIL (Incorrect password or auth mismatch)";
        case 203: return "ASSOC_FAIL (Hotspot rejected connection association)";
        case 204: return "HANDSHAKE_TIMEOUT (WPA handshake failed)";
        case 205: return "CONNECTION_FAIL";
        default:  return "OTHER_DISCONNECT_REASON";
    }
}

const char* getAuthModeName(wifi_auth_mode_t auth) {
    switch (auth) {
        case WIFI_AUTH_OPEN:         return "OPEN";
        case WIFI_AUTH_WEP:          return "WEP";
        case WIFI_AUTH_WPA_PSK:      return "WPA_PSK";
        case WIFI_AUTH_WPA2_PSK:     return "WPA2_PSK";
        case WIFI_AUTH_WPA_WPA2_PSK: return "WPA_WPA2_PSK";
        case WIFI_AUTH_WPA2_ENTERPRISE: return "WPA2_ENTERPRISE";
        case WIFI_AUTH_WPA3_PSK:     return "WPA3_PSK (May need WPA2/WPA3 mixed)";
        case WIFI_AUTH_WPA2_WPA3_PSK:return "WPA2_WPA3_PSK";
        default:                     return "UNKNOWN";
    }
}

const char* getWiFiStatusName(wl_status_t status) {
    switch (status) {
        case WL_NO_SHIELD:       return "WL_NO_SHIELD";
        case WL_IDLE_STATUS:     return "WL_IDLE_STATUS";
        case WL_NO_SSID_AVAIL:   return "WL_NO_SSID_AVAIL (Target SSID not visible)";
        case WL_SCAN_COMPLETED:  return "WL_SCAN_COMPLETED";
        case WL_CONNECTED:       return "WL_CONNECTED (Connected & IP acquired)";
        case WL_CONNECT_FAILED:  return "WL_CONNECT_FAILED (Authentication failed)";
        case WL_CONNECTION_LOST: return "WL_CONNECTION_LOST (Signal lost)";
        case WL_DISCONNECTED:    return "WL_DISCONNECTED";
        default:                 return "UNKNOWN_STATUS";
    }
}

// Track disconnect stats
static volatile uint8_t last_disconnect_reason = 0;
static volatile bool disconnected_event_fired = false;

// =============================================================================
// ESP32 Wi-Fi Event Handler Callback
// =============================================================================
void WiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
    switch (event) {
        case ARDUINO_EVENT_WIFI_READY:
            Serial.println("[EVENT] Wi-Fi interface ready.");
            break;
        case ARDUINO_EVENT_WIFI_STA_START:
            Serial.println("[EVENT] Wi-Fi Station mode started.");
            break;
        case ARDUINO_EVENT_WIFI_STA_CONNECTED:
            Serial.printf("[EVENT] Connected to AP: '%s'. Waiting for DHCP IP assignment...\n", TARGET_SSID);
            break;
        case ARDUINO_EVENT_WIFI_STA_GOT_IP:
            Serial.print("[EVENT] IP Address acquired: ");
            Serial.println(WiFi.localIP());
            break;
        case ARDUINO_EVENT_WIFI_STA_DISCONNECTED: {
            uint8_t reason = info.wifi_sta_disconnected.reason;
            last_disconnect_reason = reason;
            disconnected_event_fired = true;
            Serial.println();
            Serial.printf(">>> [WIFI EVENT DISCONNECT] Reason code: %d -> %s\n", 
                          reason, getDisconnectReasonName(reason));
            
            if (reason == 201 /* NO_AP_FOUND */) {
                Serial.println("    [DIAGNOSIS]: The ESP32 cannot find the hotspot SSID!");
                Serial.println("    -> 1. Is your phone's hotspot set to 5 GHz only?");
                Serial.println("          ESP32 only supports 2.4 GHz!");
                Serial.println("          iPhone: Turn ON 'Maximize Compatibility'.");
                Serial.println("          Android: In Hotspot Settings, change AP Band to '2.4 GHz band'.");
                Serial.println("    -> 2. Is the Hotspot name exactly 'el_Johannu' (case-sensitive, no spaces)?");
            } else if (reason == 202 /* AUTH_FAIL */ || 
                       reason == 15  /* 4WAY_HANDSHAKE_TIMEOUT */ ||
                       reason == 204 /* HANDSHAKE_TIMEOUT */) {
                Serial.println("    [DIAGNOSIS]: Password or authentication rejected!");
                Serial.printf("    -> 1. Check password '%s' for typos.\n", TARGET_PASS);
                Serial.println("    -> 2. Check hotspot security (WPA2 vs WPA3). Use WPA2-Personal.");
            } else if (reason == 203 /* ASSOC_FAIL */ || reason == 5 /* ASSOC_TOOMANY */) {
                Serial.println("    [DIAGNOSIS]: Hotspot refused connection. Check maximum connected clients limit.");
            }
            break;
        }
        default:
            break;
    }
}

// =============================================================================
// STEP 1: Scan for 2.4 GHz Wi-Fi Networks
// =============================================================================
bool scanAndCheckTarget() {
    Serial.println("\n--------------------------------------------------");
    Serial.println("[STEP 1] Scanning for nearby 2.4 GHz Wi-Fi networks...");
    Serial.println("--------------------------------------------------");

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    delay(100);

    int n = WiFi.scanNetworks(false, true); // synchronous scan, show hidden
    bool targetFound = false;

    if (n == 0) {
        Serial.println("[SCAN] No Wi-Fi networks found!");
        Serial.println("       Check ESP32 antenna or board power.");
        return false;
    }

    Serial.printf("[SCAN] Found %d networks:\n", n);
    Serial.println(" # | SSID                      | RSSI (dBm) | Ch | Security");
    Serial.println("---+---------------------------+------------+----+-------------------");

    for (int i = 0; i < n; ++i) {
        String ssid = WiFi.SSID(i);
        int32_t rssi = WiFi.RSSI(i);
        int32_t channel = WiFi.channel(i);
        wifi_auth_mode_t auth = WiFi.encryptionType(i);

        bool isTarget = (ssid == TARGET_SSID);
        if (isTarget) targetFound = true;

        char line[128];
        snprintf(line, sizeof(line), "%2d | %-25.25s | %4d dBm   | %2d | %s%s",
                 i + 1,
                 ssid.length() == 0 ? "(Hidden)" : ssid.c_str(),
                 rssi,
                 channel,
                 getAuthModeName(auth),
                 isTarget ? " <== [TARGET HOTSPOT!]" : "");
        Serial.println(line);
    }
    Serial.println("--------------------------------------------------");

    if (targetFound) {
        Serial.printf("[SCAN RESULT]: TARGET HOTSPOT '%s' WAS FOUND on 2.4 GHz!\n", TARGET_SSID);
        Serial.println("               Physical radio link is OK.");
    } else {
        Serial.println();
        Serial.printf(">>> [CRITICAL SCAN RESULT]: Target SSID '%s' WAS NOT FOUND!\n", TARGET_SSID);
        Serial.println("    Why the ESP32 cannot see your hotspot:");
        Serial.println("    1. 5 GHz Band Issue (MOST COMMON):");
        Serial.println("       The ESP32 only has a 2.4 GHz Wi-Fi radio.");
        Serial.println("       * If iPhone: Settings -> Personal Hotspot -> Turn ON 'Maximize Compatibility'.");
        Serial.println("       * If Android: Settings -> Hotspot -> AP Band -> Select '2.4 GHz' (not 5 GHz).");
        Serial.println("    2. Hotspot Inactive/Hidden:");
        Serial.println("       * Ensure Personal Hotspot is turned ON.");
        Serial.println("       * Keep the hotspot settings screen open on your phone during connection.");
        Serial.println("    3. Exact SSID Match:");
        Serial.printf("       * Expected: '%s'\n", TARGET_SSID);
        Serial.println("       * Check for leading/trailing spaces or lowercase/uppercase differences.");
        Serial.println();
    }

    WiFi.scanDelete();
    return targetFound;
}

// =============================================================================
// STEP 2: Attempt Connection with Detailed Logging
// =============================================================================
void testConnection() {
    Serial.println("\n--------------------------------------------------");
    Serial.printf("[STEP 2] Attempting connection to '%s'...\n", TARGET_SSID);
    Serial.printf("         Password: '%s'\n", TARGET_PASS);
    Serial.println("--------------------------------------------------");

    WiFi.mode(WIFI_STA);
    WiFi.setHostname("esp32-wifi-debug");
    
    // Explicitly configure ESP32 Wi-Fi parameters for robust hotspot compatibility
    WiFi.setAutoReconnect(true);
    WiFi.persistent(false); // Don't rely on flash NVS cached state

    disconnected_event_fired = false;
    last_disconnect_reason = 0;

    uint32_t startMs = millis();
    WiFi.begin(TARGET_SSID, TARGET_PASS);

    Serial.println("[CONNECTING] Waiting for DHCP & IP (up to 20 seconds)...");
    
    int dotCount = 0;
    while (WiFi.status() != WL_CONNECTED && (millis() - startMs < 20000)) {
        delay(1000);
        dotCount++;

        wl_status_t curStatus = WiFi.status();
        Serial.printf("  [T+%02ds] Status: %-15s (%s)\n", 
                      dotCount, 
                      getWiFiStatusName(curStatus),
                      disconnected_event_fired ? getDisconnectReasonName(last_disconnect_reason) : "Negotiating...");
    }

    Serial.println();
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("==================================================");
        Serial.println(">>> SUCCESS! ESP32 CONNECTED TO YOUR HOTSPOT! <<<");
        Serial.println("==================================================");
        Serial.printf("SSID            : %s\n", WiFi.SSID().c_str());
        Serial.printf("IP Address      : %s\n", WiFi.localIP().toString().c_str());
        Serial.printf("Subnet Mask     : %s\n", WiFi.subnetMask().toString().c_str());
        Serial.printf("Gateway         : %s\n", WiFi.gatewayIP().toString().c_str());
        Serial.printf("DNS Server      : %s\n", WiFi.dnsIP().toString().c_str());
        Serial.printf("MAC Address     : %s\n", WiFi.macAddress().c_str());
        Serial.printf("Signal Strength : %d dBm\n", WiFi.RSSI());
        Serial.printf("Hostname        : %s.local\n", WiFi.getHostname());
        Serial.println("==================================================");
        Serial.println("Your Wi-Fi credentials in config.h are 100% correct!");
    } else {
        Serial.println("==================================================");
        Serial.println(">>> FAILED TO CONNECT AFTER 20 SECONDS <<<");
        Serial.println("==================================================");
        Serial.printf("Final Status Code : %s\n", getWiFiStatusName(WiFi.status()));
        if (last_disconnect_reason > 0) {
            Serial.printf("Last Disconnect   : Code %d (%s)\n", 
                          last_disconnect_reason, 
                          getDisconnectReasonName(last_disconnect_reason));
        }
        Serial.println("--------------------------------------------------");
        Serial.println("Summary Checklist to Fix:");
        Serial.println(" 1. Phone Hotspot AP Band MUST be 2.4 GHz (Turn on 'Maximize Compatibility').");
        Serial.println(" 2. Verify password: '" + String(TARGET_PASS) + "'");
        Serial.println(" 3. Verify SSID: '" + String(TARGET_SSID) + "'");
        Serial.println(" 4. Keep your phone's Hotspot screen active while powering on the ESP32.");
        Serial.println("==================================================");
    }
}

// =============================================================================
// SETUP & LOOP
// =============================================================================
void setup() {
    Serial.begin(115200);
    delay(1000); // Allow USB Serial to stabilize

    Serial.println();
    Serial.println("==================================================");
    Serial.println("   ESP32 WI-FI HOTSPOT DIAGNOSTIC & DEBUG TOOL   ");
    Serial.println("==================================================");
    Serial.printf("Target SSID : '%s'\n", TARGET_SSID);
    Serial.printf("Target Pass : '%s'\n", TARGET_PASS);
    Serial.println("==================================================");

    // Register event listener
    WiFi.onEvent(WiFiEvent);

    // 1. Scan Wi-Fi environment
    scanAndCheckTarget();

    // 2. Test actual connection
    testConnection();
}

void loop() {
    // Continuous monitoring every 5 seconds
    static uint32_t lastCheck = 0;
    if (millis() - lastCheck > 5000) {
        lastCheck = millis();
        if (WiFi.status() == WL_CONNECTED) {
            Serial.printf("[STATUS HEARTBEAT] Connected | IP: %s | RSSI: %d dBm\n",
                          WiFi.localIP().toString().c_str(), WiFi.RSSI());
        } else {
            Serial.printf("[STATUS HEARTBEAT] NOT connected | Status: %s\n",
                          getWiFiStatusName(WiFi.status()));
        }
    }
    delay(100);
}
