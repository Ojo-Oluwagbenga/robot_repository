#include "wifi_communication.h"
#include "encoder.h"
#include "motor.h"
#include <esp_wifi.h>
#include <ctype.h>
#include <string.h>

WifiCommunication wifiComm;

// Forward declaration for visual indicator blink
extern void trigger_instruction_double_blink();

// Simple zero-dependency JSON helper functions
static int extractJsonInt(const char* json, const char* key, int defaultVal) {
    if (!json || !key) return defaultVal;
    const char* p = json;
    size_t klen = strlen(key);

    while ((p = strstr(p, key)) != NULL) {
        // Ensure key is preceded by quote or start
        bool precededByQuote = (p > json && *(p - 1) == '"');
        const char* afterKey = p + klen;
        if (precededByQuote && *afterKey == '"') afterKey++; // skip trailing quote
        while (*afterKey == ' ' || *afterKey == '\t') afterKey++;
        if (*afterKey == ':') {
            afterKey++;
            while (*afterKey == ' ' || *afterKey == '\t' || *afterKey == '"') afterKey++;
            int sign = 1;
            if (*afterKey == '-') { sign = -1; afterKey++; }
            else if (*afterKey == '+') { afterKey++; }
            if (isdigit(*afterKey)) {
                int val = 0;
                while (isdigit(*afterKey)) {
                    val = val * 10 + (*afterKey - '0');
                    afterKey++;
                }
                return val * sign;
            }
        }
        p += klen;
    }
    return defaultVal;
}

static bool extractJsonString(const char* json, const char* key, char* out, size_t maxLen) {
    if (!json || !key || !out || maxLen == 0) return false;
    out[0] = '\0';
    const char* p = json;
    size_t klen = strlen(key);

    while ((p = strstr(p, key)) != NULL) {
        const char* afterKey = p + klen;
        if (*afterKey == '"') afterKey++;
        while (*afterKey == ' ' || *afterKey == '\t') afterKey++;
        if (*afterKey == ':') {
            afterKey++;
            while (*afterKey == ' ' || *afterKey == '\t') afterKey++;
            if (*afterKey == '"') {
                afterKey++;
                size_t idx = 0;
                while (*afterKey && *afterKey != '"' && idx < maxLen - 1) {
                    out[idx++] = *afterKey++;
                }
                out[idx] = '\0';
                return true;
            } else {
                // Unquoted string (e.g. true, false, word)
                size_t idx = 0;
                while (*afterKey && *afterKey != ',' && *afterKey != '}' && *afterKey != ' ' && idx < maxLen - 1) {
                    out[idx++] = *afterKey++;
                }
                out[idx] = '\0';
                return true;
            }
        }
        p += klen;
    }
    return false;
}

WifiCommunication::WifiCommunication()
    : _webServer(WEB_OTA_PORT),
      _queueHead(0),
      _queueTail(0),
      _queueCount(0),
      _lastSenderIP(255, 255, 255, 255),
      _lastSenderPort(WIFI_UDP_PORT),
      _lastReconnectAttempt(0),
      _lastStatusBroadcast(0),
      _apFallbackActive(false) {
    snprintf(_hostname, sizeof(_hostname), "esp32-robot-%d", ESP_ID);
    memset(_commandQueue, 0, sizeof(_commandQueue));
}

void WifiCommunication::begin() {
    setupWiFi();
    setupOTA();
    setupWebOTA();

    // Start UDP listening
    if (_udp.begin(WIFI_UDP_PORT)) {
#if DEBUG_LEVEL > 0
        Serial.printf("[WIFI] UDP broadcast listener bound to port %d\n", WIFI_UDP_PORT);
#endif
    } else {
#if DEBUG_LEVEL > 0
        Serial.printf("[WIFI ERROR] Failed to bind UDP listener to port %d\n", WIFI_UDP_PORT);
#endif
    }

    sendStatusBroadcast();
}

