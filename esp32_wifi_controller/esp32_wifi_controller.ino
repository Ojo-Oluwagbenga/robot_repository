/**
 * ESP32 Wi-Fi & OTA Motor Controller Firmware for Humanoid Robot.
 * Arduino IDE Sketch: esp32_wifi_controller.ino
 *
 * Architecture:
 * - ESP 1: Right Arm / Body -> 6 Encoded DC Motors (Single pulse pins 35, 34, 23, 22, 18, 16)
 * - ESP 2: Left Arm / Body  -> 6 Encoded DC Motors (Single pulse pins 35, 34, 23, 22, 18, 16)
 * - ESP 3: Head / Gripper   -> 2 Ordinary DC Motors with Limit Switches (Pins 26 and 25, active HIGH)
 * - Controls motors over Wi-Fi UDP JSON Packet Protocol.
 * - Supports Over-The-Air (OTA) updates via ArduinoOTA (port 3232) and WebOTA (port 80).
 * - Blue LED (GPIO 2) double-blinks briefly on instruction, driven LOW otherwise.
 */

#include <Arduino.h>
#include "config.h"
#include "wifi_communication.h"
#include "encoder.h"
#include "motor.h"
#include "esp_timer.h"

// Quick motor indexing array
static MotorController* motors[NUM_MOTORS] = {
#if NUM_MOTORS >= 1
    &motor1,
#endif
#if NUM_MOTORS >= 2
    &motor2,
#endif
#if NUM_MOTORS >= 3
    &motor3,
#endif
#if NUM_MOTORS >= 4
    &motor4,
#endif
#if NUM_MOTORS >= 5
    &motor5,
#endif
#if NUM_MOTORS >= 6
    &motor6,
#endif
};

static void IRAM_ATTR motor_timer_callback(void* arg) {
    (void)arg;
    for (uint8_t m = 0; m < NUM_MOTORS; ++m) {
        motors[m]->onTimer1ms();
    }
}

// =============================================================================
// Visual LED Indicator (Onboard Blue LED on GPIO 2)
// =============================================================================
// Non-blocking double blink sequence: ON (50ms) -> OFF (50ms) -> ON (50ms) -> OFF
// Driven LOW at all other times.
static uint8_t instruction_blink_remaining = 0;
static uint32_t next_blink_toggle_time = 0;
static const uint32_t BLINK_STEP_MS = 50;

// Trigger two crisp, distinct blue LED blinks on receiving any instruction packet
void trigger_instruction_double_blink() {
    instruction_blink_remaining = 4; // 4 transitions: ON, OFF, ON, OFF
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
                // Odd remaining (3, 1): turn OFF
                digitalWrite(PIN_LED, LOW);
                next_blink_toggle_time = millis() + BLINK_STEP_MS;
            } else {
                // Even remaining (2): turn ON for 2nd pulse
                digitalWrite(PIN_LED, HIGH);
                next_blink_toggle_time = millis() + BLINK_STEP_MS;
            }
        }
    } else {
        // Enforce: strictly driven LOW when idle / on standby
        digitalWrite(PIN_LED, LOW);
    }
}

// =============================================================================
// SETUP
// =============================================================================
void setup() {
#if DEBUG_LEVEL > 0
    Serial.begin(SERIAL_BAUD_RATE);
    delay(200);
    Serial.println();
    Serial.println("==================================================");
    Serial.printf("ESP32 MOTOR CONTROLLER - BOARD ID: %d\n", ESP_ID);
    Serial.printf("Total Motors      : %d\n", NUM_MOTORS);
    Serial.printf("Communication Mode: WI-FI UDP JSON PACKET PROTOCOL\n");
    Serial.printf("OTA Updates       : ENABLED (Port %d & Web Port %d)\n", OTA_PORT, WEB_OTA_PORT);
    Serial.println("==================================================");
#endif

    // 0. Initialize onboard visual indicator to LOW
    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, LOW);

    // 1. Initialize motors with safe boot pin state (IN1=HIGH, IN2=HIGH short brake)
    init_all_motors();

    // 2. Start 1ms high-resolution timer for non-blocking brake & jam watchdog timing
    const esp_timer_create_args_t periodic_timer_args = {
        .callback = &motor_timer_callback,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "motor_brake_timer"
    };
    esp_timer_handle_t motor_periodic_timer;
    esp_timer_create(&periodic_timer_args, &motor_periodic_timer);
    esp_timer_start_periodic(motor_periodic_timer, 1000); // 1000 us = 1 ms

    // 2. Initialize encoders (ESP 1 & ESP 2 only)
#if ESP_ID != 3
    init_all_encoders();
#endif

    // 3. Initialize Wi-Fi connection, ArduinoOTA, WebOTA, and UDP broadcast listener
    wifiComm.begin();

#if DEBUG_LEVEL > 0
    Serial.println("Initialization complete. Listening for JSON packets on Wi-Fi...");
#endif
}

