#include "motor.h"
#include "wifi_communication.h"

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
      _mode(MODE_IDLE), _direction(0), _target_pulses(0),
      _is_running(false),
      _pulses(0), _driven_time_ms(0), _driven_time_at_watchdog_start(0),
      _brake_hold_remaining_ms(0), _is_braking(false),
      _completed(false), _jammed(false), _config_error(false),
      _start_time_ms(0), _last_pulse_time_ms(0), _last_pulse_count(0),
      _last_completed_time_ms(0),
      _is_interval_braking(false),
      _interval_brake_until_ms(0),
      _next_interval_brake_pulse(0) {
    memset(&_profile, 0, sizeof(_profile));
    memset(_jam_section, 0, sizeof(_jam_section));
    memset(_error_msg, 0, sizeof(_error_msg));
    memset(_current_instruction_id, 0, sizeof(_current_instruction_id));
    memset(_last_completed_instruction_id, 0, sizeof(_last_completed_instruction_id));
}

void MotorController::begin() {
    // 1. Configure pins as outputs and clamp HIGH (DRV8871 active short brake) on boot
    pinMode(_pin_in1, OUTPUT);
    pinMode(_pin_in2, OUTPUT);
    digitalWrite(_pin_in1, HIGH);
    digitalWrite(_pin_in2, HIGH);

    // 2. Link encoder to this motor controller for instant ISR notifications
    if (_encoder != nullptr) {
        _encoder->setMotorController(this);
    }

    // 3. Initialize limit switch pin if configured (ESP 3)
    if (_limit_pin != 255) {
        pinMode(_limit_pin, INPUT_PULLDOWN);
    }

#if DEBUG_LEVEL > 0
    Serial.printf("[MOTOR %d INIT] Pins: IN1=%d, IN2=%d -> CLAMPED ACTIVE BRAKE (HIGH/HIGH)\n",
                  _id + 1, _pin_in1, _pin_in2);
#endif
}

bool MotorController::isLimitHit() const {
    if (_limit_pin == 255) return false;
    return (digitalRead(_limit_pin) == HIGH);
}

void MotorController::applyBinary(uint8_t drive_state, uint8_t direction) {
    // drive_state == 0: ACTIVE BRAKE / IDLE (IN1=HIGH, IN2=HIGH -> short brake)
    // drive_state == 1: RUN FULL DRIVE (Dir 0: IN1=HIGH, IN2=LOW | Dir 1: IN1=LOW, IN2=HIGH)
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
    _is_braking = false;
    applyBinary(0, _direction);
}

bool MotorController::startProfile(uint32_t target_pulses, uint8_t direction, const MotorProfile& profile,
                                   const char* instruction_id, char* err_msg, size_t err_len) {
    // 1. Validation
    if (target_pulses == 0) {
        snprintf(err_msg, err_len, "pulse must be > 0");
        _config_error = true;
        strncpy(_error_msg, err_msg, sizeof(_error_msg) - 1);
        applyBinary(0, 0);
        return false;
    }
    if (direction != 0 && direction != 1) {
        snprintf(err_msg, err_len, "dir must be 0 or 1");
        _config_error = true;
        strncpy(_error_msg, err_msg, sizeof(_error_msg) - 1);
        applyBinary(0, 0);
        return false;
    }
    for (uint8_t s = 0; s < profile.section_count; ++s) {
        const BrakeSection& sec = profile.sections[s];
        if (sec.start >= sec.end) {
            snprintf(err_msg, err_len, "section %s: start (%lu) must be < end (%lu)",
                     sec.name, (unsigned long)sec.start, (unsigned long)sec.end);
            _config_error = true;
            strncpy(_error_msg, err_msg, sizeof(_error_msg) - 1);
            applyBinary(0, 0);
            return false;
        }
    }

    // 2. Setup Profile Structure
    _profile = profile;
    for (uint8_t s = 0; s < _profile.section_count; ++s) {
        BrakeSection& sec = _profile.sections[s];
        uint32_t w = sec.end - sec.start;
        if (sec.brake_count == 0) {
            sec.effective_count = 0;
            sec.active = false;
        } else if (sec.brake_count > w) {
            // Drop extras: brake once at every position in window
            sec.effective_count = w;
            sec.active = true;
            sec.current_k = 0;
        } else {
            sec.effective_count = sec.brake_count;
            sec.active = true;
            sec.current_k = 0;
        }
    }

    // 3. Reset Timing & State
    _mode = MODE_PROFILED;
    _target_pulses = target_pulses;
    _direction = direction;
    _pulses = 0;
    _driven_time_ms = 0;
    _driven_time_at_watchdog_start = 0;
    _brake_hold_remaining_ms = 0;
    _is_braking = false;
    _completed = false;
    _jammed = false;
    _config_error = false;
    _is_running = true;

    if (instruction_id && strlen(instruction_id) > 0) {
        strncpy(_current_instruction_id, instruction_id, sizeof(_current_instruction_id) - 1);
        _current_instruction_id[sizeof(_current_instruction_id) - 1] = '\0';
    } else {
        snprintf(_current_instruction_id, sizeof(_current_instruction_id), "prof_%lu", millis());
    }

    if (_encoder != nullptr) {
        _encoder->clearAndStartCounting(direction, target_pulses, _pin_in1, _pin_in2);
    }

    // 4. Check for immediate brake at start (position 0)
    bool immediate_brake = false;
    uint32_t hold_ms = 0;
    for (uint8_t s = 0; s < _profile.section_count; ++s) {
        BrakeSection& sec = _profile.sections[s];
        if (!sec.active || sec.duration_type != DURATION_PULSE) continue;
        if (sec.start == 0 && sec.effective_count > 0 && sec.current_k == 0) {
            immediate_brake = true;
            if (sec.brake_ms > hold_ms) hold_ms = sec.brake_ms;
            sec.current_k++;
        }
    }

    if (immediate_brake) {
        applyBinary(0, _direction); // Hold brake
        _is_braking = true;
        _brake_hold_remaining_ms = hold_ms;
    } else {
        applyBinary(1, _direction); // Drive full on
    }

#if DEBUG_LEVEL > 0
    Serial.printf("[MOTOR %d] PROFILED MOVE START: target=%lu, dir=%d, sections=%d (immediate_brake=%d)\n",
                  _id + 1, (unsigned long)_target_pulses, _direction, _profile.section_count, immediate_brake);
#endif
    return true;
}

