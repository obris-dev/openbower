"""The per-call harness: the one catch site between a tool's function
and the framework. Every guard FIRES: an authored raise folds and the
run continues, a closer shuts the tool, a crash never escapes, and a
closed tool never even runs its function.

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from django.test import SimpleTestCase

from agents.constants import ToolStatus
from agents.runtime.deps import CellDeps
from agents.tools.base import NOTE_TOOL_CLOSED
from agents.tools.harness import NOTE_CALL_FAILED, wrap
from agents.tools.search.errors import SearchRateLimited, SearchUnreachable
from agents.tools.search.web_search import SPEC as WEB_SPEC


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(deps=CellDeps())


def _spec(function):
    """A registrable spec around a test function, borrowing the search
    vocabulary (the harness reads only name, closers, failure_modes)."""
    return replace(WEB_SPEC, function=function)


class ToolHarnessTests(SimpleTestCase):
    def test_an_authored_raise_folds_the_code_and_returns_the_note(self):
        def flaky(ctx, query):
            raise SearchUnreachable(note="try a different query")

        ctx = _ctx()
        body = json.loads(wrap(_spec(flaky))(ctx, "acme"))
        self.assertEqual(body, {"records": [], "note": "try a different query"})
        # Provisional fold: the tool wears the failure but stays
        # callable (unreachable is a HAZARD, not a closer).
        self.assertEqual(ctx.deps.tool_status["flaky"], "unreachable")
        self.assertNotIn("flaky", ctx.deps.served)

    def test_a_closer_raise_shuts_the_tool_and_says_so(self):
        def throttled(ctx, query):
            raise SearchRateLimited()

        ctx = _ctx()
        call = wrap(_spec(throttled))
        first = json.loads(call(ctx, "acme"))
        self.assertIn("throttled is unavailable", first["note"])
        self.assertEqual(ctx.deps.tool_status["throttled"], "rate_limited")

    def test_a_closed_tool_never_runs_its_function(self):
        ran: list[str] = []

        def spender(ctx, query):
            ran.append(query)
            raise SearchRateLimited()

        ctx = _ctx()
        call = wrap(_spec(spender))
        call(ctx, "first")
        second = json.loads(call(ctx, "second"))
        self.assertEqual(ran, ["first"])
        self.assertIn("do not call it again", second["note"])
        self.assertIn(NOTE_TOOL_CLOSED.split("{tool}")[1].split("{status}")[0][:20], second["note"])

    def test_a_crash_never_escapes_and_folds_as_error(self):
        def broken(ctx, query):
            raise RuntimeError("a library blew up with a secret in its message")

        ctx = _ctx()
        with self.assertLogs("agents.tools.harness", level="ERROR"):
            body = json.loads(wrap(_spec(broken))(ctx, "acme"))
        # The model and the record get the generic note and the code;
        # the traceback is the log's alone.
        self.assertEqual(body, {"records": [], "note": NOTE_CALL_FAILED})
        self.assertNotIn("secret", json.dumps(body))
        self.assertEqual(ctx.deps.tool_status["broken"], ToolStatus.ERROR)

    def test_the_wrapper_is_transparent_to_the_framework(self):
        # pydantic-ai reads the function's name and docstring to build
        # the schema the model sees; an opaque wrapper would rename
        # every tool "call".
        wrapped = wrap(WEB_SPEC)
        self.assertEqual(wrapped.__name__, "web_search")
        self.assertIn("Search the web", wrapped.__doc__)
