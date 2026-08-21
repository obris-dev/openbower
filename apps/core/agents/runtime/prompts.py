"""Prompt assembly: Django-template rendering in a SANDBOXED engine,
and the instruction tails. Prompts are user-authored, so the engine is
locked to what a prompt can safely mean (variety
like filters, defaults, and conditionals comes from the engine we
already ship, never from growing a private grammar):

- the context is the row's STRINGS only (nothing rich to traverse);
- underscore attributes are a parse error (Django blocks them);
- the engine is LOADERLESS ({% include %}/{% extends %} have no
  filesystem to reach; they fail at render as a config-tier error);
- autoescape is OFF (prompts are text for a model, not HTML);
- a missing variable renders "" (blank over garbage).

Syntax validates at the serializer boundary (a bad prompt 400s at
save/test); render-time template errors propagate as the run's loud
failure. Instructions state CONDUCT only (tool use, URL honesty,
empty-when-unknown); output shape travels as the typed output_type,
never as prose."""

from __future__ import annotations

from django.template import Context, Engine
from django.template.defaulttags import DebugNode
from django.template.exceptions import TemplateSyntaxError

_ENGINE = Engine(dirs=[], app_dirs=False, autoescape=False, string_if_invalid="")

AGENT_INSTRUCTIONS = (
    "Use the provided tools to gather evidence before answering; call them as often as needed within reason."
    " Use ONLY URLs that appear in tool results; never invent one."
    " Use an empty string for anything unknown."
)
DIRECT_INSTRUCTIONS = "Use an empty string for anything unknown; never invent URLs."


def validate_prompt(template: str) -> None:
    """Raises django.template.TemplateSyntaxError on a prompt the
    engine cannot parse or that uses the debug tag ({% debug %} dumps
    the render context AND sys.modules into the output: an information
    leak into a model call, never a feature). The check walks the
    PARSED nodelist: the tag ignores its arguments, so {% debug x %}
    renders the same dump while a source regex never sees it, and
    node-walking reaches tags nested inside blocks."""
    parsed = _ENGINE.from_string(template)
    if parsed.nodelist.get_nodes_by_type(DebugNode):
        raise TemplateSyntaxError("the debug tag is not available in prompts")


def render_prompt(template: str, row_data: dict) -> str:
    """The prompt for one row: full Django-template semantics over the
    row's values ({{key}}, filters, {% if %}), inside the sandbox
    described above. The web extracts ROOT variables with a mirrored
    regex to drive the bench inputs and chips."""
    return _ENGINE.from_string(template).render(Context(row_data, autoescape=False))
