/*
 * outputs — the ultron output controller (spec 003-can-act, T15).
 *
 * Consumes `CMD` events and drives relay channels per the **Actuation Behavior
 * Contract** (requirements.md) with the engines listed in design.md → "Output
 * controller":
 *
 *   registry behavior      engine
 *   ---------------------  ----------------------------------------------------
 *   steady                 latch the luz group; a luz command replaces the state
 *   blink_45s              1 s ON / 1 s OFF for exactly 45 s, then OFF
 *   blink_until_cancel     GD + GI blink in phase until CP (FR-017)
 *   cancel (CP)            stop parqueo; no-op when parqueo is not active
 *   motor / motor_45s      motor channel ON for a fixed 45 s window (Q-011)
 *   pulse_2s / pulse_05s   hold the channel exactly 2 s / 0.5 s, then OFF
 *
 * Priority rules (requirements.md): GD/GI while parqueo is active cancels the
 * hazard blink and starts the single-side blink; parqueo overrides a single-side
 * blink; a luz command replaces the previous luz state; LPB/LPA are mutually
 * exclusive (switching cancels the running window, re-selecting restarts it);
 * CP affects parqueo only — never the wipers.
 *
 * Why this file has no hardware in it (AGENTS rule 5): spec 006 runs the same
 * engines on the companion ESP32-S3 SuperMini and spec 005 runs them on the Pi.
 * The behaviour layer therefore speaks only in **channel ids** and receives
 * time from its caller; a `ChannelSink` implementation owns the pins (XIAO:
 * active-LOW GPIOs; Pi: libgpiod; companion: its own relay board). Timing is
 * driven by a 50 ms non-blocking tick — nothing here blocks or sleeps, so the
 * XIAO's classification loop never pauses for outputs. Because a blocking mic
 * read keeps the loop at ~100 ms, the XIAO runs the tick from a 50 ms timer and
 * publishes the resulting edges through `takeEdges()` (see relay_gpio.h); the
 * engine itself is tick-source agnostic.
 *
 * Requirements covered: FR-009 (drive mapped channels within tolerances),
 * FR-017 (CP), NFR-009 (all channels OFF at boot; no toggle without a command).
 *
 * Host test: tests/esp32/outputs_engine_test.cpp drives this file with a fake
 * clock and asserts every engine + timing tolerance (tests/test_esp32_outputs.py
 * compiles and runs it) — the accepted proof for the commands the current model
 * does not recognise yet.
 */

#ifndef ULTRON_OUTPUTS_H
#define ULTRON_OUTPUTS_H

#include <stddef.h>
#include <stdint.h>

