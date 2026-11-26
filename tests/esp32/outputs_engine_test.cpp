/*
 * outputs_engine_test — host proof for the output controller (spec 003 T15).
 *
 * WHY A HOST TEST: the trained spec-002 model is the minimal 4-class artifact
 * (`wake`, `noise`, `la`, `lb`), so on the bench only `la`/`lb` can reach the
 * output controller through recognition. The other contract behaviours
 * (`blink_45s`, `blink_until_cancel`, `cancel`, `motor_45s`, `pulse_2s`,
 * `pulse_05s`) are implemented for spec 006/005 reuse and are not reachable from
 * the trained set yet. This test drives the SAME translation unit the firmware
 * ships (`devices/esp32/ultron_voice/outputs.cpp`) with a fake clock and asserts
 * the contract timings and priority rules — the honest proof instead of a claim.
 *
 * It also asserts the command table the engine uses is the generated registry
 * (`config.h` ← `model/registry.json`), so the firmware never keeps a second
 * hand-written copy of the mapping.
 *
 * Built and run by tests/test_esp32_outputs.py (pytest):
 *   c++ -std=c++17 -O2 -Wall -Wextra -I devices/esp32/ultron_voice \
 *       tests/esp32/outputs_engine_test.cpp devices/esp32/ultron_voice/outputs.cpp \
 *       -o /tmp/outputs_engine_test && /tmp/outputs_engine_test
 */

#include <cstdio>
#include <cstring>
#include <vector>

#include "outputs.h"
#include "outputs_config.h" /* generated registry (config.h) → CommandSpec[] */
#include "pins.h"           /* the XIAO pin map this milestone ships          */

