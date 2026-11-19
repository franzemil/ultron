"""Tests for `ultron sim` — the wake → command SIMULATOR (spec 003 is the owner).

The state machine is pure (`feed(t_s, detection)`), so every transition is
tested without a microphone, a model or a clock. The session-level test stubs
the shared scoring path and checks the console transcript.

Semantics under test (owner's description):
  LISTENING --wake--> COMMAND (window_s) --command--> LISTENING
  COMMAND --window_s elapsed with no command--> LISTENING
  noise never fires; wake is not a command; non-eligible commands are ignored.
"""

from __future__ import annotations

import numpy as np
import pytest
import typer

from ultron.infer import Detection, OperatingPoint, Thresholds
from ultron.sim import (
    DEFAULT_ELIGIBLE,
    STATE_COMMAND,
    STATE_EXECUTING,
    STATE_LISTENING,
    WakeCommandSimulator,
    run_sim_session,
)

LABELS = ["wake", "noise", "lb", "la"]
WINDOW_S = 5.0


def thresholds(window_s: float = WINDOW_S) -> Thresholds:
    return Thresholds(
        wake=0.8,
        margin_wake=0.1,
        commands={
            "lb": OperatingPoint(0.8, 0.1),
            "la": OperatingPoint(0.8, 0.1),
        },
        window_s=window_s,
    )


def det(label: str, score: float = 0.99) -> Detection:
    """A decoded window: ``label`` accepted when it is a real class, else rejected.

    Mirrors ``decode_window``: ``noise`` is the null class (never accepted) and a
    rejected window has no label at all.
    """
    accepted = label in ("wake", "lb", "la")
    argmax = "noise" if label in ("noise", "none") else label
    scores = np.zeros(len(LABELS), dtype=np.float32)
    scores[LABELS.index(argmax)] = score
    return Detection(
        argmax_label=argmax,
        score=score,
        runner_up=0.0,
        operating_point=OperatingPoint(0.8, 0.1) if accepted else None,
        accepted=accepted,
        reason="accepted" if accepted else "rejected",
    )


def sim(**kwargs) -> WakeCommandSimulator:
    return WakeCommandSimulator(thresholds(), **kwargs)


# ── happy path ──────────────────────────────────────────────────────────────


def test_starts_in_listening():
    s = sim()
    assert s.state == STATE_LISTENING
    assert s.counts() == {"wakes": 0, "commands": 0, "timeouts": 0, "executed": 0}


def test_wake_opens_the_command_window():
    s = sim()
    event = s.feed(2.0, det("wake"))
    assert event is not None and event.kind == "wake"
    assert s.state == STATE_COMMAND
    assert s.wake_t_s == pytest.approx(2.0)


@pytest.mark.parametrize("command", ["la", "lb"])
def test_eligible_command_fires_then_executes(command):
    s = sim()
    s.feed(2.0, det("wake"))
    event = s.feed(3.0, det(command, 0.97))
    assert event is not None and event.kind == "command"
    assert event.label == command
    assert event.after_wake_s == pytest.approx(1.0)
    assert s.state == STATE_EXECUTING  # executing before listening resumes
    assert s.commands == [command]


def test_full_cycle_can_repeat():
    s = sim()
    for wake_t, cmd_t, done_t, cmd in [(2.0, 3.0, 8.0, "la"), (12.0, 13.0, 18.0, "lb")]:
        assert s.feed(wake_t, det("wake")).kind == "wake"
        assert s.feed(cmd_t, det(cmd)).kind == "command"
        assert s.feed(done_t, det("noise")).kind == "executed"
        assert s.state == STATE_LISTENING
    assert s.counts() == {"wakes": 2, "commands": 2, "timeouts": 0, "executed": 2}


# ── the 5 s budget ──────────────────────────────────────────────────────────


def test_window_expires_after_five_seconds_without_command():
    s = sim()
    s.feed(2.0, det("wake"))
    assert s.feed(6.9, det("noise")) is None  # 4.9 s in: still open
    assert s.state == STATE_COMMAND
    event = s.feed(7.0, det("none"))  # exactly 5.0 s after the wake
    assert event is not None and event.kind == "timeout"
    assert s.state == STATE_LISTENING
    assert s.timeouts == 1


def test_command_after_the_window_is_ignored():
    s = sim()
    s.feed(2.0, det("wake"))
    s.feed(7.0, det("none"))  # expires here
    assert s.feed(7.5, det("la")) is None  # no wake → not a command
    assert s.state == STATE_LISTENING
    assert s.commands == []


