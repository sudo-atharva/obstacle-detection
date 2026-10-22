// Obstacle Detection - ESP32 + L293D motor driver
//
// The PC app decides the speed and sends it over USB serial (115200 baud), one line per command:
//   S<speed>     speed from 0.00 to 1.00, e.g. "S0.30" = 30 %, "S0" = stop
// Safety: if no command arrives for WATCHDOG_MS (app closed or crashed, cable unplugged),
// the motors stop.
// You can also type commands in the Arduino Serial Monitor to test the motors without the app.
//
// Board: any ESP32 dev board (e.g. ESP32-WROOM-32 DevKit). Works with Arduino-ESP32 core 2.x and 3.x.
// Wiring: see ../../docs/wiring.svg

// ---- Pins (L293D pin numbers in brackets) ----
const int ENA = 25;  // [1]  EN1  - speed of motor A (PWM)
const int IN1 = 26;  // [2]  IN1  - direction of motor A
const int IN2 = 27;  // [7]  IN2  - direction of motor A
const int ENB = 14;  // [9]  EN2  - speed of motor B (PWM)
const int IN3 = 32;  // [10] IN3  - direction of motor B
const int IN4 = 33;  // [15] IN4  - direction of motor B
const int STATUS_LED = 2;  // on-board LED: lit while the app is sending commands

// ---- Tuning ----
const int PWM_FREQ = 1000;               // Hz
const int PWM_BITS = 8;                  // duty 0..255
const int MIN_DUTY = 120;                // small DC motors stall below this; raise it if yours only hum
const unsigned long WATCHDOG_MS = 500;   // stop if the app goes quiet this long
const bool REVERSE_A = false;            // set true if motor A spins backwards (or swap its wires)
const bool REVERSE_B = false;

// ---- PWM helpers: the LEDC API changed in Arduino-ESP32 core 3.0 ----
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
void pwmSetup(int pin, int channel) { ledcAttach(pin, PWM_FREQ, PWM_BITS); }
void pwmWrite(int pin, int channel, int duty) { ledcWrite(pin, duty); }
#else
void pwmSetup(int pin, int channel) { ledcSetup(channel, PWM_FREQ, PWM_BITS); ledcAttachPin(pin, channel); }
void pwmWrite(int pin, int channel, int duty) { ledcWrite(channel, duty); }
#endif

unsigned long lastCommandMs = 0;
float currentSpeed = 0;
String line;

void setMotor(int en, int channel, int inA, int inB, bool reverse, float speed) {
  if (speed <= 0.01) {
    // Brake: both inputs equal with EN high stops the motor faster than letting it coast.
    digitalWrite(inA, LOW);
    digitalWrite(inB, LOW);
    pwmWrite(en, channel, 255);
    return;
  }
  digitalWrite(inA, reverse ? LOW : HIGH);
  digitalWrite(inB, reverse ? HIGH : LOW);
  int duty = MIN_DUTY + (int)((255 - MIN_DUTY) * min(speed, 1.0f));
  pwmWrite(en, channel, duty);
}

void setSpeed(float speed) {
  speed = constrain(speed, 0.0f, 1.0f);
  setMotor(ENA, 0, IN1, IN2, REVERSE_A, speed);
  setMotor(ENB, 1, IN3, IN4, REVERSE_B, speed);
  if (speed != currentSpeed) {
    Serial.printf("speed %.2f\n", speed);
  }
  currentSpeed = speed;
}

void handleLine(const String &cmd) {
  if (cmd.length() >= 2 && (cmd[0] == 'S' || cmd[0] == 's')) {
    setSpeed(cmd.substring(1).toFloat());
    lastCommandMs = millis();
  } else if (cmd.length() > 0) {
    Serial.println("unknown command (use S0.00 .. S1.00)");
  }
}

void setup() {
  Serial.begin(115200);
  for (int pin : {IN1, IN2, IN3, IN4, STATUS_LED}) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }
  pwmSetup(ENA, 0);
  pwmSetup(ENB, 1);
  setSpeed(0);
  Serial.println("obstacle_esp32 ready");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      line.trim();
      handleLine(line);
      line = "";
    } else if (line.length() < 32) {
      line += c;
    }
  }

  bool alive = millis() - lastCommandMs < WATCHDOG_MS;
  if (!alive && currentSpeed > 0) {
    setSpeed(0);
    Serial.println("watchdog: no commands, motors stopped");
  }
  digitalWrite(STATUS_LED, alive ? HIGH : LOW);
}
