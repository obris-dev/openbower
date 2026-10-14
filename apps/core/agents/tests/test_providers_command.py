"""The operator's source matrix (`manage.py providers`): each source's
line names its state from a LIVE probe, and the exit code says whether
any source can run an agent, which the install's setup step branches
on. Mocked at the roster seam we own (httpx).

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import httpx
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from common.testing import FakeResponse

from .sources import source

_LOCAL = source("ollama", "http://o.test/v1")
_CANONICAL_KEYLESS = source("openai", "https://api.openai.com/v1")


class ProvidersCommandTests(SimpleTestCase):
    def _lines(self, sources: dict) -> list[str]:
        out = StringIO()
        with self.settings(INFERENCE_SOURCES=sources):
            call_command("providers", stdout=out)
        return out.getvalue().splitlines()

    def test_a_source_with_models_is_ready_and_the_command_succeeds(self):
        def fake_get(url, **kwargs):
            return FakeResponse(200, {"data": [{"id": "gemma4:12b", "created": 2}, {"id": "llama3", "created": 1}]})

        with patch("agents.providers.base.httpx.get", side_effect=fake_get):
            lines = self._lines({"openai_compatible": _LOCAL})
        self.assertEqual(lines, ["openai_compatible/ollama (http://o.test/v1): ready | 2 models: gemma4:12b, llama3"])

    def test_an_empty_roster_is_named_and_fails(self):
        def fake_get(url, **kwargs):
            return FakeResponse(200, {"data": []})

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.assertRaisesMessage(CommandError, "no inference source is ready"),
            self.settings(INFERENCE_SOURCES={"openai_compatible": _LOCAL}),
        ):
            out = StringIO()
            try:
                call_command("providers", stdout=out)
            finally:
                self.assertEqual(
                    out.getvalue().splitlines(),
                    [
                        "openai_compatible/ollama (http://o.test/v1): no models | the server answered with an empty roster"
                    ],
                )

    def test_an_unreachable_source_carries_the_reason_and_fails(self):
        def fake_get(url, **kwargs):
            raise httpx.ConnectError("connection refused")

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.assertRaisesMessage(CommandError, "no inference source is ready"),
            self.settings(INFERENCE_SOURCES={"openai_compatible": _LOCAL}),
        ):
            out = StringIO()
            try:
                call_command("providers", stdout=out)
            finally:
                self.assertEqual(
                    out.getvalue().splitlines(),
                    ["openai_compatible/ollama (http://o.test/v1): unreachable | ConnectError: connection refused"],
                )

    def test_a_keyless_canonical_source_is_closed_and_never_probed(self):
        with (
            patch("agents.providers.base.httpx.get") as get,
            self.assertRaisesMessage(CommandError, "no inference source is ready"),
            self.settings(INFERENCE_SOURCES={"openai_compatible": _CANONICAL_KEYLESS}),
        ):
            out = StringIO()
            try:
                call_command("providers", stdout=out)
            finally:
                get.assert_not_called()
                self.assertEqual(
                    out.getvalue().splitlines(),
                    [
                        "openai_compatible/openai (https://api.openai.com/v1): closed | needs an api_key in config/providers.toml"
                    ],
                )

    def test_a_long_roster_is_capped_with_a_count(self):
        def fake_get(url, **kwargs):
            return FakeResponse(200, {"data": [{"id": f"m{i}", "created": 20 - i} for i in range(20)]})

        with patch("agents.providers.base.httpx.get", side_effect=fake_get):
            lines = self._lines({"openai_compatible": _LOCAL})
        self.assertEqual(
            lines,
            [
                "openai_compatible/ollama (http://o.test/v1): ready | 20 models: m0, m1, m2, m3, m4, m5, m6, m7, and 12 more"
            ],
        )

    def test_no_sources_at_all_says_so(self):
        with (
            self.assertRaisesMessage(CommandError, "no inference sources configured"),
            self.settings(INFERENCE_SOURCES={}),
        ):
            call_command("providers", stdout=StringIO())

    def test_one_ready_source_among_failures_succeeds(self):
        # The exit code is "can SOMETHING run", not "is everything up".
        def fake_get(url, **kwargs):
            if url.startswith("http://o.test"):
                return FakeResponse(200, {"data": [{"id": "gemma4:12b", "created": 1}]})
            raise httpx.ConnectError("connection refused")

        with patch("agents.providers.base.httpx.get", side_effect=fake_get):
            lines = self._lines(
                {"openai_compatible": {**_LOCAL, **source("vllm", "http://gpu.test/v1")}},
            )
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("ready | 1 model: gemma4:12b"))
        self.assertTrue(lines[1].startswith("openai_compatible/vllm (http://gpu.test/v1): unreachable"))
