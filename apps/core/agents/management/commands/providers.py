"""The operator's source matrix: one line per inference source in
config/providers.toml with what a LIVE probe says about it (ready
with its roster, no models yet, unreachable with the reason, or closed
for want of a key), so "can an agent run on this install" is one
command instead of a fill attempt. Exits non-zero when no source is
ready, which is what the install's setup step branches on."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ...providers import providers

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
        ready = 0
        configured = 0
        for provider in providers():
            open_names = set(provider.sources())
            for name, source in provider.configured_sources.items():
                configured += 1
                label = f"{provider.name}/{name} ({source['base_url']})"
                if name not in open_names:
                    self.stdout.write(f"{label}: closed | needs an api_key in {_CONFIG}")
                    continue
                try:
                    roster = provider.probe(name)
                except Exception as e:
                    self.stdout.write(f"{label}: unreachable | {type(e).__name__}: {e}")
                    continue
                if not roster:
                    self.stdout.write(f"{label}: no models | the server answered with an empty roster")
                    continue
                ready += 1
                self.stdout.write(f"{label}: ready | {_roster_line(roster)}")
        if configured == 0:
            raise CommandError(f"no inference sources configured ({_CONFIG} is absent or empty)")
        if ready == 0:
            raise CommandError("no inference source is ready")