void WifiCommunication::setupWiFi() {
    WiFi.disconnect(true);
    delay(100);
    WiFi.mode(WIFI_STA);
    WiFi.setHostname(_hostname);
    WiFi.setAutoReconnect(true);
    WiFi.persistent(false);

#if DEBUG_LEVEL > 0
    Serial.println("\n==================================================");
    Serial.printf("[WIFI] Connecting to SSID: '%s' (Hostname: %s)...\n", WIFI_SSID, _hostname);
    Serial.println("==================================================");

    // Register event listener to catch disconnect reasons
    WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
        if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
            uint8_t reason = info.wifi_sta_disconnected.reason;
            Serial.printf("\n[WIFI DISCONNECT] Reason code %d: ", reason);
            switch (reason) {
                case WIFI_REASON_NO_AP_FOUND:
                    Serial.println("NO_AP_FOUND (Hotspot not found! Check if hotspot is 5 GHz instead of 2.4 GHz)");
                    break;
                case WIFI_REASON_AUTH_FAIL:
                case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
                case WIFI_REASON_HANDSHAKE_TIMEOUT:
                    Serial.println("AUTH_FAIL (Incorrect password or WPA authentication rejected)");
                    break;
                case WIFI_REASON_ASSOC_FAIL:
                case WIFI_REASON_ASSOC_TOOMANY:
                    Serial.println("ASSOC_FAIL (Connection rejected by hotspot / max clients reached)");
                    break;
                case WIFI_REASON_BEACON_TIMEOUT:
                    Serial.println("BEACON_TIMEOUT (Signal lost or out of range)");
                    break;
                default:
                    Serial.printf("Error code %d\n", reason);
                    break;
            }
        } else if (event == ARDUINO_EVENT_WIFI_STA_CONNECTED) {
            Serial.println("\n[WIFI] Connected to AP! Awaiting IP from DHCP...");
        } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
            Serial.printf("\n[WIFI] IP assigned: %s\n", WiFi.localIP().toString().c_str());
        }
    });
#endif

    WiFi.begin(WIFI_SSID, WIFI_PASS);

    uint32_t startMs = millis();
    uint32_t lastPrintMs = 0;
    while (WiFi.status() != WL_CONNECTED && (millis() - startMs < 15000)) {
        delay(250);
#if DEBUG_LEVEL > 0
        if (millis() - lastPrintMs >= 1000) {
            lastPrintMs = millis();
            Serial.printf(" [T+%ds: %s]", (int)((millis() - startMs) / 1000), 
                          (WiFi.status() == WL_NO_SSID_AVAIL) ? "SSID_NOT_FOUND" :
                          (WiFi.status() == WL_CONNECT_FAILED) ? "AUTH_FAILED" :
                          (WiFi.status() == WL_DISCONNECTED) ? "CONNECTING" : "IDLE");
        }
#endif
    }

    if (WiFi.status() == WL_CONNECTED) {
#if DEBUG_LEVEL > 0
        Serial.println("\n==================================================");
        Serial.printf("[WIFI OK] Connected to %s!\n", WIFI_SSID);
        Serial.printf("[WIFI OK] IP Address : %s\n", WiFi.localIP().toString().c_str());
        Serial.printf("[WIFI OK] Subnet Mask: %s\n", WiFi.subnetMask().toString().c_str());
        Serial.printf("[WIFI OK] Gateway    : %s\n", WiFi.gatewayIP().toString().c_str());
        Serial.printf("[WIFI OK] Signal     : %d dBm\n", WiFi.RSSI());
        Serial.printf("[WIFI OK] Hostname   : %s.local\n", _hostname);
        Serial.println("==================================================");
#endif
        if (MDNS.begin(_hostname)) {
            MDNS.addService("http", "tcp", WEB_OTA_PORT);
            MDNS.addService("arduino", "tcp", OTA_PORT);
        }
    } else {
#if DEBUG_LEVEL > 0
        Serial.println("\n\n==================================================");
        Serial.println("[WIFI WARN] Station connect timed out (15s)!");
        Serial.println("Scanning 2.4 GHz Wi-Fi networks in range...");
        int n = WiFi.scanNetworks(false, true);
        if (n == 0) {
            Serial.println("  -> [SCAN] 0 networks found! Check ESP32 antenna / power.");
        } else {
            bool foundTarget = false;
            Serial.printf("  -> [SCAN] Found %d networks in 2.4 GHz band:\n", n);
            for (int i = 0; i < n; ++i) {
                String s = WiFi.SSID(i);
                bool matches = s.equals(WIFI_SSID);
                if (matches) foundTarget = true;
                Serial.printf("     * '%s' (%d dBm)%s\n", s.c_str(), WiFi.RSSI(i), matches ? " <== [MATCHES TARGET!]" : "");
            }
            if (!foundTarget) {
                Serial.printf("  -> [CRITICAL]: '%s' was NOT found in the 2.4 GHz scan!\n", WIFI_SSID);
                Serial.println("     1. Check if phone hotspot is set to 5 GHz only:");
                Serial.println("        - iPhone: Turn ON 'Maximize Compatibility'");
                Serial.println("        - Android: Set AP Band to '2.4 GHz'");
                Serial.println("     2. Check for typos or trailing spaces in config.h");
            }
        }
        WiFi.scanDelete();
        Serial.println("Starting fallback AP mode...");
        Serial.println("==================================================");
#endif
        // Start AP mode so device is never bricked or unreachable over the air
        char apSsid[32];
        snprintf(apSsid, sizeof(apSsid), "ESP32-Robot-%d-AP", ESP_ID);
        WiFi.mode(WIFI_AP_STA);
        WiFi.softAP(apSsid, "robot1234");
        _apFallbackActive = true;
#if DEBUG_LEVEL > 0
        Serial.printf("[WIFI AP] Fallback AP created: %s (IP: %s)\n", apSsid, WiFi.softAPIP().toString().c_str());
#endif
    }
}

