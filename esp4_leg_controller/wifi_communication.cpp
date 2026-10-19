#include "wifi_communication.h"
#include "encoder.h"
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
        bool precededByQuote = (p > json && *(p - 1) == '"');
        const char* afterKey = p + klen;
        if (precededByQuote && *afterKey == '"') afterKey++;
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

    if (_udp.begin(WIFI_UDP_PORT)) {
#if DEBUG_LEVEL > 0
        Serial.printf("[WIFI] UDP broadcast listener bound to port %d\n", WIFI_UDP_PORT);
#endif
    }
    sendStatusBroadcast();
}

void WifiCommunication::setupWiFi() {
    WiFi.disconnect(true);
    delay(100);

    WiFi.mode(WIFI_STA);
    WiFi.setHostname(_hostname);

    // Disable Wi-Fi modem sleep to guarantee ultra-low UDP latency
    WiFi.setSleep(false);
    esp_wifi_set_ps(WIFI_PS_NONE);

#if DEBUG_LEVEL > 0
    Serial.println("==================================================");
    Serial.printf("[WIFI] Connecting to SSID: '%s'\n", WIFI_SSID);
    Serial.printf("[WIFI] Target Hostname    : %s\n", _hostname);
    Serial.printf("[WIFI] MAC Address        : %s\n", WiFi.macAddress().c_str());
    Serial.println("==================================================");

    WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
        if (event == ARDUINO_EVENT_WIFI_STA_CONNECTED) {
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
        Serial.println("Starting fallback AP mode...");
        Serial.println("==================================================");
#endif
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
        String type = (ArduinoOTA.getCommand() == U_FLASH) ? "sketch" : "filesystem";
        Serial.printf("\n[OTA START] Over-The-Air Update starting: %s\n", type.c_str());
#if defined(PIN_LED) && PIN_LED >= 0
        digitalWrite(PIN_LED, HIGH);
#endif
    });

    ArduinoOTA.onEnd([]() {
        Serial.println("\n[OTA END] Update complete! Rebooting ESP32...");
#if defined(PIN_LED) && PIN_LED >= 0
        digitalWrite(PIN_LED, LOW);
#endif
    });

    ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
        static uint8_t lastPct = 255;
        uint8_t pct = (progress / (total / 100));
        if (pct != lastPct && pct % 10 == 0) {
            lastPct = pct;
            Serial.printf("[OTA] Progress: %u%%\n", pct);
        }
    });

    ArduinoOTA.onError([](ota_error_t error) {
        Serial.printf("\n[OTA ERROR %u]: ", error);
        if (error == OTA_AUTH_ERROR) Serial.println("Auth Failed");
        else if (error == OTA_BEGIN_ERROR) Serial.println("Begin Failed");
        else if (error == OTA_CONNECT_ERROR) Serial.println("Connect Failed");
        else if (error == OTA_RECEIVE_ERROR) Serial.println("Receive Failed");
        else if (error == OTA_END_ERROR) Serial.println("End Failed");
#if defined(PIN_LED) && PIN_LED >= 0
        digitalWrite(PIN_LED, LOW);
#endif
    });

    ArduinoOTA.begin();
#if DEBUG_LEVEL > 0
    Serial.printf("[OTA] ArduinoOTA service initialized on port %d\n", OTA_PORT);
#endif
}

void WifiCommunication::setupWebOTA() {
    _webServer.on("/", HTTP_GET, [this]() { handleRoot(); });

    _webServer.on("/update", HTTP_GET, [this]() { handleUpdatePage(); });

    _webServer.on("/update", HTTP_POST, [this]() {
        _webServer.sendHeader("Connection", "close");
        _webServer.send(200, "text/plain", (Update.hasError()) ? "OTA UPDATE FAILED" : "OTA UPDATE SUCCESS! REBOOTING...");
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
                Serial.printf("[WEB OTA] Successfully received %u bytes. Rebooting...\n", upload.totalSize);
            } else {
                Update.printError(Serial);
            }
        }
    });

    _webServer.begin();
#if DEBUG_LEVEL > 0
    Serial.printf("[WEB OTA] HTTP Firmware Upload server ready on port %d\n", WEB_OTA_PORT);
#endif
}

void WifiCommunication::handleRoot() {
    char page[1024];
    snprintf(page, sizeof(page),
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>%s Status</title>"
        "<style>body{font-family:sans-serif;background:#121212;color:#eee;text-align:center;padding:40px;}"
        ".card{background:#1e1e1e;border-radius:12px;padding:24px;display:inline-block;box-shadow:0 8px 24px rgba(0,0,0,0.5);}"
        "h1{color:#4CAF50;}a{color:#64B5F6;text-decoration:none;font-weight:bold;}</style></head>"
        "<body><div class='card'>"
        "<h1>Robot Controller: %s</h1>"
        "<p>Board ID: <b>%d (Leg Driving Board)</b></p>"
        "<p>Motors: <b>%d Encoded Channels</b></p>"
        "<p>IP: <b>%s</b></p>"
        "<p>Uptime: <b>%lu sec</b></p>"
        "<p><a href='/update'>[ Over-The-Air Firmware Upload ]</a></p>"
        "</div></body></html>",
        _hostname, _hostname, ESP_ID, NUM_MOTORS,
        WiFi.localIP().toString().c_str(), millis() / 1000);
    _webServer.send(200, "text/html", page);
}

