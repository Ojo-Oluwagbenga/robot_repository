/**
 * ESP32 Wi-Fi & OTA Motor Controller Firmware - BOARD 4 (Leg Driving Board)
 * Arduino IDE Sketch: esp4_leg_controller.ino
 *
 * Hardware Configuration:
 * - Motor 1 (B1_front): IN1 = GPIO 25, IN2 = GPIO 26 | Encoder Pulse = GPIO 15
 * - Motor 2 (B1_rear) : IN1 = GPIO 32, IN2 = GPIO 33 | Encoder Pulse = GPIO 16
 * - Motor 3 (B2_front): IN1 = GPIO 14, IN2 = GPIO 27 | Encoder Pulse = GPIO 34
 * - Motor 4 (B2_rear) : IN1 = GPIO 12, IN2 = GPIO 13 | Encoder Pulse = GPIO 35
 * - Visual Signalling : Onboard Blue LED = GPIO 2 (Double-blinks on instructions)
 *
 * Connectivity & Updates:
 * - Controls 4 motors over Wi-Fi UDP JSON Packet Protocol (Port 5005).
 * - Over-The-Air (OTA) updates via ArduinoOTA (Port 3232) and WebOTA (Port 80 at http://<IP>/update).
 * - Automatic Fallback Access Point: ESP32-Robot-4-AP (pass: robot1234) if station Wi-Fi fails.
 */

#include <Arduino.h>
#include "config.h"
#include "wifi_communication.h"
#include "encoder.h"
#include "motor.h"

// Quick motor indexing array for the 4 leg drive motors
static MotorController* motors[NUM_MOTORS] = {
    &motor1,
    &motor2,
    &motor3,
    &motor4
};

// =============================================================================
// Visual LED Indicator (Safely guarded - disabled when PIN_LED < 0)
// =============================================================================
#if defined(PIN_LED) && PIN_LED >= 0
static uint8_t instruction_blink_remaining = 0;
static uint32_t next_blink_toggle_time = 0;
static const uint32_t BLINK_STEP_MS = 50;

void trigger_instruction_double_blink() {
    instruction_blink_remaining = 4;
    digitalWrite(PIN_LED, HIGH);
    next_blink_toggle_time = millis() + BLINK_STEP_MS;
}

static void update_led() {
    if (instruction_blink_remaining > 0) {
        if (millis() >= next_blink_toggle_time) {
            instruction_blink_remaining--;
            if (instruction_blink_remaining == 0) {
                digitalWrite(PIN_LED, LOW);
            } else if (instruction_blink_remaining % 2 == 1) {
                digitalWrite(PIN_LED, LOW);
                next_blink_toggle_time = millis() + BLINK_STEP_MS;
            } else {
                digitalWrite(PIN_LED, HIGH);
                next_blink_toggle_time = millis() + BLINK_STEP_MS;
            }
        }
    } else {
        digitalWrite(PIN_LED, LOW);
    }
}
#else
void trigger_instruction_double_blink() {
    // No-op: GPIO 2 is dedicated to Motor 2's encoder
}
static void update_led() {
    // No-op
}
#endif

// =============================================================================
// SETUP - First Run USB Flash Entry Point
// =============================================================================
void setup() {
#if DEBUG_LEVEL > 0
    Serial.begin(SERIAL_BAUD_RATE);
    delay(200);
    Serial.println();
    Serial.println("==================================================");
    Serial.printf("ESP32 MOTOR CONTROLLER - BOARD ID: %d (LEG DRIVING BOARD)\n", ESP_ID);
    Serial.printf("Total Motors      : %d (4 Encoded Channels)\n", NUM_MOTORS);
    Serial.printf("Communication Mode: WI-FI UDP JSON PACKET PROTOCOL (Port %d)\n", WIFI_UDP_PORT);
    Serial.printf("OTA Updates       : ENABLED (Port %d & Web Port %d)\n", OTA_PORT, WEB_OTA_PORT);
    Serial.println("==================================================");
#endif

#if defined(PIN_LED) && PIN_LED >= 0
    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, LOW);
#endif

    // 1. Initialize motors with safe LOW pin state on boot
    init_all_motors();

    // 2. Initialize pulse counter interrupts on GPIOs 15, 2, 34, 35
    init_all_encoders();

    // 3. Connect to Wi-Fi, initialize ArduinoOTA, WebOTA server, and UDP listener
    wifiComm.begin();

#if DEBUG_LEVEL > 0
    Serial.println("ESP #4 Ready! Listening for motor commands over Wi-Fi...");
#endif
}