void WifiCommunication::setupOTA() {
    ArduinoOTA.setPort(OTA_PORT);
    ArduinoOTA.setHostname(_hostname);

    if (strlen(OTA_PASSWORD) > 0) {
        ArduinoOTA.setPassword(OTA_PASSWORD);
    }

    ArduinoOTA.onStart([]() {
        String type;
        if (ArduinoOTA.getCommand() == U_FLASH) {
            type = "sketch";
        } else {
            type = "filesystem";
        }
        Serial.printf("\n[OTA START] Over-The-Air Update starting: %s\n", type.c_str());
        digitalWrite(PIN_LED, HIGH);
    });

    ArduinoOTA.onEnd([]() {
        Serial.println("\n[OTA END] Update complete! Rebooting ESP32...");
        digitalWrite(PIN_LED, LOW);
    });

    ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
        Serial.printf("[OTA PROGRESS] %u%%\r", (progress / (total / 100)));
    });

    ArduinoOTA.onError([](ota_error_t error) {
        Serial.printf("[OTA ERROR][%u]: ", error);
        if (error == OTA_AUTH_ERROR) Serial.println("Auth Failed");
        else if (error == OTA_BEGIN_ERROR) Serial.println("Begin Failed");
        else if (error == OTA_CONNECT_ERROR) Serial.println("Connect Failed");
        else if (error == OTA_RECEIVE_ERROR) Serial.println("Receive Failed");
        else if (error == OTA_END_ERROR) Serial.println("End Failed");
        digitalWrite(PIN_LED, LOW);
    });

    ArduinoOTA.begin();
#if DEBUG_LEVEL > 0
    Serial.printf("[OTA] ArduinoOTA service initialized on port %d\n", OTA_PORT);
#endif
}

void WifiCommunication::setupWebOTA() {
    // 1. Root diagnostic info
    _webServer.on("/", HTTP_GET, [this]() {
        char buf[512];
        snprintf(buf, sizeof(buf),
            "{\"esp\":%d,\"hostname\":\"%s\",\"ip\":\"%s\",\"status\":\"online\","
            "\"uptime_sec\":%lu,\"ota\":\"ready\",\"web_update\":\"/update\"}",
            ESP_ID, _hostname, WiFi.localIP().toString().c_str(), millis() / 1000, WEB_OTA_PORT);
        _webServer.send(200, "application/json", buf);
    });

    // 2. Web browser OTA update form
    _webServer.on("/update", HTTP_GET, [this]() {
        String html =
            "<!DOCTYPE html><html><head><title>ESP32 Firmware Update</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<style>body{font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:30px;text-align:center;}"
            ".card{background:#1e293b;padding:24px;border-radius:12px;max-width:440px;margin:auto;box-shadow:0 4px 12px rgba(0,0,0,0.5);}"
            "input[type=file]{margin:16px 0;background:#090d16;padding:10px;border-radius:6px;width:90%;color:#94a3b8;}"
            "input[type=submit]{background:#06b6d4;color:#000;font-weight:bold;border:none;padding:12px 24px;border-radius:6px;cursor:pointer;width:100%;font-size:16px;}"
            "</style></head><body><div class='card'>"
            "<h2>ESP32 #" + String(ESP_ID) + " OTA Deploy</h2>"
            "<p>Select firmware <code>.bin</code> file to flash over Wi-Fi:</p>"
            "<form method='POST' action='/update' enctype='multipart/form-data'>"
            "<input type='file' name='update' accept='.bin' required><br>"
            "<input type='submit' value='Upload & Flash Firmware'>"
            "</form></div></body></html>";
        _webServer.send(200, "text/html", html);
    });

    // 3. Web browser OTA POST handler
    _webServer.on("/update", HTTP_POST, [this]() {
        _webServer.sendHeader("Connection", "close");
        _webServer.send(200, "text/plain", (Update.hasError()) ? "UPDATE FAILED" : "UPDATE SUCCESS! Rebooting...");
        delay(500);
        ESP.restart();
    }, [this]() {
        HTTPUpload& upload = _webServer.upload();
        if (upload.status == UPLOAD_FILE_START) {
            Serial.printf("[WEB OTA] Receiving firmware: %s\n", upload.filename.c_str());
            if (!Update.begin(UPDATE_SIZE_UNKNOWN)) {
                Update.printError(Serial);
            }
        } else if (upload.status == UPLOAD_FILE_WRITE) {
            if (Update.write(upload.buf, upload.currentSize) != upload.currentSize) {
                Update.printError(Serial);
            }
        } else if (upload.status == UPLOAD_FILE_END) {
            if (Update.end(true)) {
                Serial.printf("[WEB OTA SUCCESS] Flashed %u bytes successfully.\n", upload.totalSize);
            } else {
                Update.printError(Serial);
            }
        }
    });

    _webServer.begin();
#if DEBUG_LEVEL > 0
    Serial.printf("[WEB OTA] HTTP Web server listening on port %d (/update)\n", WEB_OTA_PORT);
#endif
}

