"""The tool registry: name -> ToolSpec, with ONE guarded write path.

`register()` is how every tool enters, built-ins included: each tool
MODULE registers itself at its own bottom (`register(SPEC)`), and the
roster lives in AgentsConfig.ready(), so a tool file is
self-contained down to its registration.
Validation is all-or-nothing BEFORE the registry mutates: an
incomplete spec (a failure code with no failure mode, a vocabulary
missing the base) is a loud import-time error, never a quiet gap a
row discovers later. A name collision between two different specs
fails loud (silent last-wins would route calls to whichever import
ran last); re-registering the same spec is idempotent, so repeated
imports are harmless.

No ordering rides registration: the rosters are walked off the
directory (alphabetical, nothing semantic), and the one load-bearing
order, blame, is a DECLARED field on each spec (`blame_order`), so a
blank cell's cause is named by the lowest-ordered toggled tool that
closed unserved wherever the imports happened to run."""

from __future__ import annotations

from pydantic_ai import Tool

from openbower_schema.agents import AgentConfig

from ..constants import ToolStatus
from ..runtime.deps import CellDeps
from .base import TOOL_NAME_MAX_LENGTH, FailureMode, ToolError, ToolSpec
from .harness import wrap

_REGISTRY: dict[str, ToolSpec] = {}


def _blame_key(tool: ToolSpec) -> tuple[int, str]:
    return (tool.blame_order, tool.name)


class UnknownTool(LookupError):
    """A config toggles a tool this registry does not hold. Config
    tier: it fails every row identically, so it must fail loudly, never
    read as a quietly bad agent."""


def register(tool: ToolSpec) -> None:
    """Register one tool. Raises ValueError on an invalid spec, a
    name collision, or a blame_order collision; re-registering the
    same spec object is a no-op."""
    _validate(tool)
    existing = _REGISTRY.get(tool.name)
    if existing is not None:
        if existing is tool:
            return
        raise ValueError(f"tool name {tool.name!r} is already registered by another spec")
    # Blame must be a HUMAN'S ranking, never the alphabet's: the
    # ordering is scattered across the tool modules, so without this
    # refusal a new tool lands on a taken number and ties decide
    # blame silently. The message is the ranking's one computed view.
    taken = {t.blame_order: t.name for t in _REGISTRY.values()}
    if tool.blame_order in taken:
        ranking = ", ".join(f"{t.name}={t.blame_order}" for t in sorted(_REGISTRY.values(), key=_blame_key))
        raise ValueError(
            f"tool {tool.name!r} declares blame_order {tool.blame_order}, already held by "
            f"{taken[tool.blame_order]!r} (current ranking: {ranking})"
        )
    _REGISTRY[tool.name] = tool


def _validate(tool: ToolSpec) -> None:
    """The whole contract, checked before any mutation. Each check
    names the registration it refuses, because these errors surface at
    import time where a bare assertion would read as a framework bug."""
    # tool.name is the DERIVED name (the function's own, never
    # declared), so the shape check guards against functions with no
    # usable name: a lambda's "<lambda>" must never become a
    # registered tool.
    if not tool.name.isidentifier() or len(tool.name) > TOOL_NAME_MAX_LENGTH:
        raise ValueError(f"tool name {tool.name!r} must be a valid identifier of at most {TOOL_NAME_MAX_LENGTH} chars")
    if not callable(tool.availability) or not callable(tool.failure_copy):
        raise ValueError(f"tool {tool.name!r} must declare callable availability and failure_copy")
    # The declared ERROR CLASSES are the tool's failure vocabulary
    # (failure_modes derives from them); "open" is the one reserved
    # status and never a failure a class may claim.
    if not tool.errors:
        raise ValueError(f"tool {tool.name!r} must declare its failure vocabulary (errors is empty)")
    for error in tool.errors:
        if not (isinstance(error, type) and issubclass(error, ToolError)):
            raise ValueError(f"tool {tool.name!r} errors must be ToolError subclasses, got {error!r}")
        if not error.code or error.code == ToolStatus.OPEN.value:
            raise ValueError(f"tool {tool.name!r} error {error.__name__} must declare a non-open code")
        if not isinstance(getattr(error, "mode", None), FailureMode):
            raise ValueError(f"tool {tool.name!r} error {error.__name__} must declare a FailureMode mode")
    if not isinstance(tool.blame_order, int) or isinstance(tool.blame_order, bool):
        raise ValueError(f"tool {tool.name!r} must declare an integer blame_order")
    codes = [error.code for error in tool.errors]
    if len(set(codes)) != len(codes):
        raise ValueError(f"tool {tool.name!r} declares duplicate failure codes: {sorted(codes)}")
    # The harness folds an unexpected CRASH as the tool's own error
    # code, unconditionally; a tool that declared none would have its
    # crashes recorded nowhere and read as an honest decline.
    if ToolStatus.ERROR.value not in codes:
        raise ValueError(f"tool {tool.name!r} must declare an {ToolStatus.ERROR.value!r} failure (the crash fold)")
    # One code, one mode: the stored tools maps key on the code
    # strings, so two tools may share a code only by agreeing how it
    # behaves.
    for other in _REGISTRY.values():
        for code, mode in tool.failure_modes.items():
            if other.failure_modes.get(code, mode) != mode:
                raise ValueError(
                    f"tool {tool.name!r} maps status {code!r} to {mode}, but {other.name!r} maps it to"
                    f" {other.failure_modes[code]}"
                )


