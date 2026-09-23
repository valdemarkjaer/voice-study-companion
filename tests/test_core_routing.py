from __future__ import annotations

import unittest

from voice_study_companion.contracts import LanguageTag
from voice_study_companion.core.domain import (
    AudioEncoding,
    Capability,
    ErrorCode,
    Operation,
    PipelineError,
    ProviderCapabilities,
    TurnIdentity,
)
from voice_study_companion.core.routing import (
    CapabilityPreflightError,
    CapabilityRequirement,
    FailureOutcome,
    OperationRoutes,
    RouteDefinition,
    RoutePinError,
    RouteProfile,
    RouteRegistry,
    RouteRouter,
    UnknownRouteError,
)


def _caps(*, keyword_hints: bool = True) -> ProviderCapabilities:
    features = {
        Capability.STREAMING_INPUT,
        Capability.CANCELLATION,
        Capability.MULTILINGUAL,
    }
    if keyword_hints:
        features.add(Capability.KEYWORD_HINTS)
    return ProviderCapabilities(
        operations=frozenset({Operation.TRANSCRIPTION}),
        features=frozenset(features),
        languages=frozenset({LanguageTag("pt-BR"), LanguageTag("en-US")}),
        audio_encodings=frozenset({AudioEncoding.PCM_S16LE}),
    )


def _router() -> RouteRouter:
    routes = (
        RouteDefinition("offline-primary", "fake-a", "script-a", _caps()),
        RouteDefinition("offline-fallback", "fake-b", "script-b", _caps()),
    )
    profile = RouteProfile(
        "offline-economy",
        (
            OperationRoutes(
                Operation.TRANSCRIPTION,
                ("offline-primary", "offline-fallback"),
            ),
        ),
    )
    return RouteRouter(RouteRegistry(routes), profile)


def _identity(turn: str, generation: int) -> TurnIdentity:
    return TurnIdentity(
        "session",
        "synthetic-card",
        turn,
        generation,
        generation,
    )


def _requirement() -> CapabilityRequirement:
    return CapabilityRequirement(
        features=frozenset(
            {
                Capability.STREAMING_INPUT,
                Capability.CANCELLATION,
                Capability.KEYWORD_HINTS,
            }
        ),
        languages=frozenset({LanguageTag("pt-BR"), LanguageTag("en-US")}),
        audio_encoding=AudioEncoding.PCM_S16LE,
    )


def _error(identity: TurnIdentity, *, uncertain: bool) -> PipelineError:
    return PipelineError(
        identity=identity,
        code=ErrorCode.TIMEOUT,
        operation=Operation.TRANSCRIPTION,
        public_message="The selected transcription route timed out.",
        retry_safe=not uncertain,
        uncertain_outcome=uncertain,
    )


class CoreRoutingTests(unittest.TestCase):
    def test_route_is_pinned_and_failover_waits_for_new_turn(self) -> None:
        router = _router()
        first_identity = _identity("turn-1", 1)
        pin = router.begin_turn(
            first_identity,
            Operation.TRANSCRIPTION,
            _requirement(),
        )
        self.assertEqual(pin.route.route_id, "offline-primary")
        self.assertEqual(
            router.begin_turn(
                first_identity,
                Operation.TRANSCRIPTION,
                _requirement(),
            ),
            pin,
        )

        decision = router.fail(
            pin,
            _error(first_identity, uncertain=True),
            provider_accepted_input=True,
            action_bearing=True,
        )
        self.assertIs(decision.outcome, FailureOutcome.UNCERTAIN)
        self.assertEqual(decision.next_route_id, "offline-fallback")
        self.assertFalse(decision.retry_current_turn)
        self.assertTrue(decision.requires_new_turn)
        with self.assertRaisesRegex(RoutePinError, "closed_turn"):
            router.begin_turn(
                first_identity,
                Operation.TRANSCRIPTION,
                _requirement(),
            )

        next_pin = router.begin_turn(
            _identity("turn-2", 2),
            Operation.TRANSCRIPTION,
            _requirement(),
        )
        self.assertEqual(next_pin.route.route_id, "offline-fallback")
        other_identity = TurnIdentity(
            "other-session",
            "synthetic-card",
            "turn-1",
            1,
            1,
        )
        other_pin = router.begin_turn(
            other_identity,
            Operation.TRANSCRIPTION,
            _requirement(),
        )
        self.assertEqual(other_pin.route.route_id, "offline-primary")

    def test_failure_before_acceptance_is_recoverable_not_same_turn_retry(
        self,
    ) -> None:
        router = _router()
        identity = _identity("turn-1", 1)
        pin = router.begin_turn(
            identity,
            Operation.TRANSCRIPTION,
            _requirement(),
        )
        decision = router.fail(
            pin,
            _error(identity, uncertain=False),
            provider_accepted_input=False,
            action_bearing=False,
        )
        self.assertIs(decision.outcome, FailureOutcome.RECOVERABLE)
        self.assertFalse(decision.retry_current_turn)

    def test_capability_preflight_fails_before_pin(self) -> None:
        route = RouteDefinition(
            "limited",
            "fake",
            "limited-script",
            _caps(keyword_hints=False),
        )
        router = RouteRouter(
            RouteRegistry((route,)),
            RouteProfile(
                "limited",
                (OperationRoutes(Operation.TRANSCRIPTION, ("limited",)),),
            ),
        )
        with self.assertRaisesRegex(CapabilityPreflightError, "no_compatible"):
            router.begin_turn(
                _identity("turn-1", 1),
                Operation.TRANSCRIPTION,
                _requirement(),
            )

    def test_profile_rejects_unknown_and_incompatible_routes(self) -> None:
        registry = RouteRegistry(
            (RouteDefinition("stt", "fake", "script", _caps()),)
        )
        with self.assertRaisesRegex(UnknownRouteError, "not_allowlisted"):
            registry.validate_profile(
                RouteProfile(
                    "bad",
                    (OperationRoutes(Operation.TRANSCRIPTION, ("unknown",)),),
                )
            )
        with self.assertRaisesRegex(
            CapabilityPreflightError,
            "does_not_support",
        ):
            registry.validate_profile(
                RouteProfile(
                    "bad-operation",
                    (OperationRoutes(Operation.SYNTHESIS, ("stt",)),),
                )
            )


if __name__ == "__main__":
    unittest.main()