void WifiCommunication::update() {
    // 1. Handle ArduinoOTA
    ArduinoOTA.handle();

    // 2. Handle WebOTA requests
    _webServer.handleClient();

    // 3. Reconnection watchdog (attempt reconnect every 15s if disconnected)
    if (WiFi.status() != WL_CONNECTED && millis() - _lastReconnectAttempt > 15000) {
        _lastReconnectAttempt = millis();
        if (!_apFallbackActive) {
            WiFi.reconnect();
        }
    }

    // 4. Process incoming UDP packets
    processIncomingPacket();

    // 5. Periodic heartbeat broadcast every 30s
    if (millis() - _lastStatusBroadcast > 30000) {
        _lastStatusBroadcast = millis();
        sendStatusBroadcast();
    }
}

void WifiCommunication::enqueueCommand(const JsonCommand& cmd) {
    if (_queueCount < CMD_QUEUE_SIZE) {
        _commandQueue[_queueTail] = cmd;
        _queueTail = (_queueTail + 1) % CMD_QUEUE_SIZE;
        _queueCount++;
    }
}

void WifiCommunication::parseMultiJsonPacket(const char* jsonStr) {
    if (!jsonStr) return;

    // 1. Extract target ESP ID (supports esp or esp_id)
    int esp_id = extractJsonInt(jsonStr, "esp", -1);
    if (esp_id < 0) esp_id = extractJsonInt(jsonStr, "esp_id", -1);
    if (esp_id != ESP_ID && esp_id != 0) return;

    // 2. Find moves or motors array: "moves": [ ... ]
    const char* p = strstr(jsonStr, "\"moves\"");
    if (!p) p = strstr(jsonStr, "\"motors\"");
    if (!p) return;

    const char* arrStart = strchr(p, '[');
    if (!arrStart) return;

    // 3. Extract top-level instructionID as fallback
    char topInstId[48];
    topInstId[0] = '\0';
    if (!extractJsonString(jsonStr, "instructionID", topInstId, sizeof(topInstId))) {
        if (!extractJsonString(jsonStr, "instruction_id", topInstId, sizeof(topInstId))) {
            extractJsonString(jsonStr, "id", topInstId, sizeof(topInstId));
        }
    }

    // 4. Iterate over each { ... } object inside the array
    const char* cur = arrStart + 1;
    while (*cur && *cur != ']') {
        const char* objStart = strchr(cur, '{');
        if (!objStart) break;
        const char* objEnd = strchr(objStart, '}');
        if (!objEnd) break;

        size_t objLen = (size_t)(objEnd - objStart + 1);
        char objBuf[256];
        if (objLen < sizeof(objBuf)) {
            memcpy(objBuf, objStart, objLen);
            objBuf[objLen] = '\0';

            JsonCommand cmd;
            memset(&cmd, 0, sizeof(cmd));
            cmd.esp_id = esp_id;
            cmd.motor_id = extractJsonInt(objBuf, "motor", 0);
            if (cmd.motor_id == 0) cmd.motor_id = extractJsonInt(objBuf, "m", 0);

            cmd.pulse = extractJsonInt(objBuf, "pulse", 0);
            if (cmd.pulse == 0) cmd.pulse = extractJsonInt(objBuf, "p", 0);
            if (cmd.pulse == 0) cmd.pulse = extractJsonInt(objBuf, "pulses", 0);

            cmd.dir = extractJsonInt(objBuf, "dir", 0);
            if (cmd.dir != 0 && cmd.dir != 1) cmd.dir = extractJsonInt(objBuf, "d", 0);

            if (!extractJsonString(objBuf, "instructionID", cmd.instruction_id, sizeof(cmd.instruction_id))) {
                if (!extractJsonString(objBuf, "id", cmd.instruction_id, sizeof(cmd.instruction_id))) {
                    if (strlen(topInstId) > 0) {
                        snprintf(cmd.instruction_id, sizeof(cmd.instruction_id), "%s_m%d", topInstId, cmd.motor_id);
                    } else {
                        snprintf(cmd.instruction_id, sizeof(cmd.instruction_id), "multi_m%d_%lu", cmd.motor_id, millis());
                    }
                }
            }

            strncpy(cmd.cmd, "MOVE", sizeof(cmd.cmd) - 1);
            cmd.valid = (cmd.motor_id >= 1 && cmd.motor_id <= NUM_MOTORS);

            if (cmd.valid) {
                enqueueCommand(cmd);
#if DEBUG_LEVEL > 0
                Serial.printf("[UDP RX MULTI] Enqueued: ESP=%d | Motor=%d | Pulse=%d | Dir=%d | ID=%s\n",
                              cmd.esp_id, cmd.motor_id, cmd.pulse, cmd.dir, cmd.instruction_id);
#endif
            }
        }

        cur = objEnd + 1;
    }
}

