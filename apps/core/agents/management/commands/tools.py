"""The operator's tool matrix: one line per vendor-served tool, the
wired vendor and its readiness, then the whole supported roster the
same way, so "did my config/tools.toml edit take" is one command
instead of a fill attempt. `--probe <vendor>` runs ONE live query
through that vendor instead, the way the install's setup checks a
freshly entered key: metered (one credit on a prepaid vendor), and
the states are the ones an operator acts on differently (rejected
means the key, unreachable means the network)."""

from __future__ import annotations

import httpx
from django.core.management.base import BaseCommand, CommandError

from ...constants import SearchStatus
from ...tools import registry as tool_registry
from ...tools.search.machinery import SearchToolSpec, serving_provider
from ...tools.search.providers import registry as provider_registry
from ...tools.search.providers.base import AttemptThrottled, AttemptUnreachable
from ...tools.search.providers.registry import provider_status

_CONFIG = "config/tools.toml"
# Any query costs the same one credit; this one is short and names
# the caller in the vendor's logs.
_PROBE_QUERY = "openbower"


def _readiness(vendor: str) -> str:
    if provider_status(vendor) is SearchStatus.OPEN:
        return "ready"
    return f"needs setup in {_CONFIG}"


class Command(BaseCommand):
    help = f"Show each tool's wired vendor and status, plus the supported alternatives ({_CONFIG})."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--probe",
            metavar="VENDOR",
            help="Run one live query through this vendor to check its credentials (metered: one credit).",
        )

    def handle(self, *args, **options) -> None:
        if options["probe"]:
            self._probe(options["probe"])
            return
        for tool in tool_registry.all_tools():
            if not isinstance(tool, SearchToolSpec):
                continue
            serving = serving_provider(tool.name, tool.vendors)
            roster = ", ".join(f"{vendor} ({_readiness(vendor)})" for vendor in tool.vendors)
            self.stdout.write(f"{tool.name}: {serving} ({_readiness(serving)}) | supports: {roster}")

    def _probe(self, vendor: str) -> None:
        try:
            spec = provider_registry.get(vendor)
        except KeyError:
            names = ", ".join(p.name for p in provider_registry.all_providers())
            raise CommandError(f"unknown vendor {vendor!r} (registered: {names})") from None
        if provider_status(vendor) is not SearchStatus.OPEN:
            raise CommandError(f"{vendor} {_readiness(vendor)}")
        try:
            hits = spec.run(_PROBE_QUERY, 1)
        except ValueError as e:
            # The vendor DECIDED against the call (a 403 for a bad key,
            # a 400 for a drained balance): the credentials, not the
            # network, are what to fix.
            self.stdout.write(f"{vendor}: rejected | {e}")
            raise CommandError(f"{vendor} rejected the credentials") from e
        except (AttemptThrottled, AttemptUnreachable, httpx.HTTPError) as e:
            self.stdout.write(f"{vendor}: unreachable | {type(e).__name__}: {e}")
            raise CommandError(f"{vendor} could not be reached") from e
        self.stdout.write(f"{vendor}: ok | {len(hits)} hit(s) from one query")
