"""The operator's source matrix: one line per inference source in
config/providers.toml with what a LIVE probe says about it (ready
with its roster, no models yet, unreachable, rejected, or closed for
want of a key), so "can an agent run on this install" is one command
instead of a fill attempt. The exit code is the state the install's
setup step branches on (management/probe.py): ready beats empty
beats rejected beats down, so the code names the next thing to do
(pick a model, pull one, fix a key or URL, fix the network)."""

from __future__ import annotations

import httpx
from django.core.management.base import BaseCommand, CommandError

from ...providers import all_providers
from ..probe import ProbeExit, reason

_CONFIG = "config/providers.toml"
# A vendor roster runs to dozens of names; the matrix shows enough to
# recognise the source and counts the rest.
_SHOWN_MODELS = 8


def _roster_line(roster: list[str]) -> str:
    shown = ", ".join(roster[:_SHOWN_MODELS])
    rest = len(roster) - _SHOWN_MODELS
    if rest > 0:
        shown = f"{shown}, and {rest} more"
    noun = "model" if len(roster) == 1 else "models"
    return f"{len(roster)} {noun}: {shown}"


class Command(BaseCommand):
    help = f"Probe every inference source in {_CONFIG} and show which can run an agent."

    def handle(self, *args, **options) -> None:
        configured = 0
        ready = 0
        answered = 0
        rejected = 0
        for provider in all_providers():
            open_names = set(provider.sources())
            for name, source in provider.configured_sources.items():
                configured += 1
                label = f"{provider.name}/{name} ({source['base_url']})"
                if name not in open_names:
                    self.stdout.write(f"{label}: closed | needs an api_key in {_CONFIG}")
                    continue
                try:
                    roster = provider.probe(name)
                except httpx.RequestError as e:
                    # DNS, a refused connection, a timeout, a body the
                    # transport could not decode: nothing usable
                    # answered.
                    self.stdout.write(f"{label}: unreachable | {reason(e)}")
                    continue
                except ValueError as e:
                    # The roster reader's own refusals: the server
                    # answered and declined (a 401 for a bad key is the
                    # common one), or answered with something that is
                    # not a roster (a web page at a wrong port). The
                    # key or the URL, not the network.
                    rejected += 1
                    self.stdout.write(f"{label}: rejected | {reason(e)}")
                    continue
                answered += 1
                if not roster:
                    self.stdout.write(f"{label}: no models | the server answered with an empty roster")
                    continue
                ready += 1
                self.stdout.write(f"{label}: ready | {_roster_line(roster)}")
        if configured == 0:
            raise CommandError(
                f"no inference sources configured ({_CONFIG} is absent or empty)", returncode=ProbeExit.NOT_CONFIGURED
            )
        if ready:
            return
        if answered:
            raise CommandError("no inference source offers a model yet", returncode=ProbeExit.EMPTY)
        if rejected:
            raise CommandError("an inference source rejected its key or URL", returncode=ProbeExit.REJECTED)
        raise CommandError("no inference source answered", returncode=ProbeExit.DOWN)
