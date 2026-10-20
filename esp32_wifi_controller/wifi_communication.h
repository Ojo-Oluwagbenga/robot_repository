#ifndef WIFI_COMMUNICATION_H
#define WIFI_COMMUNICATION_H

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoOTA.h>
#include <WebServer.h>
#include <Update.h>
#include <ESPmDNS.h>
#include "config.h"

// Structure representing parsed incoming JSON command from Pi
struct JsonCommand {
    int esp_id;              // Target ESP board ID (1..4, or 0 for broadcast)
    int motor_id;            // Target Motor number (1..4)
    int pulse;               // Encoder pulse target
    int dir;                 // Direction (0 = CW / forward, 1 = CCW / reverse)
    char instruction_id[48]; // Unique transaction ID (e.g. "12DE4")
    char cmd[24];            // Command verb ("MOVE", "STOP", "STATUS", "LOCK_ON")
    bool valid;
};

// Structure representing outgoing JSON feedback to Pi
struct JsonFeedback {
    int esp_id;
    int motor_id;
    int32_t totalpulse;
    char instruction_id[48];
    char status[24];         // "completed", "running", "stalled", "error"
};

class WifiCommunication {
public:
    WifiCommunication();

    void begin();
    void update();

    // Check if a new command addressed to this ESP (or broadcast) has arrived
    bool hasCommand();
    bool getCommand(JsonCommand& out_cmd);

    // Send JSON feedback packet back to the Pi / network broadcast
    bool sendFeedback(int esp_id, int motor_id, int32_t totalpulse, const char* instruction_id, const char* status);
    bool sendDoneFeedback(int esp_id, int motor_id, uint32_t pulses, uint32_t running_time_ms, const char* instruction_id = "");
    bool sendJamFeedback(int esp_id, int motor_id, uint32_t pulses, uint32_t running_time_ms, const char* section);
    bool sendError(int esp_id, int motor_id, const char* reason, const char* message);

    // Broadcast current board status & telemetry
    bool sendStatusBroadcast();

    // Connection state
    bool isConnected() const { return WiFi.status() == WL_CONNECTED; }
    IPAddress getLocalIP() const { return WiFi.localIP(); }
    const char* getHostname() const { return _hostname; }

    // Last sender IP for direct unicast responses
    IPAddress getLastSenderIP() const { return _lastSenderIP; }

private:
    WiFiUDP _udp;
    WebServer _webServer;
    char _hostname[32];
    char _incomingBuffer[1024];

    static const int CMD_QUEUE_SIZE = 16;
    JsonCommand _commandQueue[CMD_QUEUE_SIZE];
    int _queueHead;
    int _queueTail;
    int _queueCount;

    IPAddress _lastSenderIP;
    uint16_t _lastSenderPort;

    uint32_t _lastReconnectAttempt;
    uint32_t _lastStatusBroadcast;
    bool _apFallbackActive;

    void setupWiFi();
    void setupOTA();
    void setupWebOTA();
    void processIncomingPacket();
    bool parseJsonPacket(const char* jsonStr, JsonCommand& cmd);
    void parseMultiJsonPacket(const char* jsonStr);
    void parseRuntimeMotorsPacket(const char* jsonStr);
    void enqueueCommand(const JsonCommand& cmd);
    void handleRoot();
    void handleUpdatePage();
};

extern WifiCommunication wifiComm;

#endif // WIFI_COMMUNICATION_H