def get(name: str) -> ToolSpec:
    """Raises KeyError when the name has no registered tool."""
    return _REGISTRY[name]


def all_tools() -> list[ToolSpec]:
    """Every registered tool, in blame order (declared per spec,
    UNIQUE by the register guard; the name term only keeps sorts
    deterministic for registries assembled outside register(), as
    test patches are)."""
    return sorted(_REGISTRY.values(), key=_blame_key)


def toggled_tools(config: AgentConfig) -> list[ToolSpec]:
    """The tools this config asks for, in BLAME order (declared per
    spec, never the import order): a blank cell's cause is named by
    the first toggled tool in this list that closed unserved.

    A toggle naming NO registered tool raises UnknownTool rather than
    being skipped: the wire's closed AgentTools shape and this registry
    are pinned equal by test, so this cannot fire today, and that is
    exactly why it must be loud, not silent, if they ever drift."""
    toggled = {name for name, on in config.tools.model_dump().items() if on}
    unknown = toggled - set(_REGISTRY)
    if unknown:
        raise UnknownTool(f"config toggles unregistered tool(s) {sorted(unknown)}; registered: {sorted(_REGISTRY)}")
    return [tool for tool in all_tools() if tool.name in toggled]


def build_tools(config: AgentConfig, deps: CellDeps) -> list[Tool]:
    """What THIS config on THIS deploy may call: a toggled tool that
    is not available (per `deps.tool_status`, seeded from each tool's
    availability) is simply not offered; its status already says
    why."""
    return [Tool(wrap(tool)) for tool in toggled_tools(config) if deps.tool_open(tool.name, tool.closers)]


def validate_tool_config() -> None:
    """The tools-config boot gate, called from AgentsConfig.ready()
    AFTER the agents.tools walk (which registers the tools AND the
    search vendors), where every roster is known. The kernel parser
    takes names as written, so every naming mistake refuses HERE,
    naming the valid options. An ABSENT vendor table is not a mistake
    (the vendor gates honestly at runtime as not configured); a
    PRESENT table with wrong or empty keys is, because the operator
    plainly meant to configure it. This gate also carries the roster
    membership check spec construction cannot: a tool's vendors
    tuple is declared before the vendors register."""
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    from .search.machinery import SearchToolSpec
    from .search.providers.registry import all_providers

    vendors = {p.name: p for p in all_providers()}
    unknown = set(settings.TOOL_VENDOR_KEYS) - set(vendors)
    if unknown:
        raise ImproperlyConfigured(
            f"unknown vendor section(s) in the tools config: {', '.join(sorted(unknown))} "
            f"(registered vendors: {', '.join(sorted(vendors))})"
        )
    for name, spec in vendors.items():
        table = settings.TOOL_VENDOR_KEYS.get(name)
        if table is None:
            continue
        problems = []
        gap = sorted(key for key in spec.config_keys if not table.get(key))
        if gap:
            problems.append(f"missing or empty {', '.join(gap)}")
        extra = sorted(set(table) - set(spec.config_keys))
        if extra:
            problems.append(f"unknown {', '.join(extra)}")
        if problems:
            raise ImproperlyConfigured(
                f"the [{name}] table needs exactly: {', '.join(spec.config_keys)} ({'; '.join(problems)})"
            )
    rosters = {tool.name: tool.vendors for tool in _REGISTRY.values() if isinstance(tool, SearchToolSpec)}
    for tool_name, vendor in settings.TOOL_WIRING.items():
        roster = rosters.get(tool_name)
        if roster is None:
            raise ImproperlyConfigured(
                f"[tools] wires unknown tool {tool_name!r} (tools that take a vendor: {', '.join(sorted(rosters))})"
            )
        if vendor not in roster:
            raise ImproperlyConfigured(
                f"[tools] wires {tool_name} to {vendor!r}; {tool_name} supports: {', '.join(roster)}"
            )
    for tool_name, roster in rosters.items():
        unregistered = sorted(set(roster) - set(vendors))
        if unregistered:
            raise ImproperlyConfigured(
                f"{tool_name} declares unregistered vendor(s): {', '.join(unregistered)} "
                f"(registered vendors: {', '.join(sorted(vendors))})"
            )