void IRAM_ATTR MotorController::onPulseEdge() {
    if (!_is_running) return;

    _pulses++;
    // The watchdog resets on every pulse
    _driven_time_at_watchdog_start = _driven_time_ms;

    // 1. Completion check
    if (_pulses >= _target_pulses) {
        applyBinary(0, _direction); // Both pins HIGH (short brake)
        _is_running = false;
        _is_braking = false;
        _completed = true;
        return;
    }

    // 2. Pulse-based profile triggers
    if (_mode == MODE_PROFILED) {
        for (uint8_t s = 0; s < _profile.section_count; ++s) {
            BrakeSection& sec = _profile.sections[s];
            if (!sec.active || sec.duration_type != DURATION_PULSE) continue;

            while (sec.current_k < sec.effective_count) {
                uint32_t pos;
                uint32_t w = sec.end - sec.start;
                if (sec.brake_count > w) {
                    pos = sec.start + sec.current_k;
                } else {
                    pos = sec.start + (uint32_t)(((uint64_t)sec.current_k * w) / sec.brake_count);
                }

                if (_pulses >= pos) {
                    // A brake fires whenever the pulse count reaches its position,
                    // even if a hold is in progress. A new brake restarts the hold at its full brake_ms.
                    applyBinary(0, _direction);
                    _is_braking = true;
                    _brake_hold_remaining_ms = sec.brake_ms;
                    sec.current_k++;
                } else {
                    break;
                }
            }
        }
    }
}

void IRAM_ATTR MotorController::onTimer1ms() {
    if (!_is_running) return;

    if (_mode == MODE_PROFILED) {
        if (_is_braking) {
            // Running time is the time the motor is driven.
            // The running-time clock pauses during a brake hold.
            // The watchdog does NOT run during a hold.
            if (_brake_hold_remaining_ms > 0) {
                _brake_hold_remaining_ms--;
            }
            if (_brake_hold_remaining_ms == 0) {
                // Hold finished!
                _is_braking = false;
                if (_pulses >= _target_pulses) {
                    applyBinary(0, _direction);
                    _is_running = false;
                    _completed = true;
                    return;
                }
                // Resume full drive!
                applyBinary(1, _direction);
                // The watchdog restarts when each hold ends.
                _driven_time_at_watchdog_start = _driven_time_ms;
            }
        } else {
            // Motor is being driven
            _driven_time_ms++;

            // 1. Jam watchdog check
            // "If the motor is being driven and 2 second of driven time passes with no pulse, it is a jam."
            if ((_driven_time_ms - _driven_time_at_watchdog_start) >= MOTOR_JAM_TIMEOUT_MS) {
                // On a jam: set both pins HIGH, end that motor's process, and send an error to the Pi
                applyBinary(0, _direction);
                _is_running = false;
                _is_braking = false;
                _jammed = true;
                findActiveSection(_jam_section, sizeof(_jam_section));
                return;
            }

            // 2. Time-based profile triggers
            for (uint8_t s = 0; s < _profile.section_count; ++s) {
                BrakeSection& sec = _profile.sections[s];
                if (!sec.active || sec.duration_type != DURATION_TIME) continue;

                while (sec.current_k < sec.effective_count) {
                    uint32_t pos;
                    uint32_t w = sec.end - sec.start;
                    if (sec.brake_count > w) {
                        pos = sec.start + sec.current_k;
                    } else {
                        pos = sec.start + (uint32_t)(((uint64_t)sec.current_k * w) / sec.brake_count);
                    }

                    if (_driven_time_ms >= pos) {
                        // A brake fires whenever running time reaches its position,
                        // even if a hold is in progress. A new brake restarts the hold at its full brake_ms.
                        applyBinary(0, _direction);
                        _is_braking = true;
                        _brake_hold_remaining_ms = sec.brake_ms;
                        sec.current_k++;
                    } else {
                        break;
                    }
                }
            }
        }
    }
}