def test_wake_inside_the_window_does_not_restart_the_clock():
    s = sim()
    s.feed(2.0, det("wake"))
    assert s.feed(5.0, det("wake")) is None  # already armed; no new event
    assert s.wake_t_s == pytest.approx(2.0)  # anchored to the first wake
    event = s.feed(7.0, det("none"))
    assert event.kind == "timeout"  # 5.0 s after the *first* wake


# ── what must never fire ────────────────────────────────────────────────────


def test_noise_never_fires_in_either_state():
    s = sim()
    assert s.feed(1.0, det("noise")) is None
    s.feed(2.0, det("wake"))
    assert s.feed(3.0, det("noise")) is None
    assert s.commands == []


def test_non_eligible_command_is_ignored_until_timeout():
    s = sim(eligible=("la",))  # lb not listened for
    s.feed(2.0, det("wake"))
    assert s.feed(3.0, det("lb")) is None
    assert s.commands == []
    assert s.state == STATE_COMMAND  # window still open for la
    assert s.feed(4.0, det("la")).label == "la"


def test_wake_is_not_a_command_in_the_command_state():
    s = sim()
    s.feed(2.0, det("wake"))
    assert s.feed(2.5, det("wake")) is None
    assert s.commands == []


def test_every_class_but_wake_is_ignored_while_listening():
    s = sim()
    for label in ("noise", "la", "lb", "none"):
        assert s.feed(1.0, det(label)) is None
    assert s.state == STATE_LISTENING


# ── validation ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("window", [0.0, -1.0])
def test_non_positive_window_is_rejected(window):
    with pytest.raises(ValueError):
        WakeCommandSimulator(thresholds(window_s=window))


def test_default_eligible_is_la_and_lb():
    assert DEFAULT_ELIGIBLE == ("la", "lb")


# ── session wiring (stubbed scoring path) ───────────────────────────────────


def _session(tmp_path, events, monkeypatch, *, eligible=DEFAULT_ELIGIBLE):
    """Feed a scripted (t_s, detection) sequence through run_sim_session."""
    lines: list[str] = []

    def fake_score_stream(blocks, runner, mfcc, settings, labels):
        for t_s, label in events:
            yield t_s, det(label)

    monkeypatch.setattr("ultron.sim.score_stream", fake_score_stream)
    sim_obj = run_sim_session(
        [np.zeros(1600, dtype=np.int16)],
        runner=None,
        mfcc=None,
        thresholds=thresholds(),
        labels=LABELS,
        eligible=eligible,
        log=lines.append,
    )
    return sim_obj, lines


def test_session_prints_transitions_and_summary(tmp_path, monkeypatch):
    sim_obj, lines = _session(
        tmp_path,
        [(2.0, "noise"), (2.5, "wake"), (3.5, "la"), (8.5, "noise"),
         (10.0, "wake"), (16.0, "none")],
        monkeypatch,
    )
    text = "\n".join(lines)
    assert "LISTENING -> COMMAND" in text
    assert "COMMAND -> EXECUTING  EXECUTING la" in text
    assert "EXECUTING -> LISTENING  done (5.0s)" in text
    assert "no command within 5.0s" in text
    assert sim_obj.counts() == {"wakes": 2, "commands": 1, "timeouts": 1, "executed": 1}
    assert lines[-1].startswith("scored 6 window(s)")


def test_session_windows_hidden_with_decisions_only(tmp_path, monkeypatch):
    _, lines = _session(tmp_path, [(2.0, "wake"), (3.0, "lb")], monkeypatch)
    streamed = [ln for ln in lines if ln.startswith("[00:")]
    assert len(streamed) == 4  # 2 per-window + 2 transition lines
    # transitions are the ones carrying state arrows
    assert sum("->" in ln for ln in streamed) == 2