// =============================================================================
// MAIN EXECUTION LOOP
// =============================================================================
void loop() {
    // 1. Maintain Wi-Fi connection, ArduinoOTA handler, and Web OTA requests
    wifiComm.update();

    // 2. Process received JSON commands from Raspberry Pi / PC
    while (wifiComm.hasCommand()) {
        JsonCommand cmd;
        if (wifiComm.getCommand(cmd)) {
            // Case A: Status Ping
            if (strcmp(cmd.cmd, "STATUS") == 0 || strcmp(cmd.cmd, "PING") == 0) {
#if DEBUG_LEVEL > 0
                Serial.printf("[JSON CMD] STATUS / PING REQUEST (ID: %s)\n", cmd.instruction_id);
#endif
                wifiComm.sendStatusBroadcast();
                wifiComm.sendFeedback(ESP_ID, 0, 0, cmd.instruction_id, "online");
            }

            // Case B: Emergency Stop Command
            else if (strcmp(cmd.cmd, "STOP") == 0) {
                trigger_instruction_double_blink();
#if DEBUG_LEVEL > 0
                Serial.printf("[JSON CMD] STOP ALL MOTORS! (ID: %s)\n", cmd.instruction_id);
#endif
                release_any_active_lock();
                for (uint8_t m = 0; m < NUM_MOTORS; ++m) {
                    motors[m]->brake();
                }
                wifiComm.sendFeedback(ESP_ID, 0, 0, cmd.instruction_id, "stopped");
            }

            // Case C: Lock-On Continuous Drive Mode
            else if (strcmp(cmd.cmd, "LOCK_ON") == 0) {
                trigger_instruction_double_blink();
                int mIdx = cmd.motor_id - 1;
                if (mIdx >= 0 && mIdx < NUM_MOTORS) {
#if DEBUG_LEVEL > 0
                    Serial.printf("[JSON CMD] LOCK-ON for Motor %d: dir=%d, ID=%s\n",
                                  cmd.motor_id, cmd.dir, cmd.instruction_id);
#endif
                    release_any_active_lock();
                    motors[mIdx]->startLockOn(cmd.dir, cmd.instruction_id);
                }
            }

            // Case D: Clear / Release Lock-On Mode
            else if (strcmp(cmd.cmd, "CLEAR_LOCK") == 0) {
                trigger_instruction_double_blink();
#if DEBUG_LEVEL > 0
                Serial.printf("[JSON CMD] CLEAR LOCK-ON (ID: %s)\n", cmd.instruction_id);
#endif
                release_any_active_lock();
            }

            // Case E: Pin Diagnostics
            else if (strcmp(cmd.cmd, "PIN_DIAG") == 0) {
                char pbuf[320];
                snprintf(pbuf, sizeof(pbuf),
                    "{\"m1_in\":[%d,%d],\"m1_enc\":%d,\"m2_in\":[%d,%d],\"m2_enc\":%d,\"m3_in\":[%d,%d],\"m3_enc\":%d,\"m4_in\":[%d,%d],\"m4_enc\":%d}",
                    digitalRead(PIN_M1_IN1), digitalRead(PIN_M1_IN2), digitalRead(PIN_M1_ENC_PULSE),
                    digitalRead(PIN_M2_IN1), digitalRead(PIN_M2_IN2), digitalRead(PIN_M2_ENC_PULSE),
                    digitalRead(PIN_M3_IN1), digitalRead(PIN_M3_IN2), digitalRead(PIN_M3_ENC_PULSE),
                    digitalRead(PIN_M4_IN1), digitalRead(PIN_M4_IN2), digitalRead(PIN_M4_ENC_PULSE));
                wifiComm.sendFeedback(ESP_ID, 0, 0, cmd.instruction_id, pbuf);
            }

            // Case F: Direct Pin Write Hardware Test
            else if (strcmp(cmd.cmd, "PIN_WRITE") == 0) {
                int target_pin = cmd.motor_id;
                int pin_val = (cmd.dir != 0) ? HIGH : LOW;
                pinMode(target_pin, OUTPUT);
                digitalWrite(target_pin, pin_val);
                char resp_buf[64];
                snprintf(resp_buf, sizeof(resp_buf), "pin_%d_val_%d", target_pin, pin_val);
                wifiComm.sendFeedback(ESP_ID, target_pin, digitalRead(target_pin), cmd.instruction_id, resp_buf);
            }

            // Case G: Normal Encoded Movement Command
            else {
                trigger_instruction_double_blink();
                int mIdx = cmd.motor_id - 1;
                if (mIdx >= 0 && mIdx < NUM_MOTORS) {
#if DEBUG_LEVEL > 0
                    Serial.printf("[JSON CMD] MOVE -> Motor %d: %d pulses, dir=%d, ID=%s\n",
                                  cmd.motor_id, cmd.pulse, cmd.dir, cmd.instruction_id);
#endif
                    motors[mIdx]->startNormalCommand(cmd.pulse, cmd.dir, cmd.instruction_id);
                } else {
#if DEBUG_LEVEL > 0
                    Serial.printf("[JSON CMD ERROR] Invalid Motor ID %d for ESP #4 (must be 1 to 4)\n", cmd.motor_id);
#endif
                    wifiComm.sendFeedback(ESP_ID, cmd.motor_id, 0, cmd.instruction_id, "error_invalid_motor");
                }
            }
        }
    }

    // 3. Update active motor PID / count tracking and safety watchdogs
    update_all_motors(false);

    // 4. Update LED indicator
    update_led();
}
