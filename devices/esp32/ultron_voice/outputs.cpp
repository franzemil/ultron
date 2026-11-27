/*
 * outputs — implementation of the ultron output controller (spec 003 T15).
 *
 * Pure C++: no Arduino, no ESP32, no I/O, no allocation, no clock of its own —
 * the caller passes millis() (device) or a fake tick (host test). See outputs.h
 * for the behaviour contract and the requirement mapping.
 *
 * Timing model: every timed behaviour stores its start timestamp and derives
 * its state from `now - start`, so a late tick can never shift a deadline
 * (a 45 s motor window ends 45 s after the command even if ticks are uneven).
 * The relay edge itself lands on a tick boundary, i.e. within one 50 ms tick of
 * the deadline — inside FR-009's ±50 ms pulse / ±100 ms blink tolerances.
 */

#include "outputs.h"

#include <string.h>

namespace ultron {
namespace {

/* Actuation Behavior Contract luz hierarchy: LM < LB < LA (cumulative). The
 * command's *level* comes from its registry channel list; the hierarchy order
 * is contract fixed — that is what makes `lb` (registry ["lb"]) drive LM + LB. */
constexpr Channel kLuzOrder[] = {kChLm, kChLb, kChLa};
constexpr uint8_t kLuzCount = (uint8_t)(sizeof(kLuzOrder) / sizeof(kLuzOrder[0]));

struct NamedChannel {
  const char *name;
  Channel ch;
};

constexpr NamedChannel kChannelNames[kChannelCount] = {
    {"lm", kChLm},   {"lb", kChLb},  {"la", kChLa},  {"gd", kChGd}, {"gi", kChGi},
    {"lpb", kChLpb}, {"lpa", kChLpa}, {"a", kChA},   {"cc", kChCc},
};

struct NamedBehavior {
  const char *name;
  Behavior behavior;
};

constexpr NamedBehavior kBehaviorNames[] = {
    {"steady", Behavior::kSteady},
    {"blink_45s", Behavior::kBlink45s},
    {"blink_until_cancel", Behavior::kBlinkUntilCancel},
    {"cancel", Behavior::kCancel},
    {"motor", Behavior::kMotor45s},     /* registry enum (model/registry.json v2) */
    {"motor_45s", Behavior::kMotor45s}, /* design.md spelling — same engine      */
    {"pulse_2s", Behavior::kPulse2s},
    {"pulse_05s", Behavior::kPulse05s},
};

bool equals(const char *a, const char *b) {
  return a != nullptr && b != nullptr && strcmp(a, b) == 0;
}

bool isSpace(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

}  // namespace

/* ── channels ────────────────────────────────────────────────────────────── */

const char *channelName(Channel ch) {
  const unsigned idx = (unsigned)ch;
  return idx < (unsigned)kChannelCount ? kChannelNames[idx].name : "?";
}

bool channelFromName(const char *name, Channel *out) {
  if (name == nullptr) return false;
  for (unsigned i = 0; i < (unsigned)kChannelCount; i++) {
    if (equals(name, kChannelNames[i].name)) {
      if (out != nullptr) *out = kChannelNames[i].ch;
      return true;
    }
  }
  return false;
}

ChannelMask channelsFromCsv(const char *csv) {
  if (csv == nullptr) return 0;
  ChannelMask mask = 0;
  const char *p = csv;
  while (*p != '\0') {
    while (*p == ',' || isSpace(*p)) p++;
    if (*p == '\0') break;
    const char *start = p;
    while (*p != '\0' && *p != ',' && !isSpace(*p)) p++;
    const size_t len = (size_t)(p - start);
    char token[8]; /* longest registry channel id is "lpb" (3) */
    if (len == 0 || len >= sizeof(token)) continue;
    memcpy(token, start, len);
    token[len] = '\0';
    Channel ch;
    if (channelFromName(token, &ch)) mask = (ChannelMask)(mask | channelBit(ch));
  }
  return mask;
}

/* ── behaviors ───────────────────────────────────────────────────────────── */

const char *behaviorName(Behavior b) {
  for (const NamedBehavior &entry : kBehaviorNames) {
    if (entry.behavior == b) return entry.name;
  }
  return "unknown";
}

Behavior behaviorFromName(const char *name) {
  if (name == nullptr) return Behavior::kUnknown;
  for (const NamedBehavior &entry : kBehaviorNames) {
    if (equals(name, entry.name)) return entry.behavior;
  }
  return Behavior::kUnknown;
}

/* ── command table ───────────────────────────────────────────────────────── */

const CommandSpec *CommandTable::find(const char *id) const {
  if (items == nullptr || id == nullptr) return nullptr;
  for (size_t i = 0; i < count; i++) {
    if (equals(items[i].id, id)) return &items[i];
  }
  return nullptr;
}

/* ── engine ──────────────────────────────────────────────────────────────── */

uint8_t OutputController::luzLevelFor(ChannelMask channels) {
  uint8_t level = 0;
  for (uint8_t i = 0; i < kLuzCount; i++) {
    if ((channels & channelBit(kLuzOrder[i])) != 0) level = (uint8_t)(i + 1);
  }
  return level;
}

void OutputController::begin(const CommandTable &table, ChannelSink *sink, uint32_t now_ms) {
  table_ = &table;
  sink_ = sink;

  /* NFR-009: all channels OFF at boot/reset — the engine starts in the off
   * state and the sink re-asserts it physically in init(). */
  resetState();
  on_ = 0;
  edge_head_ = 0;
  edge_count_ = 0;

  if (sink_ != nullptr) sink_->init(now_ms);
}

void OutputController::failSafe(uint32_t now_ms) {
  /* Runtime fault: forget every command and drop the outputs (NFR-009). `on_` is
   * kept so sync() emits one OFF edge per channel that was on; the sink's init()
   * is not re-run — the pins are already outputs by now. */
  resetState();
  sync(now_ms);
}

void OutputController::resetState() {
  luz_level_ = 0;
  blink_ = Blink::kOff;
  blink_mask_ = 0;
  blink_start_ms_ = 0;
  motor_ = Motor::kOff;
  motor_start_ms_ = 0;
  for (unsigned i = 0; i < (unsigned)kChannelCount; i++) {
    pulse_[i].active = false;
    pulse_[i].start_ms = 0;
    pulse_[i].duration_ms = 0;
  }
}

bool OutputController::onCommand(const char *cmd_id, uint32_t now_ms) {
  if (table_ == nullptr) return false;
  const CommandSpec *spec = table_->find(cmd_id);
  if (spec == nullptr) return false;

  /* A command arriving between ticks must not resurrect a window that has
   * already elapsed. */
  expire(now_ms);

  bool acted = true;
  switch (spec->behavior) {
    case Behavior::kSteady: {
      /* A luz command replaces the previous luz state (off / lm / lb / la). */
      const uint8_t level = luzLevelFor(spec->channels);
      if (level == 0) {
        acted = false; /* registry entry drives no luz channel — config error */
      } else {
        luz_level_ = level;
      }
      break;
    }

    case Behavior::kBlink45s: {
      /* GD or GI: cancels a running parqueo by replacing the blink state, and
       * starts a fresh 45 s window (priority rule, FR-009). */
      const ChannelMask sides = (ChannelMask)(spec->channels & (channelBit(kChGd) |
                                                              channelBit(kChGi)));
      if (sides == 0) {
        acted = false;
      } else {
        blink_ = Blink::kSide;
        blink_mask_ = sides;
        blink_start_ms_ = now_ms;
      }
      break;
    }

    case Behavior::kBlinkUntilCancel: {
      /* Parqueo: overrides any single-side blink and blinks both sides in
       * phase, indefinitely, until CP (FR-017). */
      const ChannelMask sides = (ChannelMask)(spec->channels & (channelBit(kChGd) |
                                                              channelBit(kChGi)));
      if (sides == 0) {
        acted = false;
      } else {
        blink_ = Blink::kParqueo;
        blink_mask_ = sides;
        blink_start_ms_ = now_ms;
      }
      break;
    }

    case Behavior::kCancel: {
      /* FR-017: stop parqueo; a no-op when parqueo is not active — a single-side
       * blink and the wipers are untouched (CP never touches wipers). */
      if (blink_ == Blink::kParqueo) {
        blink_ = Blink::kOff;
        blink_mask_ = 0;
      } else {
        acted = false;
      }
      break;
    }

    case Behavior::kMotor45s: {
      /* Q-011: LPB/LPA are mutually exclusive. Switching speed cancels the
       * running window and starts the new one; re-selecting the same speed
       * restarts its window. */
      const ChannelMask want = (ChannelMask)(spec->channels & (channelBit(kChLpb) |
                                                              channelBit(kChLpa)));
      if (want == channelBit(kChLpb)) {
        motor_ = Motor::kLpb;
      } else if (want == channelBit(kChLpa)) {
        motor_ = Motor::kLpa;
      } else {
        acted = false; /* none (or both) — a registry entry we cannot honour */
        break;
      }
      if (acted) motor_start_ms_ = now_ms;
      break;
    }

    case Behavior::kPulse2s:
    case Behavior::kPulse05s: {
      const uint32_t duration =
          spec->behavior == Behavior::kPulse2s ? kPulse2sMs : kPulse05sMs;
      if (spec->channels == 0) {
        acted = false;
      } else {
        for (unsigned i = 0; i < (unsigned)kChannelCount; i++) {
          if ((spec->channels & (ChannelMask)(1u << i)) == 0) continue;
          pulse_[i].active = true;
          pulse_[i].start_ms = now_ms;
          pulse_[i].duration_ms = duration; /* re-command restarts the pulse */
        }
      }
      break;
    }

    case Behavior::kUnknown:
    default:
      acted = false;
      break;
  }

  /* Flush any expiry that `expire()` found plus this command's new state. */
  sync(now_ms);
  return acted;
}

void OutputController::tick(uint32_t now_ms) {
  expire(now_ms);
  sync(now_ms);
}

void OutputController::expire(uint32_t now_ms) {
  if (blink_ == Blink::kSide && elapsedAtLeast(now_ms, blink_start_ms_, kBlinkWindowMs)) {
    blink_ = Blink::kOff;
    blink_mask_ = 0;
  }
  if (motor_ != Motor::kOff && elapsedAtLeast(now_ms, motor_start_ms_, kMotorWindowMs)) {
    motor_ = Motor::kOff;
  }
  for (unsigned i = 0; i < (unsigned)kChannelCount; i++) {
    Pulse &p = pulse_[i];
    if (p.active && elapsedAtLeast(now_ms, p.start_ms, p.duration_ms)) p.active = false;
  }
}

ChannelMask OutputController::desired(uint32_t now_ms) const {
  ChannelMask mask = 0;

  /* Luz group: the state is the level, not the last command. */
  for (uint8_t i = 0; i < kLuzCount; i++) {
    if (luz_level_ > i) mask = (ChannelMask)(mask | channelBit(kLuzOrder[i]));
  }

  /* Guiñadores: one phase clock for GD/GI (parqueo keeps them in phase). */
  if (blink_ != Blink::kOff && blinkPhaseOn(now_ms, blink_start_ms_)) {
    mask = (ChannelMask)(mask | blink_mask_);
  }

  /* Wipers: one 45 s window, one channel at a time (expire() clears it). */
  if (motor_ == Motor::kLpb) {
    mask = (ChannelMask)(mask | channelBit(kChLpb));
  } else if (motor_ == Motor::kLpa) {
    mask = (ChannelMask)(mask | channelBit(kChLpa));
  }

  /* Pulses: A (2 s) and CC (0.5 s) are independent of every other group. */
  for (unsigned i = 0; i < (unsigned)kChannelCount; i++) {
    if (pulse_[i].active) mask = (ChannelMask)(mask | (ChannelMask)(1u << i));
  }

  return mask;
}

void OutputController::sync(uint32_t now_ms) {
  const ChannelMask want = desired(now_ms);
  const ChannelMask changed = (ChannelMask)(want ^ on_);
  if (changed == 0) return;
  for (unsigned i = 0; i < (unsigned)kChannelCount; i++) {
    const ChannelMask bit = (ChannelMask)(1u << i);
    if ((changed & bit) == 0) continue;
    const bool on = (want & bit) != 0;
    if (sink_ != nullptr) sink_->apply((Channel)i, on, now_ms);
    pushEdge((Channel)i, on, now_ms);
  }
  on_ = want;
}

void OutputController::pushEdge(Channel ch, bool on, uint32_t now_ms) {
  if (edge_count_ == kEdgeCapacity) {
    /* The consumer drains every loop iteration, so overflow means a lost record
     * (a missing DBG line), never a wrong relay state. */
    edge_head_ = (uint8_t)((edge_head_ + 1u) % kEdgeCapacity);
    edge_count_ = (uint8_t)(edge_count_ - 1u);
    edges_dropped_++;
  }
  edges_[(edge_head_ + edge_count_) % kEdgeCapacity] = Edge{ch, on, now_ms};
  edge_count_ = (uint8_t)(edge_count_ + 1u);
}

size_t OutputController::takeEdges(Edge *out, size_t max) {
  if (out == nullptr) return 0;
  size_t n = 0;
  while (n < max && edge_count_ > 0) {
    out[n++] = edges_[edge_head_];
    edge_head_ = (uint8_t)((edge_head_ + 1u) % kEdgeCapacity);
    edge_count_ = (uint8_t)(edge_count_ - 1u);
  }
  return n;
}

}  // namespace ultron
