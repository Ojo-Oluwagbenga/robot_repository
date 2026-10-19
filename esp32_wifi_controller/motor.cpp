#include "motor.h"
#include "wifi_communication.h"

#if __has_include(<esp_arduino_version.h>)
  #include <esp_arduino_version.h>
#endif

#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
  #define ESP32_CORE_V3_OR_NEWER 1
#elif defined(ESP_ARDUINO_VERSION) && (ESP_ARDUINO_VERSION >= 0x030000)
  #define ESP32_CORE_V3_OR_NEWER 1
#else
  #define ESP32_CORE_V3_OR_NEWER 0
#endif

#if ESP_ID == 3
// =============================================================================
// ESP 3: 2 Ordinary Motors with Limit Switches (Pins 26 and 25)
// =============================================================================
MotorController motor1(0, PIN_M1_IN1, PIN_M1_IN2, 0, 1, nullptr, PIN_M1_LIMIT);
MotorController motor2(1, PIN_M2_IN1, PIN_M2_IN2, 2, 3, nullptr, PIN_M2_LIMIT);
static MotorController* all_motors[NUM_MOTORS] = {&motor1, &motor2};

#else
// =============================================================================
// ESP 1 & ESP 2: 6 DC Motors with Single-Pin Encoders
// =============================================================================
MotorController motor1(0, PIN_M1_IN1, PIN_M1_IN2, 0, 1, &encoder1);
MotorController motor2(1, PIN_M2_IN1, PIN_M2_IN2, 2, 3, &encoder2);
MotorController motor3(2, PIN_M3_IN1, PIN_M3_IN2, 4, 5, &encoder3);
MotorController motor4(3, PIN_M4_IN1, PIN_M4_IN2, 6, 7, &encoder4);
MotorController motor5(4, PIN_M5_IN1, PIN_M5_IN2, 8, 9, &encoder5);
MotorController motor6(5, PIN_M6_IN1, PIN_M6_IN2, 10, 11, &encoder6);
static MotorController* all_motors[NUM_MOTORS] = {&motor1, &motor2, &motor3, &motor4, &motor5, &motor6};
#endif

MotorController::MotorController(uint8_t id, uint8_t pin_in1, uint8_t pin_in2,
                                 uint8_t pwm_ch1, uint8_t pwm_ch2,
                                 Encoder* encoder, uint8_t limit_pin)
    : _id(id), _pin_in1(pin_in1), _pin_in2(pin_in2),
      _pwm_ch1(pwm_ch1), _pwm_ch2(pwm_ch2),
      _encoder(encoder), _limit_pin(limit_pin),
      _mode(MODE_BRAKED), _direction(0), _target_pulses(0),
      _is_running(false),
      _start_time_ms(0), _last_pulse_time_ms(0), _last_pulse_count(0),
      _last_completed_time_ms(0),
      _is_interval_braking(false),
      _interval_brake_until_ms(0),
      _next_interval_brake_pulse(0) {
    memset(_current_instruction_id, 0, sizeof(_current_instruction_id));
    memset(_last_completed_instruction_id, 0, sizeof(_last_completed_instruction_id));
}

void MotorController::begin() {
    // 1. Configure pins as outputs and clamp HIGH (DRV8871 active brake) immediately
    pinMode(_pin_in1, OUTPUT);
    pinMode(_pin_in2, OUTPUT);
    digitalWrite(_pin_in1, HIGH);
    digitalWrite(_pin_in2, HIGH);

#if DEBUG_LEVEL > 0
    Serial.printf("[MOTOR %d INIT] DRV8871 Digital IN1 (GPIO %d) | IN2 (GPIO %d) -> CLAMPED ACTIVE BRAKE (HIGH/HIGH)\n",
                  _id + 1, _pin_in1, _pin_in2);
#endif

    // 2. Initialize limit switch pin if configured (ESP 3)
    if (_limit_pin != 255) {
        pinMode(_limit_pin, INPUT_PULLDOWN);
    }
}

bool MotorController::isLimitHit() const {
    if (_limit_pin == 255) return false;
    return (digitalRead(_limit_pin) == HIGH);
}

void MotorController::applyBinary(uint8_t drive_state, uint8_t direction) {
    pinMode(_pin_in1, OUTPUT);
    pinMode(_pin_in2, OUTPUT);

    // Drive states (NO PWM, FULL MAX DIGITAL HIGH/LOW):
    // drive_state == 0: ACTIVE BRAKE (IN1=HIGH, IN2=HIGH -> slow decay, shorts motor terminals)
    // drive_state == 1: RUN FULL SPEED BINARY (Dir 0: IN1=HIGH, IN2=LOW | Dir 1: IN1=LOW, IN2=HIGH)
    if (drive_state == 0) {
        digitalWrite(_pin_in1, HIGH);
        digitalWrite(_pin_in2, HIGH);
    } else {
        if (direction == 0) {
            digitalWrite(_pin_in1, HIGH);
            digitalWrite(_pin_in2, LOW);
        } else {
            digitalWrite(_pin_in1, LOW);
            digitalWrite(_pin_in2, HIGH);
        }
    }
}

