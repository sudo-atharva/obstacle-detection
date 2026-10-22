"""Obstacle-response state machine. Contains no GUI or camera code, so it can be unit-tested.

    CRUISING ──obstacle──▶ AWAITING_PILOT (slows down, asks the pilot)
                              │ pilot says "forward" ──▶ PROCEEDING (moves on slowly)
                              │ pilot says "stop"    ──▶ STOPPED
                              │ no answer in time    ──▶ STOPPED
                              └ obstacle clears      ──▶ CRUISING
    PROCEEDING ──path clear──▶ CRUISING
    any state ──obstacle too close──▶ STOPPED (emergency, no question asked)
    STOPPED ──pilot presses resume──▶ PROCEEDING / CRUISING
"""
import time
from dataclasses import dataclass
from enum import Enum


class Level(Enum):
    CLEAR = 0
    WARNING = 1   # obstacle ahead: slow down and ask the pilot
    CRITICAL = 2  # obstacle very close: stop right away


class State(Enum):
    CRUISING = "Cruising"
    AWAITING_PILOT = "Obstacle - waiting for pilot"
    PROCEEDING = "Proceeding with caution"
    STOPPED = "Stopped"


@dataclass
class Config:
    cruise_speed: float = 1.0      # speeds are fractions of full speed, 0..1
    slow_speed: float = 0.3        # speed while waiting for the pilot
    proceed_speed: float = 0.3     # speed after the pilot approved moving on
    pilot_timeout: float = 5.0     # seconds the pilot has to answer
    clear_time: float = 1.5        # obstacle must be gone this long before speeding up
    decel_rate: float = 0.8        # speed units per second when slowing
    accel_rate: float = 0.3        # speed units per second when speeding up


class ObstacleController:
    def __init__(self, config: Config | None = None, clock=time.monotonic):
        self.config = config or Config()
        self.clock = clock
        self.state = State.CRUISING
        self.speed = 0.0
        self.stop_reason = ""
        self._asked_at = None
        self._clear_since = None
        self._level = Level.CLEAR
        self._last_update = clock()

    # ---- pilot input ---------------------------------------------------
    def pilot_forward(self):
        """Pilot allows moving forward (answer to the question, or resume after a stop)."""
        if self.state == State.AWAITING_PILOT:
            self._set_state(State.PROCEEDING)
        elif self.state == State.STOPPED and self._level != Level.CRITICAL:
            self._set_state(State.PROCEEDING if self._level == Level.WARNING else State.CRUISING)

    def pilot_stop(self):
        if self.state != State.STOPPED:
            self._stop("Pilot chose to stop")

    def emergency_stop(self, reason: str):
        """Stop at once for a non-obstacle reason, e.g. the camera feed was lost."""
        if self.state != State.STOPPED:
            self._stop(reason)
            self.speed = 0.0

    # ---- main loop -----------------------------------------------------
    def update(self, level: Level) -> float:
        """Feed the latest obstacle reading; returns the speed to send to the motors."""
        now = self.clock()
        dt = max(0.0, now - self._last_update)
        self._last_update = now
        self._level = level

        if level == Level.CLEAR:
            if self._clear_since is None:
                self._clear_since = now
        else:
            self._clear_since = None
        clear_long_enough = (
            self._clear_since is not None and now - self._clear_since >= self.config.clear_time
        )

        if level == Level.CRITICAL and self.state != State.STOPPED:
            self._stop("Obstacle too close")
        elif self.state == State.CRUISING and level == Level.WARNING:
            self._set_state(State.AWAITING_PILOT)
        elif self.state == State.AWAITING_PILOT:
            if clear_long_enough:
                self._set_state(State.CRUISING)
            elif self.seconds_left() <= 0:
                self._stop("No answer from pilot")
        elif self.state == State.PROCEEDING and clear_long_enough:
            self._set_state(State.CRUISING)

        self._ramp_speed(dt)
        return self.speed

    def seconds_left(self) -> float:
        """Time the pilot still has to answer (0 when no question is open)."""
        if self.state != State.AWAITING_PILOT:
            return 0.0
        return max(0.0, self.config.pilot_timeout - (self.clock() - self._asked_at))

    @property
    def target_speed(self) -> float:
        return {
            State.CRUISING: self.config.cruise_speed,
            State.AWAITING_PILOT: self.config.slow_speed,
            State.PROCEEDING: self.config.proceed_speed,
            State.STOPPED: 0.0,
        }[self.state]

    # ---- internals -----------------------------------------------------
    def _ramp_speed(self, dt):
        target = self.target_speed
        if self.state == State.STOPPED:
            self.speed = 0.0  # stopping is never gradual
        elif self.speed > target:
            self.speed = max(target, self.speed - self.config.decel_rate * dt)
        else:
            self.speed = min(target, self.speed + self.config.accel_rate * dt)

    def _stop(self, reason):
        self.stop_reason = reason
        self._set_state(State.STOPPED)

    def _set_state(self, state):
        self.state = state
        if state == State.AWAITING_PILOT:
            self._asked_at = self.clock()
        if state != State.STOPPED:
            self.stop_reason = ""