// =============================================================================
// MAIN LOOP
// =============================================================================
void loop() {
    // -------------------------------------------------------------------------
    // 1. Maintain Wi-Fi connection, ArduinoOTA handler, and Web OTA requests
    // -------------------------------------------------------------------------
    wifiComm.update();

    // -------------------------------------------------------------------------
    // 2. Process Received JSON Command Packets from Raspberry Pi / PC
    // -------------------------------------------------------------------------
    while (wifiComm.hasCommand()) {
        JsonCommand cmd;
        if (wifiComm.getCommand(cmd)) {
            // Case A: Status Request / Ping (Quiet response without LED blink)
            if (strcmp(cmd.cmd, "STATUS") == 0 || strcmp(cmd.cmd, "PING") == 0) {
#if DEBUG_LEVEL > 0
                Serial.printf("[JSON CMD] STATUS / PING REQUEST (instructionID: %s)\n", cmd.instruction_id);
#endif
                wifiComm.sendStatusBroadcast();
                wifiComm.sendFeedback(ESP_ID, 0, 0, cmd.instruction_id, "online");
            }

            // Case B: Emergency Stop Command
            else if (strcmp(cmd.cmd, "STOP") == 0) {
                trigger_instruction_double_blink();
#if DEBUG_LEVEL > 0
                Serial.printf("[JSON CMD] STOP ALL MOTORS! (instructionID: %s)\n", cmd.instruction_id);
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

            // Case D: Release Lock-On Mode
            else if (strcmp(cmd.cmd, "CLEAR_LOCK") == 0) {
                trigger_instruction_double_blink();
#if DEBUG_LEVEL > 0
                Serial.printf("[JSON CMD] CLEAR LOCK-ON (instructionID: %s)\n", cmd.instruction_id);
#endif
                release_any_active_lock();
            }

            // Case E: Pin Diagnostics
            else if (strcmp(cmd.cmd, "PIN_DIAG") == 0) {
                char pbuf[320];
#if ESP_ID != 3
                snprintf(pbuf, sizeof(pbuf),
                    "{\"p5\":%d,\"p16\":%d,\"p17\":%d,\"p18\":%d,\"p19\":%d,\"p21\":%d,\"p22\":%d,\"p23\":%d,\"p34\":%d,\"p35\":%d}",
                    digitalRead(5), digitalRead(16), digitalRead(17),
                    digitalRead(18), digitalRead(19), digitalRead(21),
                    digitalRead(22), digitalRead(23), digitalRead(34), digitalRead(35));
#else
                snprintf(pbuf, sizeof(pbuf), "{\"m1_lim\":%d,\"m2_lim\":%d}",
                    digitalRead(PIN_M1_LIMIT), digitalRead(PIN_M2_LIMIT));
#endif
                wifiComm.sendFeedback(ESP_ID, 0, 0, cmd.instruction_id, pbuf);
            }

            // Case F: Direct Pin Write for Hardware Diagnostic
            else if (strcmp(cmd.cmd, "PIN_WRITE") == 0) {
                int target_pin = cmd.motor_id;
                int pin_val = (cmd.dir != 0) ? HIGH : LOW;
                pinMode(target_pin, OUTPUT);
                digitalWrite(target_pin, pin_val);
                char resp_buf[64];
                snprintf(resp_buf, sizeof(resp_buf), "pin_%d_val_%d", target_pin, pin_val);
                wifiComm.sendFeedback(ESP_ID, target_pin, digitalRead(target_pin), cmd.instruction_id, resp_buf);
            }

            // Case G: Normal Motor Movement (Target Pulses / Duration & Direction)
            else {
                trigger_instruction_double_blink();
                int mIdx = cmd.motor_id - 1;
                if (mIdx >= 0 && mIdx < NUM_MOTORS) {
#if DEBUG_LEVEL > 0
                    Serial.printf("[JSON CMD] MOVE -> Motor %d: %d pulses/duration, dir=%d, ID=%s\n",
                                  cmd.motor_id, cmd.pulse, cmd.dir, cmd.instruction_id);
#endif
                    motors[mIdx]->startNormalCommand(cmd.pulse, cmd.dir, cmd.instruction_id);
                } else {
#if DEBUG_LEVEL > 0
                    Serial.printf("[JSON CMD ERROR] Invalid Motor ID %d for ESP #%d (must be 1 to %d)\n",
                                  cmd.motor_id, ESP_ID, NUM_MOTORS);
#endif
                    wifiComm.sendFeedback(ESP_ID, cmd.motor_id, 0, cmd.instruction_id, "error_invalid_motor");
                }
            }
        }
    }

    // -------------------------------------------------------------------------
    // 3. Non-blocking Motor State Updates & Limit Switch Monitoring
    // -------------------------------------------------------------------------
    update_all_motors(false);

    // -------------------------------------------------------------------------
    // 4. Update Visual LED Indicator (Enforces LOW on standby)
    // -------------------------------------------------------------------------
    update_led();
}
