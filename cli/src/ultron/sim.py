"""``ultron sim`` — host-side SIMULATION of the wake → command state machine.

**This is not the device state machine.** Spec 003 owns the real one (TD-013:
"the device state machine has exactly one owner"). This module exists so the
owner can watch the intended behaviour on the host microphone before the
firmware pipeline exists, and it must be replaced by — or, better, deleted in
favour of — the shared portable core once spec 003 lands. It never actuates
anything: no relays, no hardware, no side effects beyond console output.

Behaviour modelled (owner's description, 2026-11-19):

    1. LISTENING — every scored window may arm the session, but only the
       ``wake`` class can do it (threshold + margin from ``thresholds.json``).
    2. COMMAND — for ``window_s`` (5.0 s) from the wake, only the *eligible*
       commands (``la``, ``lb`` by default) may fire. ``noise`` never fires
       (it is the null class) and ``wake`` is not a command.
    3. A fired command prints its label and returns to LISTENING immediately.
    4. If nothing fires within ``window_s``, the window expires and the
       simulator returns to LISTENING (wake detection resumes).

Deliberately unspecified by the owner, decided here and flagged as provisional:
a ``wake`` heard *inside* the command window does not restart the window — the
clock stays anchored to the first wake, which keeps the 5 s budget honest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import numpy as np
import typer

from .infer import (
    DEFAULT_MIC_SECONDS,
    DEFAULT_MODEL,
    DEFAULT_THRESHOLDS,
    Detection,
    InferDepsError,
    InferError,
    Mfcc,
    Thresholds,
    TfliteRunner,
    check_class_alignment,
    flush_print,
    format_clock,
    format_live_line,
    load_thresholds,
    mic_blocks,
    open_mfcc,
    resolve_labels,
    score_stream,
    validate_model_file,
)

STATE_LISTENING = "LISTENING"
STATE_COMMAND = "COMMAND"
STATE_EXECUTING = "EXECUTING"

DEFAULT_ELIGIBLE = ("la", "lb")
DEFAULT_EXEC_S = 5.0  # how long a fired command "executes" before listening resumes

# How the COMMAND window turns per-window detections into a decision.
#   first - commit to the first eligible window that fires (fastest, most fragile:
#           a window that only caught the shared phrase prefix can win)
#   max   - collect the whole window, fire its highest-scoring detection at expiry
#   vote  - fire as soon as VOTE_N detections agree on a label (default 2)
AGGREGATIONS = ("first", "max", "vote")
DEFAULT_AGGREGATION = "first"
DEFAULT_VOTE_N = 2

# Classes that carry meaning while waiting for the wake word. Everything else —
# every command — is ignored in LISTENING: a door slam that scores ``la`` must
# not look like the system is considering a command.
LISTENING_LABELS = ("wake", "noise")


@dataclass(frozen=True)
class SimEvent:
    """One state-machine transition, emitted only when something happens."""

    t_s: float
    kind: str  # "wake" | "command" | "timeout" | "executed"
    label: Optional[str] = None
    score: Optional[float] = None
    after_wake_s: Optional[float] = None
    detail: Optional[str] = None  # how the decision was reached (aggregation)

    def line(self, window_s: float, exec_s: float = DEFAULT_EXEC_S) -> str:
        """Human line for the console — ASCII states so it greps cleanly."""
        stamp = format_clock(self.t_s)
        if self.kind == "wake":
            return (
                f"[{stamp}] {STATE_LISTENING} -> {STATE_COMMAND}  "
                f"wake {self.score:.3f} (window {window_s:.1f}s, "
                f"eligible: see banner)"
            )
        if self.kind == "command":
            detail = f", {self.detail}" if self.detail else ""
            return (
                f"[{stamp}] {STATE_COMMAND} -> {STATE_EXECUTING}  "
                f"EXECUTING {self.label} {self.score:.3f} "
                f"({self.after_wake_s:.2f}s after wake{detail}) — "
                f"listening again in {exec_s:.1f}s"
            )
        if self.kind == "executed":
            return (
                f"[{stamp}] {STATE_EXECUTING} -> {STATE_LISTENING}  "
                f"done ({exec_s:.1f}s), listening for wake"
            )
        return (
            f"[{stamp}] {STATE_COMMAND} -> {STATE_LISTENING}  "
            f"no command within {window_s:.1f}s (window expired)"
        )


class WakeCommandSimulator:
    """Simulator: LISTENING → COMMAND → EXECUTING → LISTENING.

    ``EXECUTING`` models the device doing the thing: for ``exec_s`` seconds
    everything is ignored (a second command must not stack on the first, and
    noise/wake cannot restart anything), then listening resumes.

    Pure logic: ``feed(t_s, detection)`` in, ``SimEvent | None`` out. No audio,
    no model, no clock — so the semantics are unit-testable without a mic.
    """

    def __init__(
        self,
        thresholds: Thresholds,
        eligible: Sequence[str] = DEFAULT_ELIGIBLE,
        *,
        exec_s: float = DEFAULT_EXEC_S,
        aggregate: str = DEFAULT_AGGREGATION,
        vote_n: int = DEFAULT_VOTE_N,
        wake_label: str = "wake",
        null_class: str = "noise",
    ) -> None:
        if thresholds.window_s <= 0:
            raise ValueError(f"window_s must be > 0, got {thresholds.window_s}")
        if exec_s < 0:
            raise ValueError(f"exec_s must be >= 0, got {exec_s}")
        if aggregate not in AGGREGATIONS:
            raise ValueError(f"aggregate must be one of {AGGREGATIONS}, got {aggregate!r}")
        if vote_n < 1:
            raise ValueError(f"vote_n must be >= 1, got {vote_n}")
        self.window_s = thresholds.window_s
        self.exec_s = exec_s
        self.aggregate = aggregate
        self.vote_n = vote_n
        self.eligible = tuple(eligible)
        self.wake_label = wake_label
        self.null_class = null_class
        self.state = STATE_LISTENING
        self.wake_t_s: Optional[float] = None
        self.exec_started_s: Optional[float] = None
        self.executing_label: Optional[str] = None
        # Evidence collected inside the current COMMAND window
        self.pending_best: dict[str, float] = {}
        self.pending_counts: dict[str, int] = {}
        self.wakes = 0
        self.commands: list[str] = []
        self.timeouts = 0
        self.executions = 0

    # ── transitions ────────────────────────────────────────────────────────
    def feed(self, t_s: float, detection: Detection) -> Optional[SimEvent]:
        """Advance the machine by one scored window.

        ``detection`` is already threshold-decoded, so ``detection.label`` is a
        detection (never ``noise``, which the decoder refuses).
        """
        if self.state == STATE_EXECUTING:
            done = self._finish_exec_if_due(t_s)
            if done is not None:
                return done
            # Nothing acts while executing: no stacking commands, no re-arm.
            return None

        if self.state == STATE_COMMAND:
            expired = self._expire_if_due(t_s)
            if expired is not None:
                return expired
            label = detection.label
            if label in self.eligible:
                after = t_s - self.wake_t_s if self.wake_t_s is not None else 0.0
                self.pending_best[label] = max(self.pending_best.get(label, 0.0), detection.score)
                self.pending_counts[label] = self.pending_counts.get(label, 0) + 1
                if self.aggregate == "first":
                    return self._enter_executing(t_s, label, detection.score, after)
                if self.aggregate == "vote" and self.pending_counts[label] >= self.vote_n:
                    return self._enter_executing(
                        t_s,
                        label,
                        self.pending_best[label],
                        after,
                        detail=f"{self.pending_counts[label]} vote(s)",
                    )
                # max/vote: keep listening for stronger evidence until expiry
                return None
            # wake/noise inside the command window do not restart its clock, and
            # non-eligible commands are ignored until the window closes.
            return None

        if detection.label == self.wake_label:
            self.state = STATE_COMMAND
            self.wake_t_s = t_s
            self._clear_pending()
            self.wakes += 1
            return SimEvent(t_s, "wake", label=self.wake_label, score=detection.score)
        return None

    def _enter_executing(
        self, t_s: float, label: str, score: float, after: float, detail: Optional[str] = None
    ) -> SimEvent:
        self.state = STATE_EXECUTING
        self.wake_t_s = None
        self.exec_started_s = t_s
        self.executing_label = label
        self.commands.append(label)
        self._clear_pending()
        return SimEvent(
            t_s, "command", label=label, score=score, after_wake_s=after, detail=detail
        )

    def _clear_pending(self) -> None:
        self.pending_best.clear()
        self.pending_counts.clear()

    def _finish_exec_if_due(self, t_s: float) -> Optional[SimEvent]:
        if self.exec_started_s is None:
            return None
        elapsed = t_s - self.exec_started_s
        if elapsed >= self.exec_s:
            self.state = STATE_LISTENING
            self.exec_started_s = None
            self.executing_label = None
            self.executions += 1
            return SimEvent(t_s, "executed", after_wake_s=elapsed)
        return None

    def _expire_if_due(self, t_s: float) -> Optional[SimEvent]:
        if self.wake_t_s is None:
            return None
        elapsed = t_s - self.wake_t_s
        if elapsed < self.window_s:
            return None
        # Window closed: aggregated modes decide now, from the evidence collected.
        choice = self._pending_choice()
        if choice is not None:
            label, score, detail = choice
            return self._enter_executing(t_s, label, score, elapsed, detail=detail)
        self._to_listening()
        self.timeouts += 1
        return SimEvent(t_s, "timeout", after_wake_s=elapsed)

    def _pending_choice(self) -> Optional[tuple[str, float, str]]:
        """Best deferred decision at window end, or ``None`` (=> timeout)."""
        if not self.pending_best:
            return None
        if self.aggregate == "max":
            label = max(self.pending_best, key=lambda k: self.pending_best[k])
            n = sum(self.pending_counts.values())
            return label, self.pending_best[label], f"best of {n} detection(s) at window end"
        # vote: expiry with fewer than vote_n agreeing hits is not a decision
        return None

    def _to_listening(self) -> None:
        self.state = STATE_LISTENING
        self.wake_t_s = None
        self._clear_pending()

    def ignore_reason(self, detection: Detection) -> Optional[str]:
        """Why this window cannot act right now (``None`` = it can act).

        LISTENING watches exactly ``wake`` and ``noise``; every command is
        ignored until a wake arms the machine. COMMAND listens only for
        ``eligible`` commands. EXECUTING ignores everything until it finishes.
        """
        label = detection.argmax_label
        if self.state == STATE_EXECUTING:
            return f"executing {self.executing_label}"
        if self.state == STATE_LISTENING:
            return None if label in LISTENING_LABELS else "waiting for wake"
        if label in self.eligible or label in LISTENING_LABELS:
            return None
        return "not an eligible command"

    def counts(self) -> dict:
        return {
            "wakes": self.wakes,
            "commands": len(self.commands),
            "timeouts": self.timeouts,
            "executed": self.executions,
        }


def format_sim_line(
    t_s: float, detection: Detection, *, ignored: Optional[str] = None
) -> str:
    """Per-window line for the simulator, marking classes that cannot act."""
    line = format_live_line(t_s, detection)
    return f"{line}  (ignored: {ignored})" if ignored else line


def run_sim_session(
    blocks: Iterable[np.ndarray],
    runner: TfliteRunner,
    mfcc: Mfcc,
    thresholds: Thresholds,
    labels: Sequence[str],
    *,
    eligible: Sequence[str] = DEFAULT_ELIGIBLE,
    exec_s: float = DEFAULT_EXEC_S,
    aggregate: str = DEFAULT_AGGREGATION,
    vote_n: int = DEFAULT_VOTE_N,
    log: Callable[[str], None] = flush_print,
    stream_windows: bool = True,
) -> WakeCommandSimulator:
    """Drive the simulator from a live block stream (shared scoring path)."""
    sim = WakeCommandSimulator(
        thresholds, eligible, exec_s=exec_s, aggregate=aggregate, vote_n=vote_n
    )
    scored = 0
    for t_s, detection in score_stream(blocks, runner, mfcc, thresholds, labels):
        scored += 1
        ignored = sim.ignore_reason(detection)  # state *before* this window acts
        if stream_windows:
            log(format_sim_line(t_s, detection, ignored=ignored))
        event = sim.feed(t_s, detection)
        if event is not None:
            log(event.line(thresholds.window_s, exec_s))
    log(f"scored {scored} window(s); sim: {sim.counts()}")
    return sim


__all__ = [
    "AGGREGATIONS",
    "DEFAULT_AGGREGATION",
    "DEFAULT_ELIGIBLE",
    "DEFAULT_EXEC_S",
    "DEFAULT_VOTE_N",
    "LISTENING_LABELS",
    "STATE_COMMAND",
    "STATE_EXECUTING",
    "STATE_LISTENING",
    "SimEvent",
    "WakeCommandSimulator",
    "format_sim_line",
    "run_sim_session",
    "sim_command",
]


# ── CLI ─────────────────────────────────────────────────────────────────────


def sim_command(
    model: Path = typer.Option(
        DEFAULT_MODEL, "--model", "-m", help="Exported artifact (int8 or fp16, T9)."
    ),
    labels: Optional[Path] = typer.Option(
        None, "--labels", "-l", help="Frozen class order (default: next to the model)."
    ),
    thresholds: Path = typer.Option(
        DEFAULT_THRESHOLDS, "--thresholds", "-t", help="Operating point document (Q-006)."
    ),
    seconds: float = typer.Option(
        DEFAULT_MIC_SECONDS, "--seconds", "-s", help="How long to listen (0 = until Ctrl-C)."
    ),
    device: Optional[str] = typer.Option(
        None, "--device", "-d", help="Input device index or name substring."
    ),
    eligible: str = typer.Option(
        "la,lb", "--eligible", help="Comma-separated commands allowed to fire after the wake."
    ),
    decisions_only: bool = typer.Option(
        False, "--decisions-only", help="Hide the per-window label stream; keep transitions only."
    ),
    exec_s: float = typer.Option(
        DEFAULT_EXEC_S, "--exec-s", help="Seconds spent EXECUTING before listening resumes."
    ),
    aggregate: str = typer.Option(
        DEFAULT_AGGREGATION,
        "--aggregate",
        help="How the 5s window decides: first (commit to the first detection), "
        "max (best score at window end), vote (fire when N agree).",
    ),
    vote_n: int = typer.Option(
        DEFAULT_VOTE_N, "--vote-n", help="Agreeing detections needed with --aggregate vote."
    ),
) -> None:
    """SIMULATE the wake → command state machine on the host mic (not the device).

    LISTENING waits for the wake word; a wake opens a ``window_s`` (5.0 s)
    command window; only ``--eligible`` commands may fire inside it; a fired
    command prints its label and returns to LISTENING; an empty window expires
    and returns to LISTENING too. Simulation only — spec 003 owns the real
    state machine (TD-013) and nothing is actuated here.
    """
    eligible_labels = tuple(x.strip() for x in eligible.split(",") if x.strip())
    if not eligible_labels:
        typer.echo("error: --eligible needs at least one command label", err=True)
        raise typer.Exit(code=2)
    try:
        validate_model_file(model)
        settings = load_thresholds(thresholds)
        class_labels = resolve_labels(labels, model)
        runner = TfliteRunner(model)
        check_class_alignment(class_labels, runner.n_classes, str(model))
        missing = [x for x in eligible_labels if x not in class_labels]
        if missing:
            typer.echo(
                f"error: --eligible {', '.join(missing)} not in the model's classes "
                f"[{' '.join(class_labels)}] — it could never fire",
                err=True,
            )
            raise typer.Exit(code=2)
        mfcc = open_mfcc()
    except InferDepsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=3)
    except InferError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2)

    typer.echo("SIMULATION — provisional wake/command semantics; spec 003 owns the "
               "device state machine. No actuation.")
    typer.echo(f"model: {runner.descriptor}")
    typer.echo(f"classes: {' '.join(class_labels)}")
    typer.echo(
        f"states: LISTENING --wake--> COMMAND ({settings.window_s:.1f}s; "
        f"eligible: {' '.join(eligible_labels)}) --command--> EXECUTING ({exec_s:.1f}s) "
        f"--> LISTENING   (COMMAND --timeout--> LISTENING)  aggregate={aggregate}"
        + ("" if not decisions_only else "  [per-window labels hidden]")
    )
    try:
        run_sim_session(
            mic_blocks(seconds=seconds, device=device),
            runner,
            mfcc,
            settings,
            class_labels,
            eligible=eligible_labels,
            exec_s=exec_s,
            aggregate=aggregate,
            vote_n=vote_n,
            log=flush_print,
            stream_windows=not decisions_only,
        )
    except KeyboardInterrupt:
        typer.echo("\nstopped.")