def test_sim_cli_rejects_eligible_class_the_model_lacks(tmp_path, monkeypatch, capsys):
    """A typo in --eligible must fail loudly, not silently never fire."""
    from ultron.cli import app
    from typer.testing import CliRunner

    import ultron.infer as infer_mod

    model = tmp_path / "model_int8.tflite"
    model.write_bytes(b"\x00\x00\x00\x00TFL3" + b"\x00" * 64)
    labels = tmp_path / "labels.json"
    labels.write_text('["wake", "noise", "lb", "la"]', encoding="utf-8")
    thresholds_path = tmp_path / "thresholds.json"
    thresholds_path.write_text(
        '{"wake": 0.8, "margin_wake": 0.1, "commands": {"lb": {"threshold": 0.8, "margin": 0.1},'
        ' "la": {"threshold": 0.8, "margin": 0.1}}, "window_s": 5.0}',
        encoding="utf-8",
    )

    class StubRunner:
        n_classes = 4
        descriptor = "stub"

        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr("ultron.sim.TfliteRunner", StubRunner)

    result = CliRunner().invoke(
        app,
        [
            "sim",
            "--model", str(model),
            "--labels", str(labels),
            "--thresholds", str(thresholds_path),
            "--eligible", "la,zz",
            "--seconds", "1",
        ],
    )
    assert result.exit_code == 2
    assert "eligible zz not in the model's classes" in result.output


# ── LISTENING watches wake + noise only ─────────────────────────────────────


def test_ignore_reason_while_listening_ignores_every_command():
    s = sim()
    assert s.ignore_reason(det("wake")) is None
    assert s.ignore_reason(det("noise")) is None
    for command in ("la", "lb"):
        assert s.ignore_reason(det(command)) == "waiting for wake"


def test_ignore_reason_in_command_state_allows_only_eligible():
    s = sim(eligible=("la",))
    s.feed(2.0, det("wake"))
    assert s.ignore_reason(det("la")) is None
    assert s.ignore_reason(det("lb")) == "not an eligible command"
    assert s.ignore_reason(det("wake")) is None  # a wake is not acted on, but not junk


def test_session_marks_ignored_commands_while_listening(tmp_path, monkeypatch):
    _, lines = _session(tmp_path, [(2.0, "la"), (2.5, "noise"), (3.0, "wake"), (4.0, "la")], monkeypatch)
    first = lines[0]
    assert "la" in first and "(ignored: waiting for wake)" in first  # ambient command
    assert "(ignored" not in lines[1]  # noise is legitimately watched
    assert "(ignored" not in lines[2]  # wake acts
    assert "(ignored" not in lines[3]  # eligible command fires inside the window


def test_ignored_windows_never_change_state(tmp_path, monkeypatch):
    sim_obj, _ = _session(tmp_path, [(1.0, "la"), (1.5, "lb"), (2.0, "la")], monkeypatch)
    assert sim_obj.state == STATE_LISTENING
    assert sim_obj.counts() == {"wakes": 0, "commands": 0, "timeouts": 0, "executed": 0}


# ── EXECUTING: after a command, 5 s of silence then back to listening ───────


def test_command_prints_executing_then_waits_five_seconds():
    s = sim()
    s.feed(2.0, det("wake"))
    event = s.feed(3.0, det("la", 0.97))

    line = event.line(5.0, 5.0)
    assert "COMMAND -> EXECUTING" in line
    assert "EXECUTING la 0.970" in line
    assert "listening again in 5.0s" in line

    assert s.feed(7.9, det("noise")) is None  # 4.9 s into execution
    assert s.state == STATE_EXECUTING
    done = s.feed(8.0, det("noise"))  # exactly 5.0 s after the command
    assert done is not None and done.kind == "executed"
    assert "EXECUTING -> LISTENING  done (5.0s)" in done.line(5.0, 5.0)
    assert s.state == STATE_LISTENING


def test_everything_is_ignored_while_executing():
    s = sim()
    s.feed(2.0, det("wake"))
    s.feed(3.0, det("la"))
    for t_s, label in [(3.5, "wake"), (4.0, "la"), (4.5, "lb"), (5.0, "noise")]:
        assert s.feed(t_s, det(label)) is None
        assert s.ignore_reason(det(label)) == "executing la"
    assert s.commands == ["la"]  # a second la did not stack
    assert s.wakes == 1  # the wake inside execution did not re-arm


def test_nothing_restarts_the_command_window_clock():
    """noise/wake inside COMMAND must not extend the 5 s budget."""
    s = sim()
    s.feed(2.0, det("wake"))
    for t_s, label in [(3.0, "noise"), (4.0, "wake"), (5.0, "noise"), (6.0, "lb")]:
        s.feed(t_s, det(label) if label != "lb" else det("none"))
    event = s.feed(7.0, det("none"))  # 5.0 s after the wake, not after the noise
    assert event.kind == "timeout"
    assert s.commands == []