namespace ultron {

/* ── channel vocabulary — the 9 contract channels, in contract table order ── */

enum Channel : uint8_t {
  kChLm = 0, /* luz media            */
  kChLb,     /* luz baja             */
  kChLa,     /* luz alta             */
  kChGd,     /* guiñador derecho     */
  kChGi,     /* guiñador izquierdo   */
  kChLpb,    /* limpia parabrisas baja  */
  kChLpa,    /* limpia parabrisas alta  */
  kChA,      /* arranque             */
  kChCc,     /* cierre centralizado  */
  kChannelCount
};

typedef uint16_t ChannelMask;

constexpr ChannelMask channelBit(Channel ch) { return (ChannelMask)(1u << (unsigned)ch); }
constexpr ChannelMask kAllChannels = (ChannelMask)((1u << (unsigned)kChannelCount) - 1u);

/* Registry channel id ("lm" … "cc"). Returns "?" for an out-of-range value. */
const char *channelName(Channel ch);

/* Parse one registry channel id. Returns false when the name is not a channel. */
bool channelFromName(const char *name, Channel *out);

/* Parse a registry channel CSV ("la,lb,lm", empty string → mask 0). Unknown
 * tokens are skipped; the caller can compare popcount with the token count when
 * it needs to reject a bad registry entry. */
ChannelMask channelsFromCsv(const char *csv);

/* ── behaviour vocabulary — the registry `behavior` enum ─────────────────── */

enum class Behavior : uint8_t {
  kUnknown = 0,
  kSteady,           /* "steady"             */
  kBlink45s,         /* "blink_45s"          */
  kBlinkUntilCancel, /* "blink_until_cancel" */
  kCancel,           /* "cancel"             */
  kMotor45s,         /* "motor" | "motor_45s" */
  kPulse2s,          /* "pulse_2s"           */
  kPulse05s          /* "pulse_05s"          */
};

const char *behaviorName(Behavior b);

/* Registry string → enum. Unknown strings map to Behavior::kUnknown (the caller
 * must treat that as a registry/config error, never as "do nothing quietly"). */
Behavior behaviorFromName(const char *name);

/* ── contract timings (requirements.md → Actuation Behavior Contract) ────── */

constexpr uint32_t kTickMs = 50;              /* output tick           */
constexpr uint32_t kBlinkHalfPeriodMs = 1000; /* 1 s ON / 1 s OFF      */
constexpr uint32_t kBlinkWindowMs = 45000;    /* GD/GI window          */
constexpr uint32_t kMotorWindowMs = 45000;    /* LPB/LPA window (Q-011) */
constexpr uint32_t kPulse2sMs = 2000;         /* arranque (A)          */
constexpr uint32_t kPulse05sMs = 500;         /* cierre centralizado   */

/* FR-009 tolerances the engines are built to hit. */
constexpr uint32_t kPulseToleranceMs = 50;
constexpr uint32_t kBlinkToleranceMs = 100;
constexpr uint32_t kCpStopBudgetMs = 200;

/* ── command table — filled from the generated registry (config.h) ────────── */

struct CommandSpec {
  const char *id;      /* registry id, matches EVT CMD id=<id> */
  Behavior behavior;   /* registry behavior enum               */
  ChannelMask channels; /* registry channel list               */
};

struct CommandTable {
  const CommandSpec *items;
  size_t count;

  const CommandSpec *find(const char *id) const;
};

/* ── pin/tick adapter boundary ───────────────────────────────────────────── */

/*
 * The only hardware-aware part of the output path. `init` is called once from
 * OutputController::begin: the adapter must put every physical output in its
 * OFF state there (pins, pin modes, active level) and may log the pin map.
 * `apply` is called exactly when a channel's *desired* state changes — never on
 * a repeat of the same state — so a relay only ever moves on a command-driven
 * edge (NFR-009). Adapters that own an unwired channel log it instead of
 * driving a pin (this milestone: LA, GD, GI, LPB, LPA, A, CC).
 */
class ChannelSink {
 public:
  virtual ~ChannelSink() {}

  virtual void init(uint32_t now_ms) = 0;
  virtual void apply(Channel ch, bool on, uint32_t now_ms) = 0;
};

/* ── the engine ──────────────────────────────────────────────────────────── */

class OutputController {
 public:
  OutputController() = default;
  OutputController(const OutputController &) = delete;
  OutputController &operator=(const OutputController &) = delete;

  /*
   * Bind the command table and the pin layer, then reset to the boot-safe state
   * (NFR-009: every channel OFF). `now_ms` is the boot timestamp — the caller
   * owns the clock, so the same engine runs on the XIAO, the companion and the
   * host test.
   */
  void begin(const CommandTable &table, ChannelSink *sink, uint32_t now_ms);

  /*
   * Runtime fail-safe (NFR-009): drop every behaviour and drive every channel
   * OFF. Used when the voice pipeline reports a fault — a device that cannot
   * hear must not leave a luz latched ON. No edge is produced until a new
   * command arrives.
   */
  void failSafe(uint32_t now_ms);

  /*
   * Consume one CMD event (FR-009). Returns true when the id is in the table and
   * its behaviour acted on the hardware (channels may still be unchanged, e.g. a
   * repeated `lm`); false for an unknown id or a CP no-op (FR-017). Applies the
   * new state immediately — the caller does not wait for the next tick.
   */
  bool onCommand(const char *cmd_id, uint32_t now_ms);

