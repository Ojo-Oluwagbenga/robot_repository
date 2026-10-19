#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

// =============================================================================
// 1. ESP32 IDENTIFICATION - BOARD 4: LEG DRIVING BOARD
// =============================================================================
// Controls 4 encoded DC motors driving the robot's base/legs:
// - Motor 1 (B1_front): Board 1 Front Drive (Pins 25, 26 | Enc 15)
// - Motor 2 (B1_rear) : Board 1 Rear Drive  (Pins 32, 33 | Enc 16)
// - Motor 3 (B2_front): Board 2 Front Drive (Pins 14, 27 | Enc 34)
// - Motor 4 (B2_rear) : Board 2 Rear Drive  (Pins 12, 13 | Enc 35)
#define ESP_ID 4
#define NUM_MOTORS 4

// =============================================================================
// 2. WI-FI & OVER-THE-AIR (OTA) CONFIGURATION
// =============================================================================
#define WIFI_SSID         "el_Johannu"
#define WIFI_PASS         "12345678"
#define WIFI_UDP_PORT     5005
#define OTA_PORT          3232
#define WEB_OTA_PORT      80
#define OTA_PASSWORD      ""

// Visual LED Indicator (Onboard Blue LED on GPIO 2)
// Used for status signalling: double-blink on command packet and OTA flashing.
#define PIN_LED           2

// =============================================================================
// 3. MOTOR PIN ASSIGNMENTS (LEG CONTROLLER)
// =============================================================================

// Motor 1: Board 1 Front Drive (B1_front)
#define PIN_M1_IN1          25
#define PIN_M1_IN2          26
#define PIN_M1_ENC_PULSE    15  // MTDO strapping pin; input with pullup

// Motor 2: Board 1 Rear Drive (B1_rear)
#define PIN_M2_IN1          32
#define PIN_M2_IN2          33
#define PIN_M2_ENC_PULSE    16  // Shifted to D16 (frees D2 for signalling LED)

// Motor 3: Board 2 Front Drive (B2_front)
#define PIN_M3_IN1          14
#define PIN_M3_IN2          27
#define PIN_M3_ENC_PULSE    34  // GPI input only (no internal pullup/down)

// Motor 4: Board 2 Rear Drive (B2_rear)
#define PIN_M4_IN1          12  // MTDI strapping pin (ensure low/floating at boot)
#define PIN_M4_IN2          13
#define PIN_M4_ENC_PULSE    35  // GPI input only (no internal pullup/down)

// =============================================================================
// 4. MOTOR SPEED & INTERVAL BRAKING PARAMETERS (FULL MAX DIGITAL - NO PWM)
// =============================================================================
#define MOTOR_INTERVAL_BRAKE_PULSES     10      // Brake every 10 pulses during last 50 pulses
#define MOTOR_INTERVAL_BRAKE_WINDOW     50      // Last 50 pulses interval braking active
#define MOTOR_INTERVAL_BRAKE_MS         80      // Brake for 80ms during each interval

// Stall Watchdog
#define MOTOR_STALL_VERIFY_TIMEOUT_MS 2000
#define MOTOR_STALL_MIN_PULSES           3

// Maximum execution timeout for safety
#define MAX_EXECUTION_TIMEOUT_MS 10000

// =============================================================================
// 5. DEBUGGING CONFIGURATION
// =============================================================================
#define DEBUG_LEVEL 1
#define SERIAL_BAUD_RATE 115200

#endif // CONFIG_H