void MotorController::findActiveSection(char* out, size_t out_len) const {
    for (uint8_t s = 0; s < _profile.section_count; ++s) {
        const BrakeSection& sec = _profile.sections[s];
        if (!sec.active) continue;
        if (sec.duration_type == DURATION_PULSE) {
            if (_pulses >= sec.start && _pulses < sec.end) {
                strncpy(out, sec.name, out_len - 1);
                out[out_len - 1] = '\0';
                return;
            }
        } else if (sec.duration_type == DURATION_TIME) {
            if (_driven_time_ms >= sec.start && _driven_time_ms < sec.end) {
                strncpy(out, sec.name, out_len - 1);
                out[out_len - 1] = '\0';
                return;
            }
        }
    }
    strncpy(out, "none", out_len - 1);
    out[out_len - 1] = '\0';
}

void MotorController::update() {
    // 1. Process flags set by timer / ISR
    if (_config_error) {
        _config_error = false;
        wifiComm.sendError(ESP_ID, _id + 1, "invalid_config", _error_msg);
        return;
    }

    if (_jammed) {
        _jammed = false;
        _mode = MODE_IDLE;
#if DEBUG_LEVEL > 0
        Serial.printf("[MOTOR %d JAM] pulses=%lu, running_time_ms=%lu, section=%s\n",
                      _id + 1, (unsigned long)_pulses, (unsigned long)_driven_time_ms, _jam_section);
#endif
        wifiComm.sendJamFeedback(ESP_ID, _id + 1, _pulses, _driven_time_ms, _jam_section);
        return;
    }

    if (_completed) {
        _completed = false;
        _mode = MODE_IDLE;
#if DEBUG_LEVEL > 0
        Serial.printf("[MOTOR %d DONE] pulses=%lu, running_time_ms=%lu\n",
                      _id + 1, (unsigned long)_pulses, (unsigned long)_driven_time_ms);
#endif
        wifiComm.sendDoneFeedback(ESP_ID, _id + 1, _pulses, _driven_time_ms, _current_instruction_id);
        return;
    }

    // 2. Legacy commands support
    if (_mode == MODE_IDLE || _mode == MODE_BRAKED) {
        return;
    }

    if (_limit_pin != 255) {
        if (_is_running) {
            bool limit_hit = (digitalRead(_limit_pin) == HIGH);
            uint32_t elapsed_ms = millis() - _start_time_ms;
            uint32_t target_duration_ms = (_target_pulses > 0) ? (_target_pulses * ESP3_MS_PER_PULSE) : 0;

            if (limit_hit && _direction == 0) {
                brake();
                _mode = MODE_BRAKED;
                wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "limit_reached");
                return;
            }
            if (target_duration_ms > 0 && elapsed_ms >= target_duration_ms) {
                brake();
                _mode = MODE_BRAKED;
                wifiComm.sendFeedback(ESP_ID, _id + 1, _target_pulses, _current_instruction_id, "completed");
                return;
            }
            if (elapsed_ms >= MAX_EXECUTION_TIMEOUT_MS) {
                brake();
                _mode = MODE_BRAKED;
                wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "timeout");
                return;
            }
        }
        return;
    }

    if (_mode == MODE_NORMAL && _encoder != nullptr) {
        uint32_t pulses = _encoder->getSinglePinRawCount();
        int32_t net = _encoder->getExecutionNetPulses();
        uint32_t elapsed_ms = millis() - _start_time_ms;

        if (pulses != _last_pulse_count) {
            _last_pulse_count = pulses;
            _last_pulse_time_ms = millis();
        }

        if (_is_interval_braking) {
            if (millis() < _interval_brake_until_ms) return;
            _is_interval_braking = false;
            _last_pulse_time_ms = millis();
            applyBinary(1, _direction);
        }

        if (pulses >= _target_pulses) {
            completeNormalCommand();
            return;
        }

        if (pulses >= _next_interval_brake_pulse && _next_interval_brake_pulse < _target_pulses) {
            brake();
            _is_interval_braking = true;
            _interval_brake_until_ms = millis() + MOTOR_INTERVAL_BRAKE_MS;
            _next_interval_brake_pulse = pulses + MOTOR_INTERVAL_BRAKE_PULSES;
            return;
        }

        if (pulses == 0 && elapsed_ms >= MOTOR_STALL_VERIFY_TIMEOUT_MS) {
            brake();
            _encoder->stopCounting();
            _mode = MODE_BRAKED;
            wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "stalled");
            return;
        }

        if (pulses > 0 && pulses < _target_pulses && (millis() - _last_pulse_time_ms >= 1500)) {
            brake();
            _encoder->stopCounting();
            _mode = MODE_BRAKED;
            wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "stalled");
            return;
        }

        if (elapsed_ms >= MAX_EXECUTION_TIMEOUT_MS) {
            brake();
            _encoder->stopCounting();
            _mode = MODE_BRAKED;
            wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "timeout");
            return;
        }
    } else if (_mode == MODE_LOCK_ON) {
        if (_is_running) {
            applyBinary(1, _direction);
        }
    }
}

