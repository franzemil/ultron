/*
 * relay_gpio — the XIAO ESP32S3 adapter for the output controller (spec 003 T15).
 *
 * The only file in the output path that knows about GPIOs, timers and Serial. It
 * provides the three target-specific pieces the behaviour engine deliberately
 * does not have:
 *
 *   RelayGpioSink   ChannelSink → active-LOW digitalWrite on the pins from pins.h
 *   OutputTicker    50 ms non-blocking tick source + the lock that serialises it
 *                   with the application's command handling
 *   logPinMap / logRegistry / logOutputEdges / RelayGpioSink::logBootState
 *                   the NFR-008 console lines: `DBG out=<id> state=on|off|unwired`
 *
 * Why a timer and not the loop: `UltronVoice::poll()` performs one blocking
 * ~100 ms microphone read, so a loop-driven tick would put pulse edges outside
 * FR-009's ±50 ms tolerance. The 50 ms esp_timer keeps the relay edges on time
 * while the classification loop keeps running untouched (the design's "50 ms
 * non-blocking tick"). The timer task never prints: edges are queued by the
 * engine (OutputController::takeEdges) with their exact tick timestamp and the
 * application drains them from its own context, so console lines cannot
 * interleave with the library's `DBG pred=` output.
 *
 * Keeping all of this behind `ChannelSink` is what makes the behaviour layer
 * reusable unchanged on spec 006's companion ESP32-S3 SuperMini — one target
 * today (AGENTS rule 5), target-specific pins only.
 *
 * Boot safety (NFR-009), in order of importance:
 *   1. External pull-ups to 3V3 hold the relay inputs HIGH (OFF) on reset.
 *   2. init() drives the output latch HIGH *before* switching the pin to an
 *      output, so enabling the driver cannot glitch a coil ON.
 *   3. Every wired channel is explicitly written to its OFF level at boot and
 *      logged, so the serial log itself proves the boot state.
 */

#ifndef ULTRON_RELAY_GPIO_H
#define ULTRON_RELAY_GPIO_H

#include <Arduino.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include <stdio.h>

#include "outputs.h"
#include "outputs_config.h"
#include "pins.h"

namespace ultron {

/* ── pin layer ───────────────────────────────────────────────────────────── */

class RelayGpioSink : public ChannelSink {
 public:
  void init(uint32_t now_ms) override {
    (void)now_ms;
    for (unsigned i = 0; i < (unsigned)kChannelCount; i++) {
      const PinSpec &pin = kChannelPins[i];
      if (!pin.wired) continue;

      const uint8_t off_level = pin.active_low ? HIGH : LOW;
      /* Pull-up keeps the input OFF while the pin is still hi-Z; writing the
       * latch first means the switch to OUTPUT drives OFF, never ON. */
      pinMode(pin.gpio, INPUT_PULLUP);
      digitalWrite(pin.gpio, off_level);
      pinMode(pin.gpio, OUTPUT);
      digitalWrite(pin.gpio, off_level);
    }
  }

  void apply(Channel ch, bool on, uint32_t now_ms) override {
    (void)now_ms;
    const PinSpec &pin = kChannelPins[(unsigned)ch];
    /* Unwired channels (LA, GD…CC in this milestone) have no pin to move; the
     * edge is still reported by logOutputEdges() as `state=unwired`. */
    if (!pin.wired) return;
    digitalWrite(pin.gpio, (on != pin.active_low) ? HIGH : LOW);
  }

  /* NFR-009 boot proof: the explicit OFF every wired channel starts in. */
  void logBootState(Print &out, uint32_t now_ms) const {
    for (unsigned i = 0; i < (unsigned)kChannelCount; i++) {
      const PinSpec &pin = kChannelPins[i];
      if (!pin.wired) continue;
      out.printf("DBG out=%s state=off t_ms=%lu\r\n", channelName((Channel)i),
                 (unsigned long)now_ms);
    }
  }
};

/* ── 50 ms tick source ───────────────────────────────────────────────────── */

/*
 * Owns the esp_timer and the mutex that makes the engine safe to drive from two
 * contexts (the timer task and the application loop). Every engine call — tick,
 * onCommand, failSafe, takeEdges — goes through locked(), so the behaviour state
 * and the pin writes are never interleaved.
 */
class OutputTicker {
 public:
  ~OutputTicker() { end(); }
  OutputTicker() = default;
  OutputTicker(const OutputTicker &) = delete;
  OutputTicker &operator=(const OutputTicker &) = delete;

  bool begin(OutputController *controller, uint32_t period_ms = kTickMs) {
    controller_ = controller;
    if (lock_ == nullptr) {
      lock_ = xSemaphoreCreateMutex();
      if (lock_ == nullptr) return false;
    }
    if (timer_ == nullptr) {
      esp_timer_create_args_t args = {};
      args.callback = &OutputTicker::onTimer;
      args.arg = this;
      args.name = "ultron_out";
      args.dispatch_method = ESP_TIMER_TASK;
      if (esp_timer_create(&args, &timer_) != ESP_OK) {
        timer_ = nullptr;
        return false;
      }
    }
    return esp_timer_start_periodic(timer_, (uint64_t)period_ms * 1000u) == ESP_OK;
  }

  void end() {
    if (timer_ == nullptr) return;
    esp_timer_stop(timer_);
    esp_timer_delete(timer_);
    timer_ = nullptr;
  }

  /* Run `fn` with the engine lock held (no allocation, no exceptions). */
  template <typename Fn>
  void locked(Fn fn) {
    if (lock_ == nullptr) {
      fn();
      return;
    }
    xSemaphoreTake(lock_, portMAX_DELAY);
    fn();
    xSemaphoreGive(lock_);
  }