void MotorController::brake() {
    _is_running = false;
    applyBinary(0, _direction);
}

void MotorController::interruptCurrentCommand() {
    brake();
    if (_encoder != nullptr) {
        _encoder->stopCounting();
    }
    int32_t net = (_encoder != nullptr) ? _encoder->getExecutionNetPulses() : 0;
#if DEBUG_LEVEL >= 1
    Serial.printf("[MOTOR %d] INTERRUPTED net=%ld (holding brake HIGH/HIGH)\n", _id + 1, (long)net);
#endif
    wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "interrupted");
    _mode = MODE_BRAKED;
}

void MotorController::completeNormalCommand() {
    brake(); // Clamps pins to HIGH, HIGH (DRV8871 active brake)
    if (_encoder != nullptr) {
        _encoder->stopCounting();
    }
    int32_t net = (_encoder != nullptr) ? _encoder->getExecutionNetPulses() : _target_pulses;
#if DEBUG_LEVEL >= 1
    Serial.printf("[MOTOR %d] COMPLETE: net=%ld (holding brake HIGH/HIGH)\n", _id + 1, (long)net);
#endif
    wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "completed");
    strncpy(_last_completed_instruction_id, _current_instruction_id, sizeof(_last_completed_instruction_id) - 1);
    _last_completed_instruction_id[sizeof(_last_completed_instruction_id) - 1] = '\0';
    _last_completed_time_ms = millis();
    _mode = MODE_BRAKED; // Remains braked indefinitely until otherwise commanded
}

void MotorController::startNormalCommand(uint16_t target_pulses, uint8_t direction, const char* instruction_id) {
    // Duplicate packet check (only drops network duplicate retransmissions within 300ms)
    if (instruction_id && strlen(instruction_id) > 0 && strcmp(instruction_id, "none") != 0) {
        if (_mode == MODE_NORMAL && strcmp(_current_instruction_id, instruction_id) == 0) {
            return;
        }
        if ((_mode == MODE_IDLE || _mode == MODE_BRAKED) && strcmp(_last_completed_instruction_id, instruction_id) == 0 && (millis() - _last_completed_time_ms < 300)) {
            return;
        }
    }

    if (_mode != MODE_IDLE && _mode != MODE_BRAKED) {
        interruptCurrentCommand();
    }

    if (instruction_id && strlen(instruction_id) > 0) {
        strncpy(_current_instruction_id, instruction_id, sizeof(_current_instruction_id) - 1);
        _current_instruction_id[sizeof(_current_instruction_id) - 1] = '\0';
    } else {
        snprintf(_current_instruction_id, sizeof(_current_instruction_id), "cmd_%lu", millis());
    }

    // =========================================================================
    // BRANCH A: Ordinary Motor with Limit Switch (ESP 3)
    // =========================================================================
    if (_limit_pin != 255) {
        // Check if limit switch is already touched (active HIGH)
        if (digitalRead(_limit_pin) == HIGH && direction == 0) {
            brake();
            _mode = MODE_BRAKED;
#if DEBUG_LEVEL >= 1
            Serial.printf("[MOTOR %d] Cannot move in dir 0: Limit Pin %d is ALREADY HIGH!\n", _id + 1, _limit_pin);
#endif
            wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "limit_reached");
            return;
        }

        _mode = MODE_NORMAL;
        _direction = direction;
        _target_pulses = target_pulses;
        _is_running = true;
        _start_time_ms = millis();
        applyBinary(1, _direction);
#if DEBUG_LEVEL >= 1
        Serial.printf("[MOTOR %d] Ordinary Motor RUN: target=%u (%ums), dir=%d\n",
                      _id + 1, target_pulses, target_pulses * ESP3_MS_PER_PULSE, direction);
#endif
        return;
    }

    // =========================================================================
    // BRANCH B: Encoded Motor (ESP 1 & ESP 2)
    // =========================================================================
    if (target_pulses == 0) {
        brake();
        _mode = MODE_BRAKED;
        wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "completed");
        return;
    }

    _mode = MODE_NORMAL;
    _direction = direction;
    _target_pulses = target_pulses;
    _start_time_ms = millis();
    _last_pulse_time_ms = millis();
    _last_pulse_count = 0;

    // Clear previous history completely and activate counting with zero-latency hardware cutoff
    if (_encoder != nullptr) {
        _encoder->clearAndStartCounting(direction, target_pulses, _pin_in1, _pin_in2);
    }

    _is_interval_braking = false;
    _interval_brake_until_ms = 0;

    // 10-pulse interval braking during the last 50 pulses:
    // If target > 50, the last 50 pulses zone starts at target - 50.
    // If target <= 50, all pulses are within the last 50 pulses; first brake at pulse 10.
    if (_target_pulses > MOTOR_INTERVAL_BRAKE_WINDOW) {
        _next_interval_brake_pulse = _target_pulses - MOTOR_INTERVAL_BRAKE_WINDOW;
    } else if (_target_pulses >= MOTOR_INTERVAL_BRAKE_PULSES) {
        _next_interval_brake_pulse = MOTOR_INTERVAL_BRAKE_PULSES;
    } else {
        _next_interval_brake_pulse = _target_pulses + 1; // < 10 pulses: runs directly to target
    }

    _is_running = true;
    applyBinary(1, _direction); // Full max binary (high and low) immediately, NO PWM!