void WifiCommunication::processIncomingPacket() {
    int packetSize = 0;
    while ((packetSize = _udp.parsePacket()) > 0) {
        _lastSenderIP = _udp.remoteIP();
        _lastSenderPort = _udp.remotePort();

        // 1. Drop packets from our own IP to prevent self-broadcast reflection loops
        if (_lastSenderIP == WiFi.localIP()) {
            _udp.flush();
            continue;
        }

        int len = _udp.read(_incomingBuffer, sizeof(_incomingBuffer) - 1);
        if (len <= 0) continue;
        _incomingBuffer[len] = '\0';

        // 2. Ignore telemetry/status/feedback broadcasts (they are outputs, not commands)
        if (strstr(_incomingBuffer, "\"status\"") != NULL && strstr(_incomingBuffer, "\"cmd\"") == NULL) {
            continue;
        }

        // 3. Runtime motors packet check (per-motor brake profile)
        if (strstr(_incomingBuffer, "\"runtime_motors\"") != NULL) {
            trigger_instruction_double_blink();
            parseRuntimeMotorsPacket(_incomingBuffer);
            continue;
        }

        // 4. Multi-motor packet check
        const char* movesPtr = strstr(_incomingBuffer, "\"moves\"");
        if (!movesPtr) movesPtr = strstr(_incomingBuffer, "\"motors\"");

        if (movesPtr != NULL) {
            parseMultiJsonPacket(_incomingBuffer);
        } else {
            // Standard single-motor command packet
            JsonCommand cmd;
            if (parseJsonPacket(_incomingBuffer, cmd)) {
                if (cmd.esp_id == ESP_ID || cmd.esp_id == 0) {
                    enqueueCommand(cmd);
#if DEBUG_LEVEL > 0
                    Serial.println("\n--------------------------------------------------");
                    Serial.printf("[UDP RX] JSON Packet from %s:%d addressed to THIS ESP:\n",
                                  _lastSenderIP.toString().c_str(), _lastSenderPort);
                    Serial.printf("  Raw JSON: %s\n", _incomingBuffer);
                    Serial.printf("  ESP=%d | Motor=%d | Pulse=%d | Dir=%d | ID=%s | Cmd=%s (Queue: %d)\n",
                                  cmd.esp_id, cmd.motor_id, cmd.pulse, cmd.dir, cmd.instruction_id, cmd.cmd, _queueCount);
                    Serial.println("--------------------------------------------------");
#endif
                }
            }
        }
    }
}

