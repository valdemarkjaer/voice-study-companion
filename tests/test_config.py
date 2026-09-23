from __future__ import annotations

import io
import logging
import unittest

from voice_study_companion.config import (
    ConfigError,
    RuntimeMode,
    load_runtime_config,
)


class RuntimeConfigTests(unittest.TestCase):
    def test_empty_environment_selects_offline_demo(self) -> None:
        config = load_runtime_config({})

        self.assertEqual(config.mode, RuntimeMode.DEMO)
        self.assertFalse(config.uses_external_services)
        self.assertEqual(
            config.public_summary(),
            {
                "mode": "demo",
                "external_services_enabled": False,
                "model_adapter_configured": False,
                "card_adapter_configured": False,
            },
        )

    def test_placeholders_do_not_activate_live_mode(self) -> None:
        config = load_runtime_config(
            {
                "VSC_LIVE_MODEL_ENDPOINT": "https://provider.example.invalid/v1",
                "VSC_LIVE_MODEL_TOKEN": "local-only-value",
            }
        )

        self.assertEqual(config.mode, RuntimeMode.DEMO)
        self.assertFalse(config.uses_external_services)

    def test_live_mode_requires_complete_adapter_pair(self) -> None:
        with self.assertRaisesRegex(ConfigError, "complete"):
            load_runtime_config({"VSC_MODE": "live"})

    def test_secret_is_absent_from_repr_summary_and_log_output(self) -> None:
        sentinel = "portfolio-test-secret-value"
        config = load_runtime_config(
            {
                "VSC_MODE": "live",
                "VSC_LIVE_MODEL_ENDPOINT": "https://provider.example.invalid/v1",
                "VSC_LIVE_MODEL_TOKEN": sentinel,
            }
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("voice-study-companion.config-test")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            logger.info("runtime=%r summary=%r", config, config.public_summary())
        finally:
            logger.removeHandler(handler)

        rendered = f"{config!r} {config.public_summary()!r} {stream.getvalue()}"
        self.assertNotIn(sentinel, rendered)
        self.assertIn("<redacted>", repr(config.live_model_token))
        self.assertEqual(config.live_model_token.reveal(), sentinel)

    def test_endpoint_rejects_embedded_credentials(self) -> None:
        with self.assertRaisesRegex(ConfigError, "embedded credentials"):
            load_runtime_config(
                {
                    "VSC_LIVE_MODEL_ENDPOINT": "https://name:value@example.invalid/v1"
                }
            )

    def test_endpoint_query_and_fragment_cannot_become_loggable_secrets(self) -> None:
        sentinel = "endpoint-secret-value"
        for endpoint in (
            f"https://provider.example.invalid/v1?api_key={sentinel}",
            f"https://provider.example.invalid/v1#{sentinel}",
        ):
            with self.subTest(endpoint=endpoint.split(sentinel)[0]):
                with self.assertRaisesRegex(ConfigError, "query or fragment"):
                    load_runtime_config({"VSC_LIVE_MODEL_ENDPOINT": endpoint})

    def test_endpoint_is_redacted_from_ordinary_config_representation(self) -> None:
        endpoint = "https://private-adapter.example.invalid/v1"
        config = load_runtime_config({"VSC_LIVE_MODEL_ENDPOINT": endpoint})

        self.assertNotIn(endpoint, repr(config))


if __name__ == "__main__":
    unittest.main()
