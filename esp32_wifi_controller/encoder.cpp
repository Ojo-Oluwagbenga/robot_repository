#include "encoder.h"

Encoder::Encoder(uint8_t pulse_pin, uint8_t motor_id)
    : _pulse_pin(pulse_pin), _motor_id(motor_id),
      _raw_count(0), _execution_net_pulses(0), _absolute_position(0),
      _motor_direction(0), _is_active(false), _last_edge_time_us(0),
      _target_pulses(0), _pin_in1(255), _pin_in2(255),
      _mux(portMUX_INITIALIZER_UNLOCKED) {}

void Encoder::begin() {
    // Note: GPIO 34-39 on ESP32 are input-only and lack internal pull-up/down resistors.
    if (_pulse_pin >= 34) {
        pinMode(_pulse_pin, INPUT);
    } else {
        pinMode(_pulse_pin, INPUT_PULLUP);
    }
}

void Encoder::setMotorDirection(uint8_t direction) {
    portENTER_CRITICAL(&_mux);
    _motor_direction = direction;
    portEXIT_CRITICAL(&_mux);
}

void Encoder::clearAndStartCounting(uint8_t direction, uint32_t target_pulses, uint8_t pin_in1, uint8_t pin_in2) {
    portENTER_CRITICAL(&_mux);
    _motor_direction = direction;
    _raw_count = 0;
    _execution_net_pulses = 0;
    _target_pulses = target_pulses;
    _pin_in1 = pin_in1;
    _pin_in2 = pin_in2;
    _last_edge_time_us = micros();
    _is_active = true;
    portEXIT_CRITICAL(&_mux);
}

void Encoder::stopCounting() {
    portENTER_CRITICAL(&_mux);
    _is_active = false;
    // DRV8871 Active Brake: Drive both pins HIGH (slow decay, low-side FETs ON)
    if (_pin_in1 != 255) digitalWrite(_pin_in1, HIGH);
    if (_pin_in2 != 255) digitalWrite(_pin_in2, HIGH);
    portEXIT_CRITICAL(&_mux);
}

void IRAM_ATTR Encoder::handleInterrupt() {
    portENTER_CRITICAL_ISR(&_mux);
    // 1. Only count if this designated motor is actively moving
    if (!_is_active) {
        portEXIT_CRITICAL_ISR(&_mux);
        return;
    }

    // 2. Hardware glitch & PWM noise filter: ignore transitions faster than 100 microseconds (10 kHz max)
    uint32_t now_us = micros();
    if ((uint32_t)(now_us - _last_edge_time_us) < 100) {
        portEXIT_CRITICAL_ISR(&_mux);
        return;
    }
    _last_edge_time_us = now_us;

    _raw_count++;
    int8_t dir = (_motor_direction == 0) ? 1 : -1;
    _execution_net_pulses += dir;
    _absolute_position += dir;

    // 3. Zero-latency hardware cutoff: immediately clamp motor pins HIGH (DRV8871 active brake) when target reached
    if (_target_pulses > 0 && _raw_count >= _target_pulses) {
        if (_pin_in1 != 255) digitalWrite(_pin_in1, HIGH);
        if (_pin_in2 != 255) digitalWrite(_pin_in2, HIGH);
    }
    portEXIT_CRITICAL_ISR(&_mux);
}

int32_t Encoder::getExecutionNetPulses() {
    portENTER_CRITICAL(&_mux);
    int32_t val = _execution_net_pulses;
    portEXIT_CRITICAL(&_mux);
    return val;
}

int64_t Encoder::getAbsolutePosition() {
    portENTER_CRITICAL(&_mux);
    int64_t val = _absolute_position;
    portEXIT_CRITICAL(&_mux);
    return val;
}

uint32_t Encoder::getSinglePinRawCount() {
    portENTER_CRITICAL(&_mux);
    uint32_t val = _raw_count;
    portEXIT_CRITICAL(&_mux);
    return val;
}

void Encoder::resetExecutionCounter() {
    portENTER_CRITICAL(&_mux);
    _execution_net_pulses = 0;
    _raw_count = 0;
    _last_edge_time_us = micros();
    portEXIT_CRITICAL(&_mux);
}

void Encoder::resetRawCount() {
    portENTER_CRITICAL(&_mux);
    _raw_count = 0;
    _last_edge_time_us = micros();
    portEXIT_CRITICAL(&_mux);
}

#if ESP_ID != 3
volatile uint32_t g_raw_edges[6] = {0, 0, 0, 0, 0, 0};

// Global encoder instances for the 6 motors
Encoder encoder1(PIN_M1_ENC_PULSE, 0);
Encoder encoder2(PIN_M2_ENC_PULSE, 1);
Encoder encoder3(PIN_M3_ENC_PULSE, 2);
Encoder encoder4(PIN_M4_ENC_PULSE, 3);
Encoder encoder5(PIN_M5_ENC_PULSE, 4);
Encoder encoder6(PIN_M6_ENC_PULSE, 5);

static void IRAM_ATTR isr_enc1() { g_raw_edges[0]++; encoder1.handleInterrupt(); }
static void IRAM_ATTR isr_enc2() { g_raw_edges[1]++; encoder2.handleInterrupt(); }
static void IRAM_ATTR isr_enc3() { g_raw_edges[2]++; encoder3.handleInterrupt(); }
static void IRAM_ATTR isr_enc4() { g_raw_edges[3]++; encoder4.handleInterrupt(); }
static void IRAM_ATTR isr_enc5() { g_raw_edges[4]++; encoder5.handleInterrupt(); }
static void IRAM_ATTR isr_enc6() { g_raw_edges[5]++; encoder6.handleInterrupt(); }

void init_all_encoders() {
    encoder1.begin();
    encoder2.begin();
    encoder3.begin();
    encoder4.begin();
    encoder5.begin();
    encoder6.begin();

    // Attach interrupts on CHANGE for single-pin counters
    attachInterrupt(digitalPinToInterrupt(PIN_M1_ENC_PULSE), isr_enc1, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_M2_ENC_PULSE), isr_enc2, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_M3_ENC_PULSE), isr_enc3, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_M4_ENC_PULSE), isr_enc4, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_M5_ENC_PULSE), isr_enc5, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_M6_ENC_PULSE), isr_enc6, CHANGE);
}
#endif
