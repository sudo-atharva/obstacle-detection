import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controller import Config, Level, ObstacleController, State


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(**cfg):
    clock = FakeClock()
    ctl = ObstacleController(Config(**cfg), clock=clock)
    return ctl, clock


def run(ctl, clock, level, seconds, step=0.1):
    for _ in range(round(seconds / step)):
        clock.t += step
        ctl.update(level)


def test_accelerates_to_cruise_when_clear():
    ctl, clock = make()
    run(ctl, clock, Level.CLEAR, 10)
    assert ctl.state == State.CRUISING
    assert ctl.speed == 1.0


def test_obstacle_slows_down_and_asks_pilot():
    ctl, clock = make()
    run(ctl, clock, Level.CLEAR, 10)
    run(ctl, clock, Level.WARNING, 2)
    assert ctl.state == State.AWAITING_PILOT
    assert ctl.speed < 1.0
    run(ctl, clock, Level.WARNING, 1)
    assert ctl.speed == 0.3


def test_no_answer_stops():
    ctl, clock = make(pilot_timeout=5)
    run(ctl, clock, Level.CLEAR, 10)
    run(ctl, clock, Level.WARNING, 4.5)
    assert ctl.state == State.AWAITING_PILOT
    run(ctl, clock, Level.WARNING, 1)
    assert ctl.state == State.STOPPED
    assert ctl.speed == 0.0
    assert ctl.stop_reason == "No answer from pilot"


def test_pilot_forward_proceeds_slowly_then_cruises_when_clear():
    ctl, clock = make()
    run(ctl, clock, Level.CLEAR, 10)
    run(ctl, clock, Level.WARNING, 1)
    ctl.pilot_forward()
    run(ctl, clock, Level.WARNING, 10)  # well past the timeout: must not stop
    assert ctl.state == State.PROCEEDING
    assert ctl.speed == 0.3
    run(ctl, clock, Level.CLEAR, 2)
    assert ctl.state == State.CRUISING


def test_pilot_stop():
    ctl, clock = make()
    run(ctl, clock, Level.WARNING, 1)
    ctl.pilot_stop()
    run(ctl, clock, Level.WARNING, 0.1)
    assert ctl.state == State.STOPPED
    assert ctl.speed == 0.0


def test_critical_obstacle_stops_without_asking():
    ctl, clock = make()
    run(ctl, clock, Level.CLEAR, 10)
    run(ctl, clock, Level.CRITICAL, 0.1)
    assert ctl.state == State.STOPPED
    assert ctl.speed == 0.0


def test_critical_stops_even_after_pilot_approved():
    ctl, clock = make()
    run(ctl, clock, Level.WARNING, 1)
    ctl.pilot_forward()
    run(ctl, clock, Level.CRITICAL, 0.1)
    assert ctl.state == State.STOPPED


def test_obstacle_clearing_before_answer_resumes_cruise():
    ctl, clock = make()
    run(ctl, clock, Level.WARNING, 1)
    run(ctl, clock, Level.CLEAR, 2)
    assert ctl.state == State.CRUISING


def test_brief_flicker_does_not_cancel_question():
    ctl, clock = make(clear_time=1.5)
    run(ctl, clock, Level.WARNING, 1)
    run(ctl, clock, Level.CLEAR, 0.5)
    assert ctl.state == State.AWAITING_PILOT


def test_resume_after_stop_requires_pilot_and_not_critical():
    ctl, clock = make(pilot_timeout=1)
    run(ctl, clock, Level.WARNING, 2)
    assert ctl.state == State.STOPPED
    run(ctl, clock, Level.WARNING, 5)
    assert ctl.state == State.STOPPED  # stays stopped on its own
    run(ctl, clock, Level.CRITICAL, 0.1)
    ctl.pilot_forward()
    assert ctl.state == State.STOPPED  # refuses while too close
    run(ctl, clock, Level.CLEAR, 0.1)
    ctl.pilot_forward()
    assert ctl.state == State.CRUISING
