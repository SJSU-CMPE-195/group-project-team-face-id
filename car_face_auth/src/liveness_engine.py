"""Backend liveness detection contract and per-session aggregation.

This module deliberately does not implement presentation-attack detection yet.
It defines the boundary that a future detector must implement while keeping
the current runtime fail-closed when no detector is configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


LivenessStatus = Literal["live", "spoof", "uncertain", "unavailable"]


@dataclass(frozen=True, slots=True)
class LivenessObservation:
    """One detector result for one captured frame."""

    status: LivenessStatus
    score: float | None = None
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "live"


@dataclass(frozen=True, slots=True)
class LivenessDecision:
    """Aggregated result for a sequence of frames."""

    status: LivenessStatus
    observations: int
    live_observations: int
    spoof_observations: int
    score: float | None = None
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "live"


class LivenessSession:
    """Collect bounded observations for one scan or enrollment session.

    The session only aggregates detector output. It does not inspect pixels and
    therefore remains useful with a future heuristic, ML model, or hardware-
    assisted implementation.
    """

    def __init__(self, *, window_size: int = 10, minimum_live: int = 6):
        if window_size < 1:
            raise ValueError("window_size must be positive")
        if not 1 <= minimum_live <= window_size:
            raise ValueError("minimum_live must be within the liveness window")
        self.window_size = window_size
        self.minimum_live = minimum_live
        self._observations: list[LivenessObservation] = []

    @property
    def observations(self) -> tuple[LivenessObservation, ...]:
        return tuple(self._observations)

    def add(self, observation: LivenessObservation) -> None:
        if not isinstance(observation, LivenessObservation):
            raise TypeError("observation must be a LivenessObservation")
        if len(self._observations) >= self.window_size:
            self._observations.pop(0)
        self._observations.append(observation)

    def decision(self) -> LivenessDecision:
        observations = len(self._observations)
        live = sum(item.status == "live" for item in self._observations)
        spoof = sum(item.status == "spoof" for item in self._observations)
        scores = [item.score for item in self._observations if item.score is not None]
        score = sum(scores) / len(scores) if scores else None

        if spoof:
            return LivenessDecision(
                status="spoof",
                observations=observations,
                live_observations=live,
                spoof_observations=spoof,
                score=score,
                reason="presentation attack suspected",
            )
        if live >= self.minimum_live:
            return LivenessDecision(
                status="live",
                observations=observations,
                live_observations=live,
                spoof_observations=spoof,
                score=score,
                reason="liveness requirement met",
            )
        status: LivenessStatus = (
            "unavailable"
            if any(item.status == "unavailable" for item in self._observations)
            else "uncertain"
        )
        return LivenessDecision(
            status=status,
            observations=observations,
            live_observations=live,
            spoof_observations=spoof,
            score=score,
            reason="not enough live observations",
        )


class LivenessEngine(Protocol):
    """Interface for the future pixel/model-based liveness implementation."""

    @property
    def available(self) -> bool: ...

    def create_session(self, *, purpose: str) -> LivenessSession: ...

    def analyze(
        self,
        frame: Any,
        *,
        face_count: int,
        session: LivenessSession,
    ) -> LivenessObservation: ...


class UnavailableLivenessEngine:
    """Explicit default until a real detector is selected and installed."""

    available = False

    def create_session(self, *, purpose: str) -> LivenessSession:
        del purpose
        return LivenessSession()

    def analyze(
        self,
        frame: Any,
        *,
        face_count: int,
        session: LivenessSession,
    ) -> LivenessObservation:
        del frame, face_count, session
        return LivenessObservation(
            status="unavailable",
            reason="no liveness detector is configured",
        )
