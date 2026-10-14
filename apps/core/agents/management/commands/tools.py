"""The operator's tool matrix: one line per vendor-served tool, the
wired vendor and its readiness, then the whole supported roster the
same way, so "did my config/tools.toml edit take" is one command
instead of a fill attempt. Two vendor questions answer by exit code
(management/probe.py) for the install's setup step: `--status
<vendor>`, is it configured; `--probe <vendor>`, does ONE live query
go through (metered: one credit on a prepaid vendor). The probe's
states are the ones an operator acts on differently: rejected means
the credentials, unreachable means the network, throttled means wait."""

from __future__ import annotations

import httpx
from django.core.management.base import BaseCommand, CommandError

from ...constants import SearchStatus
from ...tools import registry as tool_registry
from ...tools.search.machinery import SearchToolSpec, serving_provider
from ...tools.search.providers import registry as provider_registry
from ...tools.search.providers.base import AttemptThrottled, AttemptUnreachable
from ...tools.search.providers.registry import provider_status
from ..probe import ProbeExit, reason

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
        question = parser.add_mutually_exclusive_group()
        question.add_argument(
            "--status",
            metavar="VENDOR",
            help=f"Exit 0 when this vendor is configured, {ProbeExit.NOT_CONFIGURED.value} when not.",
        )
        question.add_argument(
            "--probe",
            metavar="VENDOR",
            help="Run one live query through this vendor to check its credentials (metered: one credit).",
        )

    def handle(self, *args, **options) -> None:
        if options["status"] is not None:
            self._status(options["status"])
            return
        if options["probe"] is not None:
            self._probe(options["probe"])
            return
        for tool in tool_registry.all_tools():
            if not isinstance(tool, SearchToolSpec):
                continue
            serving = serving_provider(tool.name, tool.vendors)
            roster = ", ".join(f"{vendor} ({_readiness(vendor)})" for vendor in tool.vendors)
            self.stdout.write(f"{tool.name}: {serving} ({_readiness(serving)}) | supports: {roster}")

    def _spec(self, vendor: str):
        try:
            return provider_registry.get(vendor)
        except KeyError:
            names = ", ".join(p.name for p in provider_registry.all_providers())
            raise CommandError(f"unknown vendor {vendor!r} (registered: {names})") from None

    def _status(self, vendor: str) -> None:
        self._spec(vendor)
        self.stdout.write(f"{vendor}: {_readiness(vendor)}")
        if provider_status(vendor) is not SearchStatus.OPEN:
            raise CommandError(f"{vendor} {_readiness(vendor)}", returncode=ProbeExit.NOT_CONFIGURED)

    def _probe(self, vendor: str) -> None:
        spec = self._spec(vendor)
        if provider_status(vendor) is not SearchStatus.OPEN:
            raise CommandError(f"{vendor} {_readiness(vendor)}", returncode=ProbeExit.NOT_CONFIGURED)
        try:
            hits = spec.run(_PROBE_QUERY, 1)
        except AttemptThrottled as e:
            # 429 or 5xx: the vendor is there and said not now.
            self.stdout.write(f"{vendor}: throttled | {reason(e)}")
            raise CommandError(f"{vendor} is throttling or down", returncode=ProbeExit.THROTTLED) from e
        except (AttemptUnreachable, httpx.TransportError) as e:
            self.stdout.write(f"{vendor}: unreachable | {reason(e)}")
            raise CommandError(f"{vendor} could not be reached", returncode=ProbeExit.UNREACHABLE) from e
        except ValueError as e:
            # The vendor DECIDED against the call (a 403 for a bad key,
            # a 400 for a drained balance): the credentials or the
            # account, not the network.
            self.stdout.write(f"{vendor}: rejected | {reason(e)}")
            raise CommandError(f"{vendor} refused the query", returncode=ProbeExit.REJECTED) from e
        self.stdout.write(f"{vendor}: ok | {len(hits)} hit(s) from one query")