#if DEBUG_LEVEL >= 1
    Serial.printf("[MOTOR %d] Encoded Motor START (FULL MAX, NO PWM): target=%u, dir=%d, id=%s (next_brake=%u)\n",
                  _id + 1, _target_pulses, _direction, _current_instruction_id, _next_interval_brake_pulse);
#endif
}

void MotorController::startLockOn(uint8_t direction, const char* instruction_id) {
    if (_mode != MODE_IDLE && _mode != MODE_BRAKED) {
        interruptCurrentCommand();
    }

    if (instruction_id && strlen(instruction_id) > 0) {
        strncpy(_current_instruction_id, instruction_id, sizeof(_current_instruction_id) - 1);
        _current_instruction_id[sizeof(_current_instruction_id) - 1] = '\0';
    } else {
        snprintf(_current_instruction_id, sizeof(_current_instruction_id), "lock_%lu", millis());
    }

    // Limit switch check
    if (_limit_pin != 255 && digitalRead(_limit_pin) == HIGH && direction == 0) {
        brake();
        _mode = MODE_BRAKED;
        wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "limit_reached");
        return;
    }

    _mode = MODE_LOCK_ON;
    _direction = direction;
    _is_running = true;
    _start_time_ms = millis();

    if (_encoder != nullptr) {
        _encoder->clearAndStartCounting(direction, 0, _pin_in1, _pin_in2);
    }

    applyBinary(1, _direction); // Full speed digital (0/1)
#if DEBUG_LEVEL >= 1
    Serial.printf("[MOTOR %d] LOCK-ON ENGAGED: dir=%d, id=%s\n", _id + 1, _direction, _current_instruction_id);
#endif
}

void MotorController::releaseLockOn() {
    if (_mode == MODE_LOCK_ON) {
        brake();
        if (_encoder != nullptr) {
            _encoder->stopCounting();
        }
        _mode = MODE_BRAKED;
        int32_t net = (_encoder != nullptr) ? _encoder->getExecutionNetPulses() : 0;
#if DEBUG_LEVEL >= 1
        Serial.printf("[MOTOR %d] LOCK-ON RELEASED: net=%ld (holding brake)\n", _id + 1, (long)net);
#endif
        wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "released");
    }
}

