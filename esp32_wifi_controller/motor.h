#ifndef MOTOR_H
#define MOTOR_H

#include <Arduino.h>
#include "config.h"
#include "encoder.h"

enum MotorMode {
    MODE_IDLE = 0,
    MODE_NORMAL = 1,
    MODE_LOCK_ON = 2,
    MODE_BRAKED = 3
};

class MotorController {
public:
    // Constructor: supports encoded motor (encoder != nullptr, limit_pin == 255)
    // or ordinary motor with limit switch (encoder == nullptr, limit_pin != 255)
    MotorController(uint8_t id, uint8_t pin_in1, uint8_t pin_in2,
                    uint8_t pwm_ch1 = 0, uint8_t pwm_ch2 = 0,
                    Encoder* encoder = nullptr, uint8_t limit_pin = 255);

    void begin();

    // Command dispatchers
    void startNormalCommand(uint16_t target_pulses, uint8_t direction, const char* instruction_id = "");
    void startLockOn(uint8_t direction, const char* instruction_id = "");
    void releaseLockOn();

    const char* getInstructionId() const { return _current_instruction_id; }

    // Main non-blocking update loop
    void update();

    // Stop and brake immediately (clamps IN pins LOW)
    void brake();

    // Status queries
    MotorMode getMode() const { return _mode; }
    bool isLockOn() const { return _mode == MODE_LOCK_ON; }
    bool isBraked() const { return _mode == MODE_BRAKED; }
    uint8_t getId() const { return _id; }
    bool isLimitHit() const;

private:
    uint8_t _id;
    uint8_t _pin_in1;
    uint8_t _pin_in2;
    uint8_t _pwm_ch1;
    uint8_t _pwm_ch2;
    Encoder* _encoder;
    uint8_t _limit_pin;       // 255 if not using limit switch

    MotorMode _mode;
    uint8_t _direction;      // 0 = CW, 1 = CCW
    uint16_t _target_pulses;
    bool _is_running;

    uint32_t _start_time_ms;
    uint32_t _last_pulse_time_ms;
    uint32_t _last_pulse_count;

    char _current_instruction_id[48];
    char _last_completed_instruction_id[48];
    uint32_t _last_completed_time_ms;

    // Interval Braking State (10-pulse interval braking for 80ms during last 50 pulses)
    bool     _is_interval_braking;
    uint32_t _interval_brake_until_ms;
    uint32_t _next_interval_brake_pulse;

    void applyBinary(uint8_t drive_state, uint8_t direction);
    void interruptCurrentCommand();
    void completeNormalCommand();
};

#if ESP_ID == 3
// ESP 3: 2 Ordinary Motors with Limit Switches
extern MotorController motor1;
extern MotorController motor2;
#else
// ESP 1 & ESP 2: 6 Encoded Motors
extern MotorController motor1;
extern MotorController motor2;
extern MotorController motor3;
extern MotorController motor4;
extern MotorController motor5;
extern MotorController motor6;
#endif

void init_all_motors();
void update_all_motors(bool bus_data_state = false);
void release_any_active_lock();

#endif // MOTOR_H
