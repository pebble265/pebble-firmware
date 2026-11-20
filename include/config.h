// pebble(a) hardware pins and timing. Change the pins to match your board.
#pragma once

#define FW_VERSION "1.0.0"
#define ROBOT_MODEL "A3120B"
#define ROBOT_COLOR "Space Black"

// --- pins (ESP32 DevKit) ---
#define PIN_BUTTON      0    // the big button on top (BOOT button on a DevKit), active LOW
#define PIN_LED         2    // status / "time for your meds" light
#define PIN_BUZZER      25   // passive buzzer (PWM)
#define PIN_BATTERY     34   // battery voltage through a 1:2 divider (ADC1 only: 32–39)
#define PIN_CHARGING    35   // HIGH while the charger is connected (optional, -1 to disable)
#define PIN_MOTOR_A1    26   // drive motor (H-bridge, e.g. TB6612 / L9110)
#define PIN_MOTOR_A2    27
#define PIN_DISPENSER   14   // dispenser servo/solenoid enable (optional, -1 to disable)

// --- battery (Li-ion 1S through a 1:2 divider) ---
#define BATTERY_EMPTY_MV 3300
#define BATTERY_FULL_MV  4150

// --- timing ---
#define HEARTBEAT_MS        60000UL   // POST /robot/heartbeat
#define TELEMETRY_MS        30000UL   // ThingsBoard telemetry
#define DOSE_POLL_MS       300000UL   // GET /robot/doses/today (backup for the realtime "dose:due")
#define PAIR_POLL_MS        10000UL   // while waiting for an owner
#define TOKEN_REFRESH_MS  3000000UL   // device token lasts 1 h: refresh after 50 min
#define MOVE_TIMEOUT_MS     10000UL   // safety: stop the motor if "stop" never arrives (app sends it on release)
#define REMINDER_BEEP_MS    30000UL   // beep again while a dose is waiting
