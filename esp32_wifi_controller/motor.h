#ifndef MOTOR_H
#define MOTOR_H

#include <Arduino.h>
#include "config.h"
#include "encoder.h"

enum MotorMode {
    MODE_IDLE = 0,
    MODE_NORMAL = 1,
    MODE_LOCK_ON = 2,
    MODE_BRAKED = 3,
    MODE_PROFILED = 4
};

enum DurationType {
    DURATION_PULSE = 0,
    DURATION_TIME = 1
};

struct BrakeSection {
    char name[8];               // "A", "B", etc.
    DurationType duration_type; // DURATION_PULSE or DURATION_TIME
    uint32_t start;
    uint32_t end;
    uint32_t brake_count;
    uint32_t brake_ms;

    // Runtime state
    uint32_t effective_count;   // min(brake_count, end - start)
    uint32_t current_k;         // Next brake index (0 .. effective_count - 1)
    bool active;
};

struct MotorProfile {
    BrakeSection sections[MAX_PROFILE_SECTIONS];
    uint8_t section_count;
};

class MotorController {
public:
    MotorController(uint8_t id, uint8_t pin_in1, uint8_t pin_in2,
                    uint8_t pwm_ch1 = 0, uint8_t pwm_ch2 = 0,
                    Encoder* encoder = nullptr, uint8_t limit_pin = 255);

    void begin();

    // Profile command dispatcher (new profile mode)
    bool startProfile(uint32_t target_pulses, uint8_t direction, const MotorProfile& profile,
                      const char* instruction_id, char* err_msg, size_t err_len);

    // Legacy command dispatchers
    void startNormalCommand(uint16_t target_pulses, uint8_t direction, const char* instruction_id = "");
    void startLockOn(uint8_t direction, const char* instruction_id = "");
    void releaseLockOn();

    const char* getInstructionId() const { return _current_instruction_id; }

    // Main non-blocking update loop (called in loop())
    void update();

    // Interrupt/Timer handlers (called from timer ISR and encoder ISR)
    void IRAM_ATTR onPulseEdge();
    void IRAM_ATTR onTimer1ms();

    // Stop and brake immediately (clamps IN pins HIGH for short brake)
    void brake();

    // Status queries
    MotorMode getMode() const { return _mode; }
    bool isLockOn() const { return _mode == MODE_LOCK_ON; }
    bool isBraked() const { return _mode == MODE_BRAKED; }
    bool isRunning() const { return _is_running; }
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
    uint8_t _direction;       // 0 = CW, 1 = CCW
    uint32_t _target_pulses;
    volatile bool _is_running;

    // Profile definition & runtime
    MotorProfile _profile;
    volatile uint32_t _pulses;
    volatile uint32_t _driven_time_ms;
    volatile uint32_t _driven_time_at_watchdog_start;
    volatile uint32_t _brake_hold_remaining_ms;
    volatile bool _is_braking;

    // Feedback flags processed in update()
    volatile bool _completed;
    volatile bool _jammed;
    char _jam_section[8];
    volatile bool _config_error;
    char _error_msg[64];

    // Legacy fields
    uint32_t _start_time_ms;
    uint32_t _last_pulse_time_ms;
    uint32_t _last_pulse_count;
    char _current_instruction_id[48];
    char _last_completed_instruction_id[48];
    uint32_t _last_completed_time_ms;

    // Legacy interval braking
    bool     _is_interval_braking;
    uint32_t _interval_brake_until_ms;
    uint32_t _next_interval_brake_pulse;

    void applyBinary(uint8_t drive_state, uint8_t direction);
    void interruptCurrentCommand();
    void completeNormalCommand();
    void findActiveSection(char* out, size_t out_len) const;
};

#if ESP_ID == 3
extern MotorController motor1;
extern MotorController motor2;
#else
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
MotorController* get_motor(uint8_t index);

#endif // MOTOR_H