bool WifiCommunication::parseJsonPacket(const char* jsonStr, JsonCommand& cmd) {
    if (!jsonStr || strlen(jsonStr) < 5) return false;
    memset(&cmd, 0, sizeof(cmd));

    // Extract ESP ID
    cmd.esp_id = extractJsonInt(jsonStr, "esp", -1);
    if (cmd.esp_id < 0) {
        cmd.esp_id = extractJsonInt(jsonStr, "esp_id", -1);
    }

    // Extract Motor ID (supports 1..6 or 0..5)
    cmd.motor_id = extractJsonInt(jsonStr, "motor", 1);

    // Extract Pulses
    cmd.pulse = extractJsonInt(jsonStr, "pulse", 0);
    if (cmd.pulse == 0) {
        cmd.pulse = extractJsonInt(jsonStr, "pulses", 0);
    }

    // Extract Direction
    cmd.dir = extractJsonInt(jsonStr, "dir", 0);
    if (cmd.dir != 0 && cmd.dir != 1) {
        cmd.dir = extractJsonInt(jsonStr, "direction", 0);
    }

    // Extract Instruction ID
    if (!extractJsonString(jsonStr, "instructionID", cmd.instruction_id, sizeof(cmd.instruction_id))) {
        if (!extractJsonString(jsonStr, "instruction_id", cmd.instruction_id, sizeof(cmd.instruction_id))) {
            extractJsonString(jsonStr, "id", cmd.instruction_id, sizeof(cmd.instruction_id));
        }
    }

    // Extract Command Verb
    if (!extractJsonString(jsonStr, "cmd", cmd.cmd, sizeof(cmd.cmd))) {
        if (cmd.pulse > 0) {
            strncpy(cmd.cmd, "MOVE", sizeof(cmd.cmd) - 1);
        } else {
            // No cmd and no pulse > 0 -> NOT a command packet (telemetry/broadcast)
            return false;
        }
    }

    // Valid if esp_id is specified (0..5)
    cmd.valid = (cmd.esp_id >= 0 && cmd.esp_id <= 5);
    return cmd.valid;
}

bool WifiCommunication::hasCommand() {
    return _queueCount > 0;
}

bool WifiCommunication::getCommand(JsonCommand& out_cmd) {
    if (_queueCount == 0) return false;
    out_cmd = _commandQueue[_queueHead];
    _queueHead = (_queueHead + 1) % CMD_QUEUE_SIZE;
    _queueCount--;
    return true;
}

bool WifiCommunication::sendFeedback(int esp_id, int motor_id, int32_t totalpulse, const char* instruction_id, const char* status) {
    char packet[320];
#if ESP_ID != 3
    snprintf(packet, sizeof(packet),
             "{\"esp\":%d,\"motor\":%d,\"totalpulse\":%ld,\"instructionID\":\"%s\",\"status\":\"%s\",\"edges\":[%lu,%lu,%lu,%lu,%lu,%lu]}",
             esp_id, motor_id, (long)totalpulse,
             (instruction_id && strlen(instruction_id) > 0) ? instruction_id : "none",
             (status && strlen(status) > 0) ? status : "completed",
             (unsigned long)g_raw_edges[0], (unsigned long)g_raw_edges[1],
             (unsigned long)g_raw_edges[2], (unsigned long)g_raw_edges[3],
             (unsigned long)g_raw_edges[4], (unsigned long)g_raw_edges[5]);
#else
    snprintf(packet, sizeof(packet),
             "{\"esp\":%d,\"motor\":%d,\"totalpulse\":%ld,\"instructionID\":\"%s\",\"status\":\"%s\"}",
             esp_id, motor_id, (long)totalpulse,
             (instruction_id && strlen(instruction_id) > 0) ? instruction_id : "none",
             (status && strlen(status) > 0) ? status : "completed");
#endif

#if DEBUG_LEVEL > 0
    Serial.printf("[UDP TX FEEDBACK] -> %s\n", packet);
#endif

    // Send via UDP broadcast so Pi and any monitoring PC receive it
    _udp.beginPacket(IPAddress(255, 255, 255, 255), WIFI_UDP_PORT);
    _udp.write((const uint8_t*)packet, strlen(packet));
    bool ok = _udp.endPacket();

    // Also send direct unicast to the last sender if it was not broadcast
    if (_lastSenderIP != IPAddress(255, 255, 255, 255) && _lastSenderIP != IPAddress(0, 0, 0, 0)) {
        _udp.beginPacket(_lastSenderIP, _lastSenderPort);
        _udp.write((const uint8_t*)packet, strlen(packet));
        _udp.endPacket();
    }

    return ok;
}

bool WifiCommunication::sendDoneFeedback(int esp_id, int motor_id, uint32_t pulses, uint32_t running_time_ms, const char* instruction_id) {
    char packet[256];
    snprintf(packet, sizeof(packet),
             "{\"esp\":%d,\"motor\":%d,\"status\":\"done\",\"pulses\":%lu,\"running_time_ms\":%lu,\"instructionID\":\"%s\"}",
             esp_id, motor_id, (unsigned long)pulses, (unsigned long)running_time_ms,
             (instruction_id && strlen(instruction_id) > 0) ? instruction_id : "none");

#if DEBUG_LEVEL > 0
    Serial.printf("[UDP TX DONE] -> %s\n", packet);
#endif

    _udp.beginPacket(IPAddress(255, 255, 255, 255), WIFI_UDP_PORT);
    _udp.write((const uint8_t*)packet, strlen(packet));
    bool ok = _udp.endPacket();

    if (_lastSenderIP != IPAddress(255, 255, 255, 255) && _lastSenderIP != IPAddress(0, 0, 0, 0)) {
        _udp.beginPacket(_lastSenderIP, _lastSenderPort);
        _udp.write((const uint8_t*)packet, strlen(packet));
        _udp.endPacket();
    }
    return ok;
}