  uint32_t tickCount() const { return ticks_; }
  uint32_t lastTickMs() const { return last_tick_ms_; }
  uint32_t minGapMs() const { return min_gap_ms_; }
  uint32_t maxGapMs() const { return max_gap_ms_; }
  uint32_t avgGapMs() const { return ticks_ > 1 ? gap_sum_ms_ / (ticks_ - 1u) : 0u; }

 private:
  static void onTimer(void *arg) {
    OutputTicker *self = static_cast<OutputTicker *>(arg);
    self->locked([self] {
      if (self->controller_ == nullptr) return;
      const uint32_t now = millis();
      if (self->ticks_ > 0) {
        const uint32_t gap = (uint32_t)(now - self->last_tick_ms_);
        if (gap < self->min_gap_ms_) self->min_gap_ms_ = gap;
        if (gap > self->max_gap_ms_) self->max_gap_ms_ = gap;
        self->gap_sum_ms_ += gap;
      }
      self->last_tick_ms_ = now;
      self->ticks_++;
      self->controller_->tick(now);
    });
  }

  OutputController *controller_ = nullptr;
  esp_timer_handle_t timer_ = nullptr;
  SemaphoreHandle_t lock_ = nullptr;
  uint32_t ticks_ = 0;
  uint32_t last_tick_ms_ = 0;
  uint32_t min_gap_ms_ = 0xFFFFFFFFu;
  uint32_t max_gap_ms_ = 0;
  uint32_t gap_sum_ms_ = 0; /* reported by the app; see gap stats below */
};

/* ── console reporting (NFR-008) ─────────────────────────────────────────── */

/* `DBG out_pins wired=2/9 lm=gpio6:alow lb=gpio7:alow la=unwired …` */
inline void logPinMap(Print &out) {
  char line[240];
  int n = snprintf(line, sizeof(line), "DBG out_pins wired=%u/%u", wiredChannels(),
                   (unsigned)kChannelCount);
  for (unsigned i = 0; n > 0 && (size_t)n < sizeof(line) && i < (unsigned)kChannelCount; i++) {
    const PinSpec &pin = kChannelPins[i];
    if (pin.wired) {
      n += snprintf(line + n, sizeof(line) - (size_t)n, " %s=gpio%d:%s", channelName((Channel)i),
                    (int)pin.gpio, pin.active_low ? "alow" : "ahigh");
    } else {
      n += snprintf(line + n, sizeof(line) - (size_t)n, " %s=unwired", channelName((Channel)i));
    }
  }
  out.println(line);
}

/* `DBG out_registry commands=11 trained=2 unknown_behavior=0 unknown_channels=0` —
 * a behavior/channel the engine cannot map is a config bug, logged at boot
 * instead of turning into a silent no-op at command time. */
inline void logRegistry(const RegistryCommandTable &registry, Print &out) {
  const char *bad_id = nullptr;
  const size_t bad_channels = unknownChannels(registry, &bad_id);
  const size_t bad_behaviors = unknownBehaviors(registry);
  if (bad_id != nullptr) {
    out.printf(
        "DBG out_registry commands=%u trained=%d unknown_behavior=%u unknown_channels=%u "
        "bad_id=%s\r\n",
        (unsigned)registry.table.count, ULTRON_NUM_COMMANDS, (unsigned)bad_behaviors,
        (unsigned)bad_channels, bad_id);
  } else {
    out.printf(
        "DBG out_registry commands=%u trained=%d unknown_behavior=%u unknown_channels=%u\r\n",
        (unsigned)registry.table.count, ULTRON_NUM_COMMANDS, (unsigned)bad_behaviors,
        (unsigned)bad_channels);
  }
}

/*
 * Drain the engine's channel-change records into `DBG out=` lines. Called from
 * the application loop (never from the timer task), so the console stays single
 * threaded for this file's lines and every line carries the edge's own `t_ms`.
 */
inline void logOutputEdges(OutputController &controller, OutputTicker &ticker, Print &out) {
  OutputController::Edge edges[OutputController::kEdgeCapacity];
  size_t n = 0;
  ticker.locked([&] { n = controller.takeEdges(edges, OutputController::kEdgeCapacity); });
  for (size_t i = 0; i < n; i++) {
    const Channel ch = edges[i].ch;
    const bool on = edges[i].on;
    if (!kChannelPins[(unsigned)ch].wired) {
      out.printf("DBG out=%s state=unwired want=%s t_ms=%lu\r\n", channelName(ch),
                 on ? "on" : "off", (unsigned long)edges[i].t_ms);
    } else {
      out.printf("DBG out=%s state=%s t_ms=%lu\r\n", channelName(ch), on ? "on" : "off",
                 (unsigned long)edges[i].t_ms);
    }
  }
}

/* `DBG tick_ms n=… min=… max=… on=0x…` — the measured cadence of the 50 ms tick,
 * i.e. how well the relay edges can actually land (NFR-008 / FR-009 evidence). */
inline void logTickStats(OutputTicker &ticker, const OutputController &controller,
                         Print &out, uint32_t now_ms) {
  const uint32_t n = ticker.tickCount();
  if (n == 0) return;
  out.printf("DBG tick_ms n=%lu min=%lu max=%lu avg=%lu last=%lu on=0x%02x t_ms=%lu\r\n",
             (unsigned long)n, (unsigned long)ticker.minGapMs(),
             (unsigned long)ticker.maxGapMs(), (unsigned long)ticker.avgGapMs(),
             (unsigned long)ticker.lastTickMs(), (unsigned)controller.onMask(),
             (unsigned long)now_ms);
}

}  // namespace ultron

#endif /* ULTRON_RELAY_GPIO_H */
