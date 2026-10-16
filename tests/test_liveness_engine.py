import unittest

from car_face_auth.src.liveness_engine import (
    LivenessObservation,
    LivenessSession,
    UnavailableLivenessEngine,
)


class LivenessSessionTests(unittest.TestCase):
    def test_requires_enough_live_observations(self):
        session = LivenessSession(window_size=4, minimum_live=3)
        session.add(LivenessObservation("live", 0.9))
        session.add(LivenessObservation("uncertain", 0.5))

        decision = session.decision()

        self.assertFalse(decision.passed)
        self.assertEqual(decision.status, "uncertain")
        self.assertEqual(decision.live_observations, 1)

    def test_passes_when_live_window_requirement_is_met(self):
        session = LivenessSession(window_size=4, minimum_live=3)
        for score in (0.8, 0.9, 0.85):
            session.add(LivenessObservation("live", score))

        decision = session.decision()

        self.assertTrue(decision.passed)
        self.assertEqual(decision.status, "live")
        self.assertEqual(decision.live_observations, 3)
        self.assertAlmostEqual(decision.score, 0.85)

    def test_spoof_observation_fails_the_sequence(self):
        session = LivenessSession(window_size=4, minimum_live=2)
        session.add(LivenessObservation("live", 0.9))
        session.add(LivenessObservation("spoof", 0.1, "screen artifact"))
        session.add(LivenessObservation("live", 0.9))

        decision = session.decision()

        self.assertFalse(decision.passed)
        self.assertEqual(decision.status, "spoof")
        self.assertEqual(decision.spoof_observations, 1)

    def test_window_is_bounded(self):
        session = LivenessSession(window_size=2, minimum_live=2)
        session.add(LivenessObservation("uncertain"))
        session.add(LivenessObservation("live", 0.8))
        session.add(LivenessObservation("live", 0.9))

        self.assertEqual(len(session.observations), 2)
        self.assertTrue(session.decision().passed)


class UnavailableLivenessEngineTests(unittest.TestCase):
    def test_default_engine_is_explicitly_unavailable(self):
        engine = UnavailableLivenessEngine()
        session = engine.create_session(purpose="scan")

        self.assertFalse(engine.available)
        result = engine.analyze(object(), face_count=1, session=session)
        self.assertEqual(result.status, "unavailable")
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