bool WifiCommunication::sendJamFeedback(int esp_id, int motor_id, uint32_t pulses, uint32_t running_time_ms, const char* section) {
    char packet[256];
    if (section == nullptr || strcmp(section, "none") == 0) {
        snprintf(packet, sizeof(packet),
                 "{\"esp\":%d,\"motor\":%d,\"status\":\"error\",\"reason\":\"jam\",\"pulses\":%lu,\"running_time_ms\":%lu,\"section\":null}",
                 esp_id, motor_id, (unsigned long)pulses, (unsigned long)running_time_ms);
    } else {
        snprintf(packet, sizeof(packet),
                 "{\"esp\":%d,\"motor\":%d,\"status\":\"error\",\"reason\":\"jam\",\"pulses\":%lu,\"running_time_ms\":%lu,\"section\":\"%s\"}",
                 esp_id, motor_id, (unsigned long)pulses, (unsigned long)running_time_ms, section);
    }

#if DEBUG_LEVEL > 0
    Serial.printf("[UDP TX JAM] -> %s\n", packet);
#endif

    _udp.beginPacket(IPAddress(255, 255, 255, 255), WIFI_UDP_PORT);
    _udp.write((const uint8_t*)packet, strlen(packet));
    bool ok = _udp.endPacket();

    if (_lastSenderIP != IPAddress(255, 255, 255, 255) && _lastSenderIP != IPAddress(0, 0, 0, 0)) {
        _udp.beginPacket(_lastSenderIP, _lastSenderPort);
        _udp.write((const uint8_t*)packet, strlen(packet));
        _udp.endPacket();
    }
    return ok;
}

bool WifiCommunication::sendError(int esp_id, int motor_id, const char* reason, const char* message) {
    char packet[256];
    snprintf(packet, sizeof(packet),
             "{\"esp\":%d,\"motor\":%d,\"status\":\"error\",\"reason\":\"%s\",\"message\":\"%s\"}",
             esp_id, motor_id,
             reason ? reason : "error",
             message ? message : "");

#if DEBUG_LEVEL > 0
    Serial.printf("[UDP TX ERROR] -> %s\n", packet);
#endif

    _udp.beginPacket(IPAddress(255, 255, 255, 255), WIFI_UDP_PORT);
    _udp.write((const uint8_t*)packet, strlen(packet));
    bool ok = _udp.endPacket();

    if (_lastSenderIP != IPAddress(255, 255, 255, 255) && _lastSenderIP != IPAddress(0, 0, 0, 0)) {
        _udp.beginPacket(_lastSenderIP, _lastSenderPort);
        _udp.write((const uint8_t*)packet, strlen(packet));
        _udp.endPacket();
    }
    return ok;
}

