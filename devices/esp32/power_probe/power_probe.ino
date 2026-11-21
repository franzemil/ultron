// power_probe — hardware-validation SPIKE: drives D10 (GPIO9) at constant 3.3 V
// so the amp's VIN can be test-powered from a GPIO. Console prints a heartbeat.
// Amp VIN min is 2.5 V → 3.3 V from a GPIO is enough to power/enable it (LED test).

constexpr int PIN_PROBE = 9;   // D10

void setup() {
  Serial.begin(115200);
  delay(2000);
  pinMode(PIN_PROBE, OUTPUT);
  digitalWrite(PIN_PROBE, HIGH);
  Serial.println("POWER PROBE: D10 = 3.3V constant. Move amp VIN here and watch its LED.");
}

void loop() {
  Serial.println("probe alive");
  delay(2000);
}