  /* 50 ms tick: expire windows/pulses and advance the blink phase. O(1). */
  void tick(uint32_t now_ms);

  /* Introspection for DBG lines, firmware checks and the host test. */
  ChannelMask onMask() const { return on_; }
  bool isOn(Channel ch) const { return (on_ & channelBit(ch)) != 0; }
  const CommandTable *table() const { return table_; }

  /*
   * Channel-change records, oldest first. Every edge the sink was told about is
   * queued with the exact tick timestamp that caused it, so the caller can print
   * NFR-008 `DBG out=` lines from its own (logging-safe) context instead of from
   * the timer that runs `tick()`. The ring holds 2 x kChannelCount entries —
   * enough for a whole command's worth of changes plus the previous tick — and
   * overwrites the oldest on overflow (counted in `droppedEdges()`).
   */
  struct Edge {
    Channel ch;
    bool on;
    uint32_t t_ms;
  };
  static constexpr uint8_t kEdgeCapacity = (uint8_t)(2 * kChannelCount);

  size_t takeEdges(Edge *out, size_t max);
  uint32_t droppedEdges() const { return edges_dropped_; }

 private:
  enum class Blink : uint8_t { kOff, kSide, kParqueo };
  enum class Motor : uint8_t { kOff, kLpb, kLpa };

  struct Pulse {
    bool active;
    uint32_t start_ms;
    uint32_t duration_ms;
  };

  /* Luz hierarchy position for a command's channel mask: 0 = none, 1 = lm,
   * 2 = lb, 3 = la. Derived from the registry mask against the contract order
   * LM < LB < LA, so `lb` (registry channels ["lb"]) still means LM + LB. */
  static uint8_t luzLevelFor(ChannelMask channels);

  /* Desired ON set implied by the current behaviour state at `now_ms`. */
  ChannelMask desired(uint32_t now_ms) const;

  /* Queue one channel-change record (observability; see takeEdges()). */
  void pushEdge(Channel ch, bool on, uint32_t now_ms);

  /* Back to the boot state without touching the pins (begin/failSafe share it). */
  void resetState();

  /* Drop every expired window/pulse (45 s blinks/wiper, 2 s / 0.5 s pulses). */
  void expire(uint32_t now_ms);

  /* Diff `desired()` against the applied mask and push the edges to the sink. */
  void sync(uint32_t now_ms);

  static bool elapsedAtLeast(uint32_t now_ms, uint32_t start_ms, uint32_t span_ms) {
    /* Unsigned arithmetic wraps cleanly: correct across millis() rollover. */
    return (uint32_t)(now_ms - start_ms) >= span_ms;
  }
  static bool blinkPhaseOn(uint32_t now_ms, uint32_t start_ms) {
    return ((uint32_t)(now_ms - start_ms) / kBlinkHalfPeriodMs) % 2u == 0u;
  }

  const CommandTable *table_ = nullptr;
  ChannelSink *sink_ = nullptr;

  ChannelMask on_ = 0; /* applied (desired) state — what the sink has been told */

  /* luz group */
  uint8_t luz_level_ = 0;

  /* guiñadores group (GD/GI: one blink clock for the pair, so parqueo keeps
   * both sides in phase) */
  Blink blink_ = Blink::kOff;
  ChannelMask blink_mask_ = 0; /* GD and/or GI for the active blink */
  uint32_t blink_start_ms_ = 0;

  /* wipers group (LPB/LPA are mutually exclusive — Q-011) */
  Motor motor_ = Motor::kOff;
  uint32_t motor_start_ms_ = 0;

  /* pulse channels (A, CC) — independent, one shoot each */
  Pulse pulse_[kChannelCount] = {};

  /* channel-change records for the adapter's DBG out= lines */
  Edge edges_[kEdgeCapacity] = {};
  uint8_t edge_head_ = 0;
  uint8_t edge_count_ = 0;
  uint32_t edges_dropped_ = 0;
};

}  // namespace ultron

#endif /* ULTRON_OUTPUTS_H */