void WifiCommunication::handleUpdatePage() {
    const char* updateHtml =
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>ESP32 Web OTA</title>"
        "<style>body{font-family:sans-serif;background:#121212;color:#eee;text-align:center;padding:50px;}"
        ".box{background:#1e1e1e;padding:30px;border-radius:12px;display:inline-block;box-shadow:0 8px 24px rgba(0,0,0,0.5);}"
        "input[type=file]{padding:10px;margin:15px;background:#2a2a2a;color:#fff;border-radius:6px;border:1px solid #444;}"
        "input[type=submit]{background:#4CAF50;color:#fff;border:none;padding:12px 24px;border-radius:6px;font-size:16px;cursor:pointer;}"
        "input[type=submit]:hover{background:#45a049;}</style></head>"
        "<body><div class='box'>"
        "<h2>Over-The-Air Firmware Update (ESP #4)</h2>"
        "<p>Select compiled <code>.bin</code> firmware file:</p>"
        "<form method='POST' action='/update' enctype='multipart/form-data'>"
        "<input type='file' name='update' accept='.bin' required><br>"
        "<input type='submit' value='Flash Firmware Over Wi-Fi'>"
        "</form></div></body></html>";
    _webServer.send(200, "text/html", updateHtml);
}

void WifiCommunication::update() {
    ArduinoOTA.handle();
    _webServer.handleClient();

    // Reconnect Wi-Fi if lost
    if (WiFi.status() != WL_CONNECTED && !_apFallbackActive) {
        if (millis() - _lastReconnectAttempt > 10000) {
            _lastReconnectAttempt = millis();
            WiFi.reconnect();
        }
    }

    processIncomingPacket();

    // Heartbeat status broadcast every 30s
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

        // 3. Multi-motor packet check
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
                    Serial.printf("[UDP RX] JSON Packet from %s:%d addressed to ESP #%d:\n",
                                  _lastSenderIP.toString().c_str(), _lastSenderPort, ESP_ID);
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

    cmd.esp_id = extractJsonInt(jsonStr, "esp", -1);
    if (cmd.esp_id < 0) {
        cmd.esp_id = extractJsonInt(jsonStr, "esp_id", -1);
    }

    cmd.motor_id = extractJsonInt(jsonStr, "motor", 1);
    cmd.pulse = extractJsonInt(jsonStr, "pulse", 0);
    if (cmd.pulse == 0) {
        cmd.pulse = extractJsonInt(jsonStr, "pulses", 0);
    }

    cmd.dir = extractJsonInt(jsonStr, "dir", 0);
    if (cmd.dir != 0 && cmd.dir != 1) {
        cmd.dir = extractJsonInt(jsonStr, "direction", 0);
    }

    if (!extractJsonString(jsonStr, "instructionID", cmd.instruction_id, sizeof(cmd.instruction_id))) {
        if (!extractJsonString(jsonStr, "instruction_id", cmd.instruction_id, sizeof(cmd.instruction_id))) {
            extractJsonString(jsonStr, "id", cmd.instruction_id, sizeof(cmd.instruction_id));
        }
    }

    if (!extractJsonString(jsonStr, "cmd", cmd.cmd, sizeof(cmd.cmd))) {
        if (cmd.pulse > 0) {
            strncpy(cmd.cmd, "MOVE", sizeof(cmd.cmd) - 1);
        } else {
            return false;
        }
    }

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
    char payload[256];
    snprintf(payload, sizeof(payload),
             "{\"esp\": %d, \"motor\": %d, \"totalpulse\": %ld, \"instructionID\": \"%s\", \"status\": \"%s\"}",
             esp_id, motor_id, (long)totalpulse,
             (instruction_id && strlen(instruction_id) > 0) ? instruction_id : "none",
             status ? status : "completed");

    _udp.beginPacket(IPAddress(255, 255, 255, 255), WIFI_UDP_PORT);
    _udp.write((const uint8_t*)payload, strlen(payload));
    _udp.endPacket();

    if (_lastSenderIP != IPAddress(255, 255, 255, 255) && _lastSenderIP != IPAddress(0, 0, 0, 0)) {
        _udp.beginPacket(_lastSenderIP, _lastSenderPort);
        _udp.write((const uint8_t*)payload, strlen(payload));
        _udp.endPacket();
    }

#if DEBUG_LEVEL > 0
    Serial.printf("[UDP TX] Feedback: %s\n", payload);
#endif
    return true;
}

bool WifiCommunication::sendStatusBroadcast() {
    char payload[256];
    snprintf(payload, sizeof(payload),
             "{\"esp\": %d, \"hostname\": \"%s\", \"ip\": \"%s\", \"status\": \"online\", \"uptime_ms\": %lu, \"ota\": true, \"motors\": %d}",
             ESP_ID, _hostname, WiFi.localIP().toString().c_str(), millis(), NUM_MOTORS);

    _udp.beginPacket(IPAddress(255, 255, 255, 255), WIFI_UDP_PORT);
    _udp.write((const uint8_t*)payload, strlen(payload));
    _udp.endPacket();
    return true;
}