namespace {

int g_failures = 0;

void fail(const char *file, int line, const char *expr) {
  std::printf("FAIL %s:%d\n     %s\n", file, line, expr);
  g_failures++;
}

#define CHECK(cond)                              \
  do {                                           \
    if (!(cond)) fail(__FILE__, __LINE__, #cond); \
  } while (0)

#define CHECK_U32_EQ(got, want)                                                        \
  do {                                                                                 \
    const unsigned long g_ = (unsigned long)(got), w_ = (unsigned long)(want);         \
    if (g_ != w_) {                                                                    \
      std::printf("FAIL %s:%d\n     expected %lu, got %lu\n", __FILE__, __LINE__, w_, g_); \
      g_failures++;                                                                    \
    }                                                                                  \
  } while (0)

using ultron::Channel;
using ultron::ChannelMask;

struct Event {
  uint32_t t_ms;
  Channel ch;
  bool on;
};

/* Records every sink call, and flags a state repeat (a relay must never be told
 * ON twice without an OFF in between — NFR-009 "no toggle without an event"). */
class RecordingSink : public ultron::ChannelSink {
 public:
  void init(uint32_t now_ms) override {
    init_calls++;
    init_ms = now_ms;
  }

  void apply(Channel ch, bool on, uint32_t now_ms) override {
    const unsigned idx = (unsigned)ch;
    if (has_state_[idx] && state_[idx] == on) {
      repeats++;
      repeats_at_ms = now_ms;
    }
    has_state_[idx] = true;
    state_[idx] = on;
    if (now_ms < last_ms) out_of_order++;
    last_ms = now_ms;
    events.push_back({now_ms, ch, on});
  }

  bool last_state(Channel ch) const { return state_[(unsigned)ch]; }

  size_t on_transitions(Channel ch, uint32_t from_ms, uint32_t to_ms) const {
    size_t n = 0;
    for (const Event &e : events) {
      if (e.ch == ch && e.on && e.t_ms >= from_ms && e.t_ms <= to_ms) n++;
    }
    return n;
  }

  size_t last_transition_time(Channel ch, bool on) const {
    for (auto it = events.rbegin(); it != events.rend(); ++it) {
      if (it->ch == ch && it->on == on) return it->t_ms;
    }
    return 0;
  }

  size_t changes(Channel ch) const {
    size_t n = 0;
    for (const Event &e : events) {
      if (e.ch == ch) n++;
    }
    return n;
  }

  void reset() { events.clear(); }

  int init_calls = 0;
  uint32_t init_ms = 0;
  int repeats = 0;
  uint32_t repeats_at_ms = 0;
  int out_of_order = 0;
  std::vector<Event> events;

 private:
  bool has_state_[ultron::kChannelCount] = {};
  bool state_[ultron::kChannelCount] = {};
  uint32_t last_ms = 0;
};

struct Harness {
  ultron::RegistryCommandTable registry{};
  RecordingSink sink;
  ultron::OutputController out;
  uint32_t now_ms = 0;

  Harness() {
    ultron::loadRegistryTable(&registry);
    out.begin(registry.table, &sink, 0);
  }

  bool command(const char *id, uint32_t t_ms) {
    CHECK(t_ms >= now_ms);
    now_ms = t_ms;
    return out.onCommand(id, t_ms);
  }

  /* Tick at `t_ms`, then advance to `to_ms` in 50 ms steps (the firmware tick). */
  void tickTo(uint32_t to_ms, uint32_t step_ms = ultron::kTickMs) {
    CHECK(step_ms > 0);
    for (uint32_t t = now_ms; t <= to_ms; t += step_ms) {
      out.tick(t);
      now_ms = t;
    }
    now_ms = to_ms;
  }
};

/* Contract blink formula: 1 s ON / 1 s OFF, phase origin = command time, and OFF
 * once the 45 s window (or CP) has ended. Used to assert every tick, not just
 * the edges, so a phase drift cannot slip through. */
bool expect_blink_on(uint32_t now_ms, uint32_t start_ms, uint32_t window_ms) {
  if ((uint32_t)(now_ms - start_ms) >= window_ms) return false;
  return ((uint32_t)(now_ms - start_ms) / ultron::kBlinkHalfPeriodMs) % 2u == 0u;
}

/* Walk the clock and assert `ch` follows the contract blink exactly. Returns the
 * first mismatching tick (0 when the whole run matched). */
uint32_t check_blink(Harness &h, Channel ch, uint32_t start_ms, uint32_t window_ms,
                     uint32_t until_ms) {
  uint32_t first_bad = 0;
  for (uint32_t t = h.now_ms; t <= until_ms; t += ultron::kTickMs) {
    h.tickTo(t);
    const bool want = expect_blink_on(t, start_ms, window_ms);
    if (h.out.isOn(ch) != want) {
      if (first_bad == 0) first_bad = t;
    }
  }
  return first_bad;
}

/* ── 1. the engine's command table is the generated registry ─────────────── */

void test_registry_table() {
  ultron::RegistryCommandTable rt{};
  ultron::loadRegistryTable(&rt);

  CHECK_U32_EQ(rt.table.count, ULTRON_REGISTRY_SIZE);
  CHECK_U32_EQ(rt.table.count, 11); /* model/registry.json v2: 11 commands */
  CHECK_U32_EQ(ultron::unknownBehaviors(rt), 0);
  CHECK_U32_EQ(ultron::unknownChannels(rt, nullptr), 0);

  /* The two trained commands resolve to the registry's behavior + channels. */
  for (int i = 0; i < ULTRON_NUM_COMMANDS; i++) {
    const ultron::CommandSpec *spec = rt.table.find(ULTRON_COMMANDS[i].id);
    CHECK(spec != nullptr);
    if (spec == nullptr) continue;
    CHECK_U32_EQ(spec->channels,
                 ultron::channelsFromCsv(ULTRON_COMMANDS[i].channels));
    CHECK(spec->behavior == ultron::behaviorFromName(ULTRON_COMMANDS[i].behavior));
  }

  const ultron::CommandSpec *lm = rt.table.find("lm");
  const ultron::CommandSpec *lb = rt.table.find("lb");
  const ultron::CommandSpec *la = rt.table.find("la");
  const ultron::CommandSpec *cp = rt.table.find("cp");
  const ultron::CommandSpec *lpb = rt.table.find("lpb");
  const ultron::CommandSpec *lpa = rt.table.find("lpa");
  const ultron::CommandSpec *a = rt.table.find("a");
  const ultron::CommandSpec *cc = rt.table.find("cc");
  CHECK(lm && lb && la && cp && lpb && lpa && a && cc);
  if (lm && lb && la && cp && lpb && lpa && a && cc) {
    CHECK_U32_EQ(lm->channels, ultron::channelBit(ultron::kChLm));
    CHECK_U32_EQ(lb->channels, ultron::channelBit(ultron::kChLb));
    CHECK_U32_EQ(la->channels,
                 ultron::channelBit(ultron::kChLa) | ultron::channelBit(ultron::kChLb) |
                     ultron::channelBit(ultron::kChLm));
    CHECK_U32_EQ(cp->channels, 0); /* CP drives no channel — FR-017 */
    CHECK(lpb->behavior == ultron::Behavior::kMotor45s);
    CHECK(lpa->behavior == ultron::Behavior::kMotor45s);
    CHECK(a->behavior == ultron::Behavior::kPulse2s);
    CHECK(cc->behavior == ultron::Behavior::kPulse05s);
    CHECK(cp->behavior == ultron::Behavior::kCancel);
  }

  /* Names round-trip (channelName ↔ channelsFromCsv). */
  for (unsigned i = 0; i < (unsigned)ultron::kChannelCount; i++) {
    const Channel ch = (Channel)i;
    Channel parsed = ultron::kChLm;
    CHECK(ultron::channelFromName(ultron::channelName(ch), &parsed));
    CHECK(parsed == ch);
    CHECK_U32_EQ(ultron::channelsFromCsv(ultron::channelName(ch)), ultron::channelBit(ch));
  }

  /* The engine's tick is the 50 ms the design calls for (FR-009 tolerances). */
  CHECK_U32_EQ(ultron::kTickMs, 50);
}

/* ── 2. pin map: this milestone wires LM + LB only ───────────────────────── */

void test_pin_map() {
  CHECK_U32_EQ(ultron::wiredChannels(), 2);
  CHECK_U32_EQ(ultron::kChannelPins[ultron::kChLm].gpio, 6);  /* D5 */
  CHECK_U32_EQ(ultron::kChannelPins[ultron::kChLb].gpio, 7);  /* D8 */
  CHECK(ultron::kChannelPins[ultron::kChLm].wired);
  CHECK(ultron::kChannelPins[ultron::kChLb].wired);
  CHECK(ultron::kChannelPins[ultron::kChLm].active_low);
  CHECK(ultron::kChannelPins[ultron::kChLb].active_low);
  CHECK(!ultron::kChannelPins[ultron::kChLa].wired); /* LA lands in spec 006 */
  for (unsigned i = 2; i < (unsigned)ultron::kChannelCount; i++) {
    CHECK(!ultron::kChannelPins[i].wired);
  }
}

/* ── 3. boot safety (NFR-009) ────────────────────────────────────────────── */

void test_boot_is_off_and_stays_off_until_a_command() {
  Harness h;
  CHECK_U32_EQ(h.sink.init_calls, 1); /* the adapter got its explicit OFF pass */
  CHECK_U32_EQ(h.out.onMask(), 0);
  CHECK_U32_EQ(h.sink.events.size(), 0); /* nothing driven, nothing toggled */

  h.tickTo(60000); /* a minute of ticks with no command */
  CHECK_U32_EQ(h.out.onMask(), 0);
  CHECK_U32_EQ(h.sink.events.size(), 0);

  /* Unknown ids are rejected without touching a channel. */
  CHECK(!h.command("nope", 60050));
  CHECK_U32_EQ(h.out.onMask(), 0);
  CHECK_U32_EQ(h.sink.events.size(), 0);
}

/* ── 4. steady / luz hierarchy (the 2 wired channels + LA unwired) ───────── */

void test_luz_hierarchy_and_replacement() {
  Harness h;

  /* `lm` → LM only, latched (steady for the whole run). */
  CHECK(h.command("lm", 1000));
  CHECK_U32_EQ(h.out.onMask(), ultron::channelBit(ultron::kChLm));
  CHECK_U32_EQ(h.sink.events.size(), 1);
  CHECK(h.sink.events[0].ch == ultron::kChLm && h.sink.events[0].on);
  CHECK_U32_EQ(h.sink.events[0].t_ms, 1000);

  h.tickTo(400000); /* 6.6 minutes: still steady, no edges */
  CHECK_U32_EQ(h.out.onMask(), ultron::channelBit(ultron::kChLm));
  CHECK_U32_EQ(h.sink.events.size(), 1);

  /* `lb` → LM + LB (contract) even though the registry lists only `lb`. */
  const size_t before = h.sink.events.size();
  CHECK(h.command("lb", 400050));
  CHECK_U32_EQ(h.out.onMask(), ultron::channelBit(ultron::kChLm) |
                                   ultron::channelBit(ultron::kChLb));
  CHECK_U32_EQ(h.sink.events.size(), before + 1); /* LM was already ON */
  CHECK(h.sink.events.back().ch == ultron::kChLb && h.sink.events.back().on);

  h.tickTo(600000);
  CHECK_U32_EQ(h.out.onMask(), ultron::channelBit(ultron::kChLm) |
                                   ultron::channelBit(ultron::kChLb));

  /* `la` → LM + LB + LA (LA unwired: the engine still owns the desired state). */
  CHECK(h.command("la", 600050));
  CHECK_U32_EQ(h.out.onMask(), ultron::channelBit(ultron::kChLm) |
                                   ultron::channelBit(ultron::kChLb) |
                                   ultron::channelBit(ultron::kChLa));
  CHECK(h.out.isOn(ultron::kChLa)); /* the sink logs `unwired` for it */

  /* A luz command replaces the whole luz state: `lm` drops LB and LA. */
  CHECK(h.command("lm", 700000));
  CHECK_U32_EQ(h.out.onMask(), ultron::channelBit(ultron::kChLm));
  CHECK(!h.out.isOn(ultron::kChLb));
  CHECK(!h.out.isOn(ultron::kChLa));

  /* Re-selecting the same luz level is a no-op on the wires. */
  const size_t after_replace = h.sink.events.size();
  CHECK(h.command("lm", 700050));
  CHECK_U32_EQ(h.sink.events.size(), after_replace);

  CHECK_U32_EQ(h.sink.repeats, 0); /* never told the same state twice */
}

/* ── 5. blink_45s: 1 s ON / 1 s OFF for exactly 45 s (FR-009) ────────────── */

void test_blink_45s() {
  Harness h;
  CHECK(h.command("gd", 1000));
  CHECK(h.out.isOn(ultron::kChGd));

  /* Every 50 ms tick for 60 s must match the contract phase. */
  CHECK_U32_EQ(check_blink(h, ultron::kChGd, 1000, ultron::kBlinkWindowMs, 61000), 0);

  /* Exactly 23 ON intervals in 45 s (t = 0, 2, …, 44 s), then silent. */
  CHECK_U32_EQ(h.sink.on_transitions(ultron::kChGd, 1000, 100000), 23);
  CHECK_U32_EQ(h.sink.changes(ultron::kChGd), 46); /* 23 ON + 23 OFF */

  /* Edges land exactly on the 1 s boundaries the contract names. */
  CHECK_U32_EQ(h.sink.events[0].t_ms, 1000);        /* first ON  */
  CHECK_U32_EQ(h.sink.events[1].t_ms, 2000);        /* first OFF */
  CHECK_U32_EQ(h.sink.events[2].t_ms, 3000);        /* second ON */
  CHECK_U32_EQ(h.sink.last_transition_time(ultron::kChGd, false), 46000);
  CHECK_U32_EQ(h.sink.last_transition_time(ultron::kChGd, true), 45000);

  /* Window length: last ON starts at start + 44 s, the window ends at +45 s. */
  CHECK_U32_EQ(h.out.onMask(), 0);

  /* A second blink restarts the window from the new command. */
  CHECK(h.command("gi", 100000));
  CHECK(h.out.isOn(ultron::kChGi));
  CHECK_U32_EQ(check_blink(h, ultron::kChGi, 100000, ultron::kBlinkWindowMs, 145000), 0);
  CHECK(!h.out.isOn(ultron::kChGi));
  CHECK_U32_EQ(h.sink.repeats, 0);
}

/* ── 6. blink_until_cancel + CP (FR-017) ─────────────────────────────────── */

void test_parqueo_and_cancel() {
  Harness h;
  CHECK(h.command("p", 0));
  CHECK(h.out.isOn(ultron::kChGd));
  CHECK(h.out.isOn(ultron::kChGi));

  /* In phase, forever: 120 s of ticks, both sides always equal, never expiring. */
  for (uint32_t t = 0; t <= 120000; t += ultron::kTickMs) {
    h.tickTo(t);
    if (h.out.isOn(ultron::kChGd) != h.out.isOn(ultron::kChGi)) {
      fail(__FILE__, __LINE__, "parqueo sides out of phase");
      break;
    }
    if (h.out.isOn(ultron::kChGd) != expect_blink_on(t, 0, UINT32_MAX)) {
      fail(__FILE__, __LINE__, "parqueo phase wrong");
      break;
    }
  }
  h.tickTo(120000);
  /* t = 120 s lands on an ON half-second: parqueo is still running. */
  CHECK_U32_EQ(h.out.onMask(), ultron::channelBit(ultron::kChGd) |
                                   ultron::channelBit(ultron::kChGi));

  /* CP stops parqueo immediately — the edge lands inside the command call, i.e.
   * 0 ms of latency, well inside FR-009's < 200 ms budget. The command is placed
   * between ticks on purpose: CP must not wait for the output tick. */
  const uint32_t cp_t = 120517;
  const size_t gd_changes = h.sink.changes(ultron::kChGd);
  CHECK(h.command("cp", cp_t));
  CHECK_U32_EQ(h.out.onMask(), 0);
  CHECK_U32_EQ(h.sink.changes(ultron::kChGd), gd_changes + 1); /* one OFF edge */
  const uint32_t stopped_at = (uint32_t)h.sink.last_transition_time(ultron::kChGd, false);
  CHECK_U32_EQ(stopped_at, cp_t);
  CHECK_U32_EQ(h.sink.last_transition_time(ultron::kChGi, false), cp_t);
  CHECK(stopped_at - cp_t <= ultron::kCpStopBudgetMs);

  /* No resurrection: 60 s of ticks stay OFF. */
  h.tickTo(180000);
  CHECK_U32_EQ(h.out.onMask(), 0);
  CHECK_U32_EQ(h.sink.on_transitions(ultron::kChGd, 0, 180000), 61); /* phase only */

  /* CP with parqueo inactive is a no-op (FR-017), twice in a row. */
  const size_t before = h.sink.events.size();
  CHECK(!h.command("cp", 180050));
  CHECK(!h.command("cp", 180100));
  CHECK_U32_EQ(h.sink.events.size(), before);
  CHECK_U32_EQ(h.out.onMask(), 0);
}

/* ── 7. priority: parqueo ↔ single-side blink ────────────────────────────── */

void test_priority_rules() {
  /* Starting GD while parqueo is active cancels parqueo and starts the 45 s
   * single-side blink; GI goes OFF at that instant. */
  {
    Harness h;
    CHECK(h.command("p", 0));
    h.tickTo(500);
    CHECK(h.out.isOn(ultron::kChGd) && h.out.isOn(ultron::kChGi));

    CHECK(h.command("gd", 500));
    CHECK(!h.out.isOn(ultron::kChGi)); /* parqueo cancelled */
    CHECK(h.out.isOn(ultron::kChGd));
    CHECK_U32_EQ(check_blink(h, ultron::kChGd, 500, ultron::kBlinkWindowMs, 45500), 0);
    CHECK_U32_EQ(h.out.onMask(), 0);
    CHECK(!h.out.isOn(ultron::kChGi)); /* GI stays OFF after the side blink ends */
    CHECK_U32_EQ(h.sink.repeats, 0);
  }

  /* Starting parqueo overrides an active single-side blink: both sides blink in
   * phase from the parqueo command, and the 45 s window no longer applies. */
  {
    Harness h;
    CHECK(h.command("gd", 0));
    h.tickTo(900);
    CHECK(h.out.isOn(ultron::kChGd)); /* mid ON half-second */

    CHECK(h.command("p", 900));
    CHECK(h.out.isOn(ultron::kChGd));
    CHECK(h.out.isOn(ultron::kChGi)); /* new phase origin at 900 → ON */
    h.tickTo(1900);
    CHECK(!h.out.isOn(ultron::kChGd)); /* both sides OFF together */
    CHECK(!h.out.isOn(ultron::kChGi));
    h.tickTo(40900);
    CHECK(h.out.isOn(ultron::kChGd)); /* still blinking well past 45 s */
    CHECK(h.out.isOn(ultron::kChGi));
    CHECK_U32_EQ(h.sink.repeats, 0);
  }
}

/* ── 8. motor_45s: fixed window, LPB/LPA mutually exclusive (Q-011) ──────── */

void test_motor_45s() {
  Harness h;
  CHECK(h.command("lpb", 0));
  CHECK(h.out.isOn(ultron::kChLpb));

  for (uint32_t t = 0; t <= 44000; t += ultron::kTickMs) {
    h.tickTo(t);
    CHECK(h.out.isOn(ultron::kChLpb));
    if (h.out.onMask() != ultron::channelBit(ultron::kChLpb)) break;
  }
  CHECK_U32_EQ(h.sink.changes(ultron::kChLpb), 1); /* one ON, no Off before 45 s */

  h.tickTo(45000);
  CHECK(!h.out.isOn(ultron::kChLpb)); /* exactly 45 s → OFF */
  CHECK_U32_EQ(h.sink.last_transition_time(ultron::kChLpb, false), 45000);

  h.tickTo(100000);
  CHECK(!h.out.isOn(ultron::kChLpb)); /* does not restart by itself */

  /* Switching speed cancels the running window and starts the other one. */
  CHECK(h.command("lpb", 100000));
  CHECK(h.out.isOn(ultron::kChLpb));
  CHECK(h.command("lpa", 110000));
  CHECK(!h.out.isOn(ultron::kChLpb));
  CHECK(h.out.isOn(ultron::kChLpa));
  h.tickTo(145000); /* the old LPB deadline does nothing to LPA */
  CHECK(h.out.isOn(ultron::kChLpa));
  h.tickTo(154950);
  CHECK(h.out.isOn(ultron::kChLpa));
  h.tickTo(155000);
  CHECK(!h.out.isOn(ultron::kChLpa)); /* 45 s after the *switch*, not the lpb start */

  /* Re-selecting the same speed restarts the window. */
  CHECK(h.command("lpa", 200000));
  CHECK(h.out.isOn(ultron::kChLpa));
  h.tickTo(220000);
  CHECK(h.command("lpa", 220000)); /* re-select while running */
  CHECK(h.out.isOn(ultron::kChLpa));
  h.tickTo(264950);
  CHECK(h.out.isOn(ultron::kChLpa));
  h.tickTo(265000);
  CHECK(!h.out.isOn(ultron::kChLpa)); /* off 45 s after the restart */
  CHECK_U32_EQ(h.sink.repeats, 0);
}

/* ── 9. pulse_2s / pulse_05s: 2 s / 0.5 s (±50 ms) ──────────────────────── */

void test_pulses() {
  Harness h;
  CHECK(h.command("a", 1000));   /* arranque */
  CHECK(h.command("cc", 1000));  /* cierre centralizado */
  CHECK(h.out.isOn(ultron::kChA));
  CHECK(h.out.isOn(ultron::kChCc));

  h.tickTo(1500);
  CHECK(!h.out.isOn(ultron::kChCc)); /* 0.5 s */
  CHECK(h.out.isOn(ultron::kChA));
  h.tickTo(3000);
  CHECK(!h.out.isOn(ultron::kChA)); /* 2 s */

  const uint32_t cc_off = (uint32_t)h.sink.last_transition_time(ultron::kChCc, false);
  const uint32_t a_off = (uint32_t)h.sink.last_transition_time(ultron::kChA, false);
  CHECK_U32_EQ(cc_off - 1000, ultron::kPulse05sMs);
  CHECK_U32_EQ(a_off - 1000, ultron::kPulse2sMs);
  CHECK(cc_off - 1000 <= ultron::kPulse05sMs + ultron::kPulseToleranceMs);
  CHECK(a_off - 1000 <= ultron::kPulse2sMs + ultron::kPulseToleranceMs);

  /* Re-commanding mid-pulse restarts it; the channel never blips OFF. */
  CHECK(h.command("a", 4000));
  h.tickTo(5000);
  CHECK(h.command("a", 5000)); /* restart while still ON */
  h.tickTo(6900);
  CHECK(h.out.isOn(ultron::kChA));
  h.tickTo(7000);
  CHECK(!h.out.isOn(ultron::kChA)); /* 2 s after the restart */
  CHECK_U32_EQ(h.sink.changes(ultron::kChA), 4); /* ON, OFF, ON, OFF — no extra ON */

  /* Nothing turns a pulse back on without a command. */
  h.tickTo(20000);
  CHECK_U32_EQ(h.sink.on_transitions(ultron::kChA, 7000, 20000), 0);
  CHECK_U32_EQ(h.sink.repeats, 0);
}

/* ── 10. groups are independent; nothing toggles without a command ───────── */

void test_groups_are_independent_and_quiet_without_commands() {
  Harness h;
  CHECK(h.command("lm", 0));
  CHECK(h.command("gd", 0));
  CHECK(h.command("lpb", 0));
  CHECK(h.command("a", 0));

  const ChannelMask want_initial = ultron::channelBit(ultron::kChLm) |
                                   ultron::channelBit(ultron::kChGd) |
                                   ultron::channelBit(ultron::kChLpb) |
                                   ultron::channelBit(ultron::kChA);
  CHECK_U32_EQ(h.out.onMask(), want_initial);

  /* CP must never touch the wipers (requirements: "CP affects parqueo only"). */
  CHECK(!h.command("cp", 100));
  CHECK(h.out.isOn(ultron::kChLpb));

  /* After every window and pulse has expired, the only channel left is the
   * latched luz. No ON edge appears without a command. */
  h.tickTo(60000);
  CHECK_U32_EQ(h.out.onMask(), ultron::channelBit(ultron::kChLm));
  for (unsigned i = 0; i < (unsigned)ultron::kChannelCount; i++) {
    const Channel ch = (Channel)i;
    if (ch == ultron::kChLm) continue;
    CHECK_U32_EQ(h.sink.on_transitions(ch, 50000, 60000), 0);
  }
  CHECK_U32_EQ(h.sink.repeats, 0);
  CHECK_U32_EQ(h.sink.out_of_order, 0);
}

}  // namespace

int main() {
  test_registry_table();
  test_pin_map();
  test_boot_is_off_and_stays_off_until_a_command();
  test_luz_hierarchy_and_replacement();
  test_blink_45s();
  test_parqueo_and_cancel();
  test_priority_rules();
  test_motor_45s();
  test_pulses();
  test_groups_are_independent_and_quiet_without_commands();

  if (g_failures != 0) {
    std::printf("\noutputs_engine_test: %d check(s) FAILED\n", g_failures);
    return 1;
  }
  std::printf(
      "outputs_engine_test: OK\n"
      "  luz         lm → LM; lb → LM+LB; la → LM+LB+LA (LA unwired); replacement ok\n"
      "  blink_45s   1 s ON / 1 s OFF, 23 ON intervals, OFF at +45000 ms\n"
      "  parqueo     GD+GI in phase, CP stops in 0 ms (< 200 ms), CP no-op when idle\n"
      "  priority    GD/GI cancels parqueo; parqueo overrides a side blink\n"
      "  motor_45s   45000 ms window, LPB/LPA exclusive, re-select restarts\n"
      "  pulses      A = 2000 ms, CC = 500 ms, restart-safe, no spontaneous edges\n"
      "  boot        all channels OFF at begin(), no sink call before a command\n"
      "  registry    command table generated from model/registry.json (11 entries)\n"
      "  pins        LM D5/GPIO6, LB D8/GPIO7, active-LOW, 2 wired of 9\n");
  return 0;
}
