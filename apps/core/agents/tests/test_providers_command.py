"""The operator's source matrix (`manage.py providers`): each source's
line names its state from a LIVE probe, and the exit code is the state
the install's setup step branches on. Mocked at the roster seam we own
(httpx).

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import httpx
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from agents.management.probe import ProbeExit
from common.testing import FakeResponse

from .sources import source

_LOCAL = source("ollama", "http://o.test/v1")
_CANONICAL_KEYLESS = source("openai", "https://api.openai.com/v1")
_CANONICAL_KEYED = source("openai", "https://api.openai.com/v1", api_key="k")


class ProvidersCommandTests(SimpleTestCase):
    def _lines(self, sources: dict, get=None) -> tuple[list[str], int]:
        out = StringIO()
        code = 0
        ctx = (
            patch("agents.providers.base.httpx.get", side_effect=get)
            if get
            else patch("agents.providers.base.httpx.get")
        )
        with ctx as mocked, self.settings(INFERENCE_SOURCES=sources):
            if get is None:
                mocked.side_effect = AssertionError("no probe expected")
            try:
                call_command("providers", stdout=out)
            except CommandError as e:
                code = e.returncode
        return out.getvalue().splitlines(), code

    def test_a_source_with_models_is_ready_and_exits_ok(self):
        def fake_get(url, **kwargs):
            return FakeResponse(200, {"data": [{"id": "gemma4:12b", "created": 2}, {"id": "llama3", "created": 1}]})

        lines, code = self._lines({"openai_compatible": _LOCAL}, fake_get)
        self.assertEqual(lines, ["openai_compatible/ollama (http://o.test/v1): ready | 2 models: gemma4:12b, llama3"])
        self.assertEqual(code, ProbeExit.OK)

    def test_an_empty_roster_is_named_and_exits_empty(self):
        # The server answered: it is configured right and waiting for a
        # model to be pulled, which is not the same as not answering.
        lines, code = self._lines({"openai_compatible": _LOCAL}, lambda url, **kw: FakeResponse(200, {"data": []}))
        self.assertEqual(
            lines, ["openai_compatible/ollama (http://o.test/v1): no models | the server answered with an empty roster"]
        )
        self.assertEqual(code, ProbeExit.EMPTY)

    def test_an_unreachable_source_carries_the_reason_and_exits_down(self):
        def fake_get(url, **kwargs):
            raise httpx.ConnectError("connection refused")

        lines, code = self._lines({"openai_compatible": _LOCAL}, fake_get)
        self.assertEqual(
            lines, ["openai_compatible/ollama (http://o.test/v1): unreachable | ConnectError: connection refused"]
        )
        self.assertEqual(code, ProbeExit.DOWN)

    def test_a_bad_key_is_refused_not_unreachable(self):
        # A 401 is the vendor declining the key; the matrix must send
        # the operator to the key, not the network.
        lines, code = self._lines(
            {"openai_compatible": _CANONICAL_KEYED}, lambda url, **kw: FakeResponse(401, {"error": "bad key"})
        )
        self.assertEqual(
            lines,
            ["openai_compatible/openai (https://api.openai.com/v1): refused | ValueError: roster probe returned 401"],
        )
        self.assertEqual(code, ProbeExit.DOWN)

    def test_a_keyless_canonical_source_is_closed_and_never_probed(self):
        lines, code = self._lines({"openai_compatible": _CANONICAL_KEYLESS})
        self.assertEqual(
            lines,
            [
                "openai_compatible/openai (https://api.openai.com/v1): closed | needs an api_key in config/providers.toml"
            ],
        )
        self.assertEqual(code, ProbeExit.DOWN)

    def test_no_sources_at_all_exits_not_configured(self):
        lines, code = self._lines({})
        self.assertEqual(lines, [])
        self.assertEqual(code, ProbeExit.NOT_CONFIGURED)

    def test_one_ready_source_among_failures_exits_ok(self):
        # The exit code is "can SOMETHING run", not "is everything up".
        def fake_get(url, **kwargs):
            if url.startswith("http://o.test"):
                return FakeResponse(200, {"data": [{"id": "gemma4:12b", "created": 1}]})
            raise httpx.ConnectError("connection refused")

        lines, code = self._lines({"openai_compatible": {**_LOCAL, **source("vllm", "http://gpu.test/v1")}}, fake_get)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("ready | 1 model: gemma4:12b"))
        self.assertTrue(lines[1].startswith("openai_compatible/vllm (http://gpu.test/v1): unreachable"))
        self.assertEqual(code, ProbeExit.OK)

    def test_an_empty_roster_beside_a_dead_source_exits_empty(self):
        # Something answered, so the next step is a pull, not a URL.
        def fake_get(url, **kwargs):
            if url.startswith("http://o.test"):
                return FakeResponse(200, {"data": []})
            raise httpx.ConnectError("connection refused")

        _, code = self._lines({"openai_compatible": {**_LOCAL, **source("vllm", "http://gpu.test/v1")}}, fake_get)
        self.assertEqual(code, ProbeExit.EMPTY)

    def test_a_long_roster_is_capped_with_a_count(self):
        def fake_get(url, **kwargs):
            return FakeResponse(200, {"data": [{"id": f"m{i}", "created": 20 - i} for i in range(20)]})

        lines, _ = self._lines({"openai_compatible": _LOCAL}, fake_get)
        self.assertEqual(
            lines,
            [
                "openai_compatible/ollama (http://o.test/v1): ready | 20 models: m0, m1, m2, m3, m4, m5, m6, m7, and 12 more"
            ],
        )