void MotorController::startNormalCommand(uint16_t target_pulses, uint8_t direction, const char* instruction_id) {
    if (_mode != MODE_IDLE && _mode != MODE_BRAKED) {
        interruptCurrentCommand();
    }
    if (instruction_id && strlen(instruction_id) > 0) {
        strncpy(_current_instruction_id, instruction_id, sizeof(_current_instruction_id) - 1);
        _current_instruction_id[sizeof(_current_instruction_id) - 1] = '\0';
    } else {
        snprintf(_current_instruction_id, sizeof(_current_instruction_id), "cmd_%lu", millis());
    }

    if (_limit_pin != 255) {
        if (digitalRead(_limit_pin) == HIGH && direction == 0) {
            brake();
            _mode = MODE_BRAKED;
            wifiComm.sendFeedback(ESP_ID, _id + 1, 0, _current_instruction_id, "limit_reached");
            return;
        }
        _mode = MODE_NORMAL;
        _direction = direction;
        _target_pulses = target_pulses;
        _is_running = true;
        _start_time_ms = millis();
        applyBinary(1, _direction);
        return;
    }

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

    if (_encoder != nullptr) {
        _encoder->clearAndStartCounting(direction, target_pulses, _pin_in1, _pin_in2);
    }

    _is_interval_braking = false;
    _interval_brake_until_ms = 0;
    if (_target_pulses > MOTOR_INTERVAL_BRAKE_WINDOW) {
        _next_interval_brake_pulse = _target_pulses - MOTOR_INTERVAL_BRAKE_WINDOW;
    } else if (_target_pulses >= MOTOR_INTERVAL_BRAKE_PULSES) {
        _next_interval_brake_pulse = MOTOR_INTERVAL_BRAKE_PULSES;
    } else {
        _next_interval_brake_pulse = _target_pulses + 1;
    }

    _is_running = true;
    applyBinary(1, _direction);
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
    applyBinary(1, _direction);
}

void MotorController::releaseLockOn() {
    if (_mode == MODE_LOCK_ON) {
        brake();
        if (_encoder != nullptr) {
            _encoder->stopCounting();
        }
        _mode = MODE_BRAKED;
        int32_t net = (_encoder != nullptr) ? _encoder->getExecutionNetPulses() : 0;
        wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "released");
    }
}

void MotorController::interruptCurrentCommand() {
    brake();
    if (_encoder != nullptr) {
        _encoder->stopCounting();
    }
    int32_t net = (_encoder != nullptr) ? _encoder->getExecutionNetPulses() : 0;
    wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "interrupted");
    _mode = MODE_BRAKED;
}

void MotorController::completeNormalCommand() {
    brake();
    if (_encoder != nullptr) {
        _encoder->stopCounting();
    }
    int32_t net = (_encoder != nullptr) ? _encoder->getExecutionNetPulses() : _target_pulses;
    wifiComm.sendFeedback(ESP_ID, _id + 1, net, _current_instruction_id, "completed");
    strncpy(_last_completed_instruction_id, _current_instruction_id, sizeof(_last_completed_instruction_id) - 1);
    _last_completed_instruction_id[sizeof(_last_completed_instruction_id) - 1] = '\0';
    _last_completed_time_ms = millis();
    _mode = MODE_BRAKED;
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

MotorController* get_motor(uint8_t index) {
    if (index < NUM_MOTORS) return all_motors[index];
    return nullptr;
}