void WifiCommunication::parseRuntimeMotorsPacket(const char* jsonStr) {
    if (!jsonStr) return;

    // Check optional ESP filter: if esp specified and != ESP_ID and != 0, ignore
    int esp_id = extractJsonInt(jsonStr, "esp", -1);
    if (esp_id < 0) esp_id = extractJsonInt(jsonStr, "esp_id", -1);
    if (esp_id > 0 && esp_id != ESP_ID) return;

    char topInstId[48];
    topInstId[0] = '\0';
    if (!extractJsonString(jsonStr, "instructionID", topInstId, sizeof(topInstId))) {
        extractJsonString(jsonStr, "instruction_id", topInstId, sizeof(topInstId));
    }

    const char* p = strstr(jsonStr, "\"runtime_motors\"");
    if (!p) return;
    const char* arrStart = strchr(p, '[');
    if (!arrStart) return;

    // Iterate through each motor object in array
    const char* cur = arrStart + 1;
    while (*cur && *cur != ']') {
        const char* objStart = strchr(cur, '{');
        if (!objStart) break;

        // Find end of motor object, accounting for nested braces in profile
        int braceDepth = 0;
        const char* objEnd = nullptr;
        for (const char* s = objStart; *s; ++s) {
            if (*s == '{') braceDepth++;
            else if (*s == '}') {
                braceDepth--;
                if (braceDepth == 0) {
                    objEnd = s;
                    break;
                }
            }
        }
        if (!objEnd) break;

        size_t objLen = (size_t)(objEnd - objStart + 1);
        char motorBuf[1024];
        if (objLen < sizeof(motorBuf)) {
            memcpy(motorBuf, objStart, objLen);
            motorBuf[objLen] = '\0';

            int motor_id = extractJsonInt(motorBuf, "motor", 0);
            int dir = extractJsonInt(motorBuf, "dir", 0);
            int pulse = extractJsonInt(motorBuf, "pulse", 0);

            MotorProfile prof;
            memset(&prof, 0, sizeof(prof));

            // Parse profile sections if present
            const char* profPtr = strstr(motorBuf, "\"profile\"");
            if (profPtr) {
                const char* profBrace = strchr(profPtr, '{');
                if (profBrace && profBrace < objEnd) {
                    // Find sections inside profile
                    const char* secCur = profBrace + 1;
                    while (*secCur && *secCur != '}' && prof.section_count < MAX_PROFILE_SECTIONS) {
                        const char* secStart = strchr(secCur, '{');
                        if (!secStart) break;

                        // Find section name preceding the '{'
                        char secName[8] = "A";
                        const char* kEnd = secStart - 1;
                        while (kEnd > secCur && (*kEnd == ' ' || *kEnd == ':' || *kEnd == '\t' || *kEnd == '\r' || *kEnd == '\n')) kEnd--;
                        if (*kEnd == '"') {
                            const char* kStart = kEnd - 1;
                            while (kStart > secCur && *kStart != '"') kStart--;
                            if (*kStart == '"') {
                                size_t kLen = (size_t)(kEnd - kStart - 1);
                                if (kLen > 0 && kLen < sizeof(secName)) {
                                    memcpy(secName, kStart + 1, kLen);
                                    secName[kLen] = '\0';
                                }
                            }
                        }

                        const char* secEnd = strchr(secStart, '}');
                        if (!secEnd) break;

                        size_t secLen = (size_t)(secEnd - secStart + 1);
                        char secBuf[256];
                        if (secLen < sizeof(secBuf)) {
                            memcpy(secBuf, secStart, secLen);
                            secBuf[secLen] = '\0';

                            BrakeSection& sec = prof.sections[prof.section_count];
                            strncpy(sec.name, secName, sizeof(sec.name) - 1);

                            char dtype[16];
                            dtype[0] = '\0';
                            extractJsonString(secBuf, "duration_type", dtype, sizeof(dtype));
                            if (strcmp(dtype, "time") == 0) {
                                sec.duration_type = DURATION_TIME;
                            } else {
                                sec.duration_type = DURATION_PULSE;
                            }

                            sec.start = extractJsonInt(secBuf, "start", 0);
                            sec.end = extractJsonInt(secBuf, "end", 0);
                            sec.brake_count = extractJsonInt(secBuf, "brake_count", 0);
                            sec.brake_ms = extractJsonInt(secBuf, "brake_ms", 0);
                            sec.active = (sec.brake_count > 0 && sec.end > sec.start);
                            prof.section_count++;
                        }
                        secCur = secEnd + 1;
                    }
                }
            }

            // Dispatch to motor
            int mIdx = motor_id - 1;
            MotorController* mc = get_motor(mIdx);
            if (mc != nullptr) {
                char err[64];
                err[0] = '\0';
                char mInstId[48];
                if (strlen(topInstId) > 0) {
                    snprintf(mInstId, sizeof(mInstId), "%s_m%d", topInstId, motor_id);
                } else {
                    snprintf(mInstId, sizeof(mInstId), "prof_m%d_%lu", motor_id, millis());
                }

                bool ok = mc->startProfile(pulse, dir, prof, mInstId, err, sizeof(err));
                if (!ok) {
                    sendError(ESP_ID, motor_id, "invalid_config", err);
                }
            } else {
                sendError(ESP_ID, motor_id, "invalid_motor", "motor ID out of range for board");
            }
        }
        cur = objEnd + 1;
    }
}

bool WifiCommunication::sendStatusBroadcast() {
    char packet[256];
    snprintf(packet, sizeof(packet),
             "{\"esp\":%d,\"hostname\":\"%s\",\"ip\":\"%s\",\"status\":\"online\",\"uptime_ms\":%lu,\"ota\":true}",
             ESP_ID, _hostname, WiFi.localIP().toString().c_str(), millis());

    _udp.beginPacket(IPAddress(255, 255, 255, 255), WIFI_UDP_PORT);
    _udp.write((const uint8_t*)packet, strlen(packet));
    return _udp.endPacket();
}
