"""The operator's tool matrix: one line per vendor-served tool, the
wired vendor and its readiness, then the whole supported roster the
same way, so "did my config/tools.toml edit take" is one command
instead of a fill attempt."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from ...constants import SearchStatus
from ...tools import registry as tool_registry
from ...tools.search.machinery import SearchToolSpec, serving_provider
from ...tools.search.providers.registry import provider_status


def _readiness(vendor: str) -> str:
    if provider_status(vendor) is SearchStatus.OPEN:
        return "ready"
    return "needs setup in config/tools.toml"


class Command(BaseCommand):
    help = "Show each tool's wired vendor and status, plus the supported alternatives (config/tools.toml)."

    def handle(self, *args, **options) -> None:
        for tool in tool_registry.all_tools():
            if not isinstance(tool, SearchToolSpec):
                continue
            serving = serving_provider(tool.name, tool.vendors)
            roster = ", ".join(f"{vendor} ({_readiness(vendor)})" for vendor in tool.vendors)
            self.stdout.write(f"{tool.name}: {serving} ({_readiness(serving)}) | supports: {roster}")
