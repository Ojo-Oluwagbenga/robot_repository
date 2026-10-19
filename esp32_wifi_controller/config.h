#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

// =============================================================================
// 1. ESP32 IDENTIFICATION (Change before compiling for each board: 1, 2, or 3)
// =============================================================================
// ESP1 = Right Arm / Body Motors 1 to 6 (with encoders)
// ESP2 = Left Arm / Body Motors 1 to 6 (with encoders)
// ESP3 = Head / Gripper Motors 1 & 2 (Ordinary motors with limit switches)
#define ESP_ID 3

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
#define PIN_LED           2

// =============================================================================
// 3. MOTOR PIN ASSIGNMENTS PER BOARD
// =============================================================================

#if ESP_ID == 3
// -----------------------------------------------------------------------------
// ESP 3: 2 Ordinary Motors with Limit Switches
// -----------------------------------------------------------------------------
#define NUM_MOTORS          2

// Motor 1: IN1=13, IN2=12, Limit Pin=26 (touches 3.3V on contact)
#define PIN_M1_IN1          13
#define PIN_M1_IN2          12
#define PIN_M1_LIMIT        26

// Motor 2: IN1=14, IN2=27, Limit Pin=25 (touches 3.3V on contact)
#define PIN_M2_IN1          14
#define PIN_M2_IN2          27
#define PIN_M2_LIMIT        25

#else
// -----------------------------------------------------------------------------
// ESP 1 & ESP 2: 6 DC Motors with Single-Pin Encoders
// -----------------------------------------------------------------------------
#define NUM_MOTORS          6

// Motor 1
#define PIN_M1_IN1          13
#define PIN_M1_IN2          12
#define PIN_M1_ENC_PULSE    35  // Input only

// Motor 2
#define PIN_M2_IN1          14
#define PIN_M2_IN2          27
#define PIN_M2_ENC_PULSE    34  // Input only

// Motor 3
#define PIN_M3_IN1          26
#define PIN_M3_IN2          25
#define PIN_M3_ENC_PULSE    23  // Input with internal pull-up

// Motor 4
#define PIN_M4_IN1          33
#define PIN_M4_IN2          32
#define PIN_M4_ENC_PULSE    22  // Input with internal pull-up

// Motor 5
#define PIN_M5_IN1          19
#define PIN_M5_IN2          21
#define PIN_M5_ENC_PULSE    18  // Input with internal pull-up

// Motor 6
#define PIN_M6_IN1          5
#define PIN_M6_IN2          17
#define PIN_M6_ENC_PULSE    16  // Input with internal pull-up

#endif

// =============================================================================
// 4. MOTOR SPEED & INTERVAL BRAKING PARAMETERS (FULL MAX DIGITAL - NO PWM)
// =============================================================================
#define MOTOR_INTERVAL_BRAKE_PULSES     10      // Brake every 10 pulses during last 50 pulses
#define MOTOR_INTERVAL_BRAKE_WINDOW     50      // Last 50 pulses interval braking active
#define MOTOR_INTERVAL_BRAKE_MS         80      // Brake for 80ms during each interval

// Stall Watchdog (for encoded motors)
#define MOTOR_STALL_VERIFY_TIMEOUT_MS 2000
#define MOTOR_STALL_MIN_PULSES           3

// Time per pulse for ordinary motors on ESP3 (1 pulse = 10ms of run time)
#define ESP3_MS_PER_PULSE        10

// Maximum execution timeout
#define MAX_EXECUTION_TIMEOUT_MS 10000

// =============================================================================
// 5. DEBUGGING CONFIGURATION
// =============================================================================
#define DEBUG_LEVEL 1
#define SERIAL_BAUD_RATE 115200

#endif // CONFIG_H
