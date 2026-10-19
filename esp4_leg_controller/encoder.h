#ifndef ENCODER_H
#define ENCODER_H

#include <Arduino.h>
#include "config.h"

class Encoder {
public:
    Encoder(uint8_t pulse_pin, uint8_t motor_id);

    void begin();

    // ISR handler called on single-pin pulse transitions
    void IRAM_ATTR handleInterrupt();

    // Informs the encoder of current motor driving direction (0 = CW, 1 = CCW)
    void setMotorDirection(uint8_t direction);

    // Active command gating & history clearing with zero-latency hardware brake cutoff
    void clearAndStartCounting(uint8_t direction, uint32_t target_pulses = 0, uint8_t pin_in1 = 255, uint8_t pin_in2 = 255);
    void stopCounting();
    bool isCountingActive() const { return _is_active; }

    // Getters and resets
    int32_t getExecutionNetPulses();
    int64_t getAbsolutePosition();
    uint32_t getSinglePinRawCount();
    void resetExecutionCounter();
    void resetRawCount();

    bool isSinglePinMode() const { return true; }
    uint8_t getPulsePin() const { return _pulse_pin; }
    uint8_t getMotorId() const { return _motor_id; }

private:
    uint8_t _pulse_pin;
    uint8_t _motor_id;

    volatile uint32_t _raw_count;
    volatile int32_t _execution_net_pulses;
    volatile int64_t _absolute_position;
    volatile uint8_t _motor_direction; // 0 = CW, 1 = CCW
    volatile bool _is_active;          // Only count when actively executing a command
    volatile uint32_t _last_edge_time_us; // Software glitch filter timestamp
    volatile uint32_t _target_pulses;
    volatile uint8_t _pin_in1;
    volatile uint8_t _pin_in2;

    portMUX_TYPE _mux;
};

// Global raw edge counts for diagnostics across all 4 pins
extern volatile uint32_t g_raw_edges[NUM_MOTORS];

// Global encoder instances for the 4 motors on ESP 4
extern Encoder encoder1;
extern Encoder encoder2;
extern Encoder encoder3;
extern Encoder encoder4;

void init_all_encoders();

#endif // ENCODER_H
