"""Allowlisted capability routing with turn pinning and safe failover."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from uuid import uuid4

from .domain import (
    AudioEncoding,
    Capability,
    LanguageTag,
    Operation,
    PipelineError,
    ProviderCapabilities,
    TurnIdentity,
)


class RoutingError(RuntimeError):
    pass


class UnknownRouteError(RoutingError):
    pass


class CapabilityPreflightError(RoutingError):
    pass


class RoutePinError(RoutingError):
    pass


class FailureOutcome(StrEnum):
    UNCERTAIN = "uncertain"
    RECOVERABLE = "recoverable"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    features: frozenset[Capability] = frozenset()
    languages: frozenset[LanguageTag] = frozenset()
    audio_encoding: AudioEncoding | None = None


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    route_id: str
    provider: str
    model: str
    capabilities: ProviderCapabilities

    def __post_init__(self) -> None:
        for name in ("route_id", "provider", "model"):
            value = str(getattr(self, name))
            if not value or not value.strip():
                raise ValueError(f"{name}_must_not_be_blank")


@dataclass(frozen=True, slots=True)
class OperationRoutes:
    operation: Operation
    route_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.route_ids:
            raise ValueError("operation_requires_at_least_one_route")
        if len(set(self.route_ids)) != len(self.route_ids):
            raise ValueError("operation_routes_must_not_contain_duplicates")


@dataclass(frozen=True, slots=True)
class RouteProfile:
    profile_id: str
    operations: tuple[OperationRoutes, ...]

    def __post_init__(self) -> None:
        if not self.profile_id or not self.profile_id.strip():
            raise ValueError("profile_id_must_not_be_blank")
        operation_names = [item.operation for item in self.operations]
        if len(set(operation_names)) != len(operation_names):
            raise ValueError("profile_must_not_repeat_operations")

    def route_ids_for(self, operation: Operation) -> tuple[str, ...]:
        match = next(
            (item for item in self.operations if item.operation is operation),
            None,
        )
        if match is None:
            raise CapabilityPreflightError(
                f"profile_has_no_route_for_{operation.value}"
            )
        return match.route_ids


class RouteRegistry:
    def __init__(self, routes: tuple[RouteDefinition, ...]) -> None:
        if not routes:
            raise ValueError("route_registry_must_not_be_empty")
        if len({route.route_id for route in routes}) != len(routes):
            raise ValueError("route_ids_must_be_unique")
        self._routes = {route.route_id: route for route in routes}

    def get(self, route_id: str) -> RouteDefinition:
        try:
            return self._routes[route_id]
        except KeyError as exc:
            raise UnknownRouteError(f"route_not_allowlisted:{route_id}") from exc

    def validate_profile(self, profile: RouteProfile) -> None:
        for operation_routes in profile.operations:
            for route_id in operation_routes.route_ids:
                route = self.get(route_id)
                if operation_routes.operation not in route.capabilities.operations:
                    raise CapabilityPreflightError(
                        f"route_{route_id}_does_not_support_"
                        f"{operation_routes.operation.value}"
                    )


@dataclass(frozen=True, slots=True)
class RoutePin:
    pin_id: str
    identity: TurnIdentity
    operation: Operation
    route: RouteDefinition
    requirement: CapabilityRequirement


@dataclass(frozen=True, slots=True)
class FailoverDecision:
    outcome: FailureOutcome
    failed_route_id: str
    next_route_id: str | None
    retry_current_turn: bool
    requires_new_turn: bool
    error: PipelineError


class RouteRouter:
    """Pin one route per turn; any fallback begins only on a new turn."""

    def __init__(self, registry: RouteRegistry, profile: RouteProfile) -> None:
        registry.validate_profile(profile)
        self._registry = registry
        self._profile = profile
        self._pins: dict[tuple[str, str, Operation], RoutePin] = {}
        self._closed_turns: set[tuple[str, str, Operation]] = set()
        self._blocked_routes: set[tuple[str, Operation, str]] = set()
        self._lock = RLock()

    @staticmethod
    def _key(
        identity: TurnIdentity,
        operation: Operation,
    ) -> tuple[str, str, Operation]:
        return (identity.session_id, identity.turn_id, operation)

    def begin_turn(
        self,
        identity: TurnIdentity,
        operation: Operation,
        requirement: CapabilityRequirement,
    ) -> RoutePin:
        key = self._key(identity, operation)
        with self._lock:
            if key in self._closed_turns:
                raise RoutePinError("closed_turn_cannot_be_retried")
            existing = self._pins.get(key)
            if existing is not None:
                if not existing.identity.same_generation(identity):
                    raise RoutePinError("turn_id_reused_with_another_generation")
                return existing

            route = self._select(identity.session_id, operation, requirement)
            pin = RoutePin(
                pin_id=str(uuid4()),
                identity=identity,
                operation=operation,
                route=route,
                requirement=requirement,
            )
            self._pins[key] = pin
            return pin

    def complete(self, pin: RoutePin) -> None:
        with self._lock:
            self._require_active_pin(pin)
            key = self._key(pin.identity, pin.operation)
            del self._pins[key]
            self._closed_turns.add(key)

    def fail(
        self,
        pin: RoutePin,
        error: PipelineError,
        *,
        provider_accepted_input: bool,
        action_bearing: bool,
    ) -> FailoverDecision:
        with self._lock:
            self._require_active_pin(pin)
            if error.identity is not None and not error.identity.same_generation(
                pin.identity
            ):
                raise RoutePinError("route_error_generation_does_not_match_pin")
            if error.operation is not None and error.operation is not pin.operation:
                raise RoutePinError("route_error_operation_does_not_match_pin")
            key = self._key(pin.identity, pin.operation)
            del self._pins[key]
            self._closed_turns.add(key)
            self._blocked_routes.add(
                (pin.identity.session_id, pin.operation, pin.route.route_id)
            )

            try:
                next_route = self._select(
                    pin.identity.session_id,
                    pin.operation,
                    pin.requirement,
                )
            except CapabilityPreflightError:
                next_route = None

            uncertain = provider_accepted_input or action_bearing
            if uncertain:
                outcome = FailureOutcome.UNCERTAIN
            elif next_route is None:
                outcome = FailureOutcome.DEGRADED
            else:
                outcome = FailureOutcome.RECOVERABLE
            return FailoverDecision(
                outcome=outcome,
                failed_route_id=pin.route.route_id,
                next_route_id=(next_route.route_id if next_route is not None else None),
                retry_current_turn=False,
                requires_new_turn=True,
                error=error,
            )

    def restore_route(self, route_id: str) -> None:
        self._registry.get(route_id)
        with self._lock:
            self._blocked_routes = {
                blocked for blocked in self._blocked_routes if blocked[2] != route_id
            }

    def _select(
        self,
        session_id: str,
        operation: Operation,
        requirement: CapabilityRequirement,
    ) -> RouteDefinition:
        incompatible: list[str] = []
        for route_id in self._profile.route_ids_for(operation):
            if (session_id, operation, route_id) in self._blocked_routes:
                continue
            route = self._registry.get(route_id)
            if route.capabilities.supports(
                operation,
                features=requirement.features,
                languages=requirement.languages,
                audio_encoding=requirement.audio_encoding,
            ):
                return route
            incompatible.append(route_id)
        detail = ",".join(incompatible) if incompatible else "none_available"
        raise CapabilityPreflightError(
            f"no_compatible_route_for_{operation.value}:{detail}"
        )

    def _require_active_pin(self, pin: RoutePin) -> None:
        current = self._pins.get(self._key(pin.identity, pin.operation))
        if current is None or current.pin_id != pin.pin_id:
            raise RoutePinError("route_pin_is_not_active")
