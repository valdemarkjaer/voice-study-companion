"""Safe runtime configuration for the credential-free public demonstration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """Raised when an explicitly selected runtime mode is incomplete or unsafe."""


class RuntimeMode(StrEnum):
    """Supported execution modes.

    Demo is intentionally the default. Live is only a configuration boundary;
    the public candidate does not silently connect to an external provider.
    """

    DEMO = "demo"
    LIVE = "live"


class SecretValue:
    """An in-memory secret whose ordinary representations are always redacted."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ConfigError("an empty credential was supplied")
        self.__value = value

    def reveal(self) -> str:
        """Return the value only to an adapter making an authorized live call."""

        return self.__value

    def __bool__(self) -> bool:
        return bool(self.__value)

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Configuration selected from process memory without file persistence."""

    mode: RuntimeMode = RuntimeMode.DEMO
    live_model_endpoint: str | None = field(default=None, repr=False)
    live_model_token: SecretValue | None = field(default=None, repr=False)
    live_card_endpoint: str | None = field(default=None, repr=False)
    live_card_token: SecretValue | None = field(default=None, repr=False)

    @property
    def uses_external_services(self) -> bool:
        return self.mode is RuntimeMode.LIVE

    def public_summary(self) -> dict[str, object]:
        """Return display-safe state without endpoint details or secret values."""

        return {
            "mode": self.mode.value,
            "external_services_enabled": self.uses_external_services,
            "model_adapter_configured": bool(
                self.live_model_endpoint and self.live_model_token
            ),
            "card_adapter_configured": bool(
                self.live_card_endpoint and self.live_card_token
            ),
        }


def _optional_secret(environ: Mapping[str, str], name: str) -> SecretValue | None:
    value = environ.get(name, "").strip()
    return SecretValue(value) if value else None


def _optional_endpoint(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ConfigError(f"{name} must be an https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"{name} must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{name} must not contain a query or fragment")
    return value


def load_runtime_config(
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    """Load configuration from a supplied mapping or the process environment.

    No file is read or written. ``VSC_MODE=live`` parses and validates the
    reserved adapter schema for contract tests, but it does not make a live
    integration runnable. The server bundled with this showcase accepts demo
    mode only.
    """

    source = os.environ if environ is None else environ
    raw_mode = source.get("VSC_MODE", RuntimeMode.DEMO.value).strip().lower()
    try:
        mode = RuntimeMode(raw_mode)
    except ValueError as exc:
        raise ConfigError("VSC_MODE must be 'demo' or 'live'") from exc

    config = RuntimeConfig(
        mode=mode,
        live_model_endpoint=_optional_endpoint(source, "VSC_LIVE_MODEL_ENDPOINT"),
        live_model_token=_optional_secret(source, "VSC_LIVE_MODEL_TOKEN"),
        live_card_endpoint=_optional_endpoint(source, "VSC_LIVE_CARD_ENDPOINT"),
        live_card_token=_optional_secret(source, "VSC_LIVE_CARD_TOKEN"),
    )
    if mode is RuntimeMode.LIVE:
        model_ready = config.live_model_endpoint and config.live_model_token
        card_ready = config.live_card_endpoint and config.live_card_token
        if not model_ready and not card_ready:
            raise ConfigError(
                "live mode requires one complete endpoint-and-credential pair"
            )
    return config