void MotorController::update() {
    // All motors remain braked indefinitely until otherwise commanded (DRV8871 active brake: HIGH/HIGH)
    if (_mode == MODE_IDLE || _mode == MODE_BRAKED) {
        return;
    }

    // =========================================================================
    // CASE 1: Ordinary Motor with Limit Switch (ESP 3)
    // =========================================================================
    if (_limit_pin != 255) {
        if (_is_running) {
            bool limit_hit = (digitalRead(_limit_pin) == HIGH);
            uint32_t elapsed_ms = millis() - _start_time_ms;
            uint32_t target_duration_ms = (_target_pulses > 0) ? (_target_pulses * ESP3_MS_PER_PULSE) : 0;

            // 1. Limit switch contact (goes HIGH on contact)
            if (limit_hit && _direction == 0) {
                brake();
                _mode = MODE_BRAKED;
#if DEBUG_LEVEL >= 1
                Serial.printf("[MOTOR %d] LIMIT REACHED on Pin %d! Holding brake.\n", _id + 1, _limit_pin);
#endif
                wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "limit_reached");
                return;
            }

            // 2. Time-based duration expired (if target pulses > 0)
            if (target_duration_ms > 0 && elapsed_ms >= target_duration_ms) {
                brake();
                _mode = MODE_BRAKED;
#if DEBUG_LEVEL >= 1
                Serial.printf("[MOTOR %d] TIME COMPLETE: %ums. Holding brake.\n", _id + 1, elapsed_ms);
#endif
                wifiComm.sendFeedback(ESP_ID, _id + 1, _target_pulses, _current_instruction_id, "completed");
                return;
            }

            // 3. Safety Watchdog
            if (elapsed_ms >= MAX_EXECUTION_TIMEOUT_MS) {
                brake();
                _mode = MODE_BRAKED;
                wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "timeout");
                return;
            }
        }
        return;
    }

    // =========================================================================
    // CASE 2: Encoded Motor (ESP 1 & ESP 2)
    // =========================================================================
    if (!_encoder) return;

    if (_mode == MODE_NORMAL) {
        uint32_t pulses = _encoder->getSinglePinRawCount();
        int32_t net = _encoder->getExecutionNetPulses();
        uint32_t elapsed_ms = millis() - _start_time_ms;

        if (pulses != _last_pulse_count) {
            _last_pulse_count = pulses;
            _last_pulse_time_ms = millis();
        }

        // =====================================================================
        // 1. INTERVAL BRAKE RESUME CHECK (80ms non-blocking timer)
        // =====================================================================
        if (_is_interval_braking) {
            if (millis() < _interval_brake_until_ms) {
                // Still holding 80ms interval brake; do not drive motor
                return;
            }
            // 80ms brake expired: resume full max binary drive!
            _is_interval_braking = false;
            _last_pulse_time_ms = millis(); // Reset stall timer
            applyBinary(1, _direction);
        }

        // =====================================================================
        // 2. TARGET REACHED -> COMPLETE & FINAL BRAKE (REMAIN BRAKED)
        // =====================================================================
        if (pulses >= _target_pulses) {
            completeNormalCommand();
            return;
        }

        // =====================================================================
        // 3. 10-PULSE INTERVAL BRAKE TRIGGER DURING LAST 50 PULSES (80ms)
        // =====================================================================
        if (pulses >= _next_interval_brake_pulse && _next_interval_brake_pulse < _target_pulses) {
            brake(); // Apply active dynamic brake (DRV8871 slow decay: HIGH/HIGH)
            _is_interval_braking = true;
            _interval_brake_until_ms = millis() + MOTOR_INTERVAL_BRAKE_MS; // 80ms
            _next_interval_brake_pulse = pulses + MOTOR_INTERVAL_BRAKE_PULSES; // Next brake in 10 pulses
            return;
        }

        // =====================================================================
        // 3. SAFETY WATCHDOGS: STALL & TIMEOUT
        // =====================================================================
        // Initial stall verify: 0 pulses after timeout
        if (pulses == 0 && elapsed_ms >= MOTOR_STALL_VERIFY_TIMEOUT_MS) {
            brake();
            _encoder->stopCounting();
            _mode = MODE_BRAKED;
#if DEBUG_LEVEL >= 1
            Serial.printf("[MOTOR %d ERROR] STALL: 0 pulses after %ums\n", _id + 1, elapsed_ms);
#endif
            wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "stalled");
            return;
        }

        // Stalled mid-way (pulses stopped advancing for > 1500ms)
        if (pulses > 0 && pulses < _target_pulses && (millis() - _last_pulse_time_ms >= 1500)) {
            brake();
            _encoder->stopCounting();
            _mode = MODE_BRAKED;
#if DEBUG_LEVEL >= 1
            Serial.printf("[MOTOR %d ERROR] STALLED mid-way at %u/%u pulses\n", _id + 1, pulses, _target_pulses);
#endif
            wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "stalled");
            return;
        }

        // Safety timeout
        if (elapsed_ms >= MAX_EXECUTION_TIMEOUT_MS) {
            brake();
            _encoder->stopCounting();
            _mode = MODE_BRAKED;
#if DEBUG_LEVEL >= 1
            Serial.printf("[MOTOR %d ERROR] TIMEOUT at %u/%u pulses after %ums\n", _id + 1, pulses, _target_pulses, elapsed_ms);
#endif
            wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "timeout");
            return;
        }
    }
    // =========================================================================
    // CASE 3: Lock-On Mode
    // =========================================================================
    else if (_mode == MODE_LOCK_ON) {
        if (_is_running) {
            applyBinary(1, _direction);
        }
    }
}

void init_all_motors() {
    for (uint8_t m = 0; m < NUM_MOTORS; ++m) {
        all_motors[m]->begin();
    }
}

void update_all_motors(bool bus_data_state) {
    (void)bus_data_state;
    for (uint8_t m = 0; m < NUM_MOTORS; ++m) {
        all_motors[m]->update();
    }
}

void release_any_active_lock() {
    for (uint8_t m = 0; m < NUM_MOTORS; ++m) {
        if (all_motors[m]->isLockOn()) {
            all_motors[m]->releaseLockOn();
        }
    }
}
