# Design — 006 Can Act Full (draft stub)

The companion-stack design is preserved from the earlier spec 003 draft:

- **Dual-MCU over UART** (TD-012, spec 003): XIAO voice + companion ESP32-S3 SuperMini
  actuation; the same `EVT` contract lines, 115200-8N1, one-way.
- **SuperMini pin map**: relays on GPIO1…GPIO10 (non-strapping pins; S3 strapping 0/3/45/46
  avoided), active-LOW relay board with pull-ups to 3V3, coils on external 5 V (JD-VCC jumper
  removed), common GND bus.
- **Wiring schema**: preserved in spec 003's design (section "Wiring — companion stack"), moved
  here in full when this spec is drafted.
- **Rollout mechanics**: per-command retrain/export/redeploy cycles (FR-013; FR-032, spec 002),
  identical flatbuffer on every runtime (AGENTS rule 5).

This stub exists so the deferred scope has an explicit home; it will be expanded (requirements
mapping, edge cases, TDs, verification) at drafting time.