def test_a_new_command_can_fire_after_execution_finishes():
    s = sim()
    s.feed(2.0, det("wake"))
    s.feed(3.0, det("la"))
    s.feed(8.0, det("noise"))  # executed → LISTENING
    assert s.feed(9.0, det("wake")).kind == "wake"
    assert s.feed(10.0, det("lb")).kind == "command"
    assert s.commands == ["la", "lb"]


def test_exec_zero_resumes_on_the_next_window():
    s = sim(exec_s=0.0)
    s.feed(2.0, det("wake"))
    s.feed(3.0, det("la"))
    assert s.state == STATE_EXECUTING
    assert s.feed(3.5, det("noise")).kind == "executed"


def test_negative_exec_is_rejected():
    with pytest.raises(ValueError):
        sim(exec_s=-1.0)


# ── aggregation: how the 5 s window turns detections into a decision ────────


def test_aggregate_first_commits_immediately():
    """Default `first`: the first eligible detection wins (old behaviour)."""
    s = sim(aggregate="first")
    s.feed(2.0, det("wake"))
    event = s.feed(3.0, det("lb", 0.90))
    assert event.kind == "command" and event.label == "lb"
    assert s.state == STATE_EXECUTING


def test_aggregate_max_waits_and_picks_the_strongest():
    """`max`: keep collecting; the best score at window end wins."""
    s = sim(aggregate="max")
    s.feed(2.0, det("wake"))
    assert s.feed(3.0, det("lb", 0.90)) is None  # deferred, not committed
    assert s.feed(4.0, det("la", 0.99)) is None
    assert s.state == STATE_COMMAND
    event = s.feed(7.0, det("noise"))  # window expires at 5.0 s after wake
    assert event.kind == "command" and event.label == "la"
    assert event.score == pytest.approx(0.99)
    assert "best of 2 detection(s) at window end" in event.line(5.0, 5.0)
    assert s.commands == ["la"]


def test_aggregate_max_without_detections_times_out():
    s = sim(aggregate="max")
    s.feed(2.0, det("wake"))
    assert s.feed(4.0, det("noise")) is None
    event = s.feed(7.0, det("noise"))
    assert event.kind == "timeout"
    assert s.commands == []


def test_aggregate_vote_fires_when_n_agree():
    s = sim(aggregate="vote", vote_n=2)
    s.feed(2.0, det("wake"))
    assert s.feed(3.0, det("la", 0.90)) is None  # one vote: keep listening
    event = s.feed(3.5, det("la", 0.95))  # second vote -> fire
    assert event.kind == "command" and event.label == "la"
    assert event.score == pytest.approx(0.95)  # best of the agreeing hits
    assert "2 vote(s)" in event.line(5.0, 5.0)


def test_aggregate_vote_ignores_a_single_outlier():
    """Two agreeing hits beat one louder outlier (the prefix-only failure)."""
    s = sim(aggregate="vote", vote_n=2)
    s.feed(2.0, det("wake"))
    s.feed(2.5, det("lb", 0.99))  # outlier (e.g. window caught only the prefix)
    assert s.feed(3.0, det("la", 0.90)) is None
    event = s.feed(3.5, det("la", 0.92))
    assert event.kind == "command" and event.label == "la"
    assert s.commands == ["la"]


def test_aggregate_vote_without_quorum_times_out():
    s = sim(aggregate="vote", vote_n=2)
    s.feed(2.0, det("wake"))
    assert s.feed(3.0, det("la", 0.95)) is None
    assert s.feed(4.0, det("lb", 0.96)) is None  # split evidence
    event = s.feed(7.0, det("noise"))
    assert event.kind == "timeout"
    assert s.commands == []


def test_aggregation_state_is_cleared_between_windows():
    s = sim(aggregate="max")
    s.feed(2.0, det("wake"))
    s.feed(3.0, det("la", 0.9))
    s.feed(7.0, det("noise"))          # fires la at expiry
    s.feed(12.0, det("noise"))         # execution ends
    s.feed(13.0, det("wake"))          # new window: no stale evidence
    event = s.feed(19.0, det("noise"))  # expires with nothing collected
    assert event.kind == "timeout"
    assert s.commands == ["la"]


@pytest.mark.parametrize("bad", ["median", "", "MAX"])
def test_invalid_aggregation_is_rejected(bad):
    with pytest.raises(ValueError):
        sim(aggregate=bad)


def test_vote_n_below_one_is_rejected():
    with pytest.raises(ValueError):
        sim(aggregate="vote", vote_n=0)
