"""Prompt assembly: Django-template rendering in a SANDBOXED engine,
and the instruction tails. Prompts are user-authored, so the engine is
locked to what a prompt can safely mean (variety
like filters, defaults, and conditionals comes from the engine we
already ship, never from growing a private grammar):

- the context is the row's STRINGS only (nothing rich to traverse);
- underscore attributes are a parse error (Django blocks them);
- the engine is LOADERLESS, and {% include %}/{% extends %} refuse at
  validation (they have no filesystem to reach, and would otherwise
  parse clean and detonate per row at render);
- autoescape is OFF (prompts are text for a model, not HTML);
- a missing variable renders "" (blank over garbage).

Syntax validates at the serializer boundary (a bad prompt 400s at
save/test); render-time template errors propagate as the run's loud
failure. Instructions state CONDUCT only (tool use, URL honesty,
empty-when-unknown); output shape travels as the typed output_type,
never as prose."""

from __future__ import annotations

from django.template import Context, Engine
from django.template.base import VariableNode
from django.template.defaulttags import DebugNode
from django.template.exceptions import TemplateSyntaxError
from django.template.loader_tags import ExtendsNode, IncludeNode

from openbower_schema.agents import CONFIDENCE_REASON_SUFFIX, CONFIDENCE_SUFFIX

from ..constants import MAX_TOOL_CALLS

_ENGINE = Engine(dirs=[], app_dirs=False, autoescape=False, string_if_invalid="")


def _scoring_conduct(*, source: str, url_source: str) -> str:
    """The scoring conduct every instruction set shares, phrased over
    its own evidence noun. ONE author on purpose: the confidence pair
    and the floor's scale are the product's contract, and two
    hand-kept copies is how an edit lands on one and silently skips
    the other."""
    return (
        f" For every output you fill, use its {CONFIDENCE_REASON_SUFFIX} field BEFORE you score it:"
        f" explain what in {source} supports your answer, and what you could not confirm, inferred"
        " rather than read, or found ambiguous or out of date. Then state"
        f" your confidence in its {CONFIDENCE_SUFFIX} field, where 1 is certain and stated outright by"
        f" {source} and 0 is nothing supporting it at all."
        f" Use ONLY URLs that appear in {url_source}; never invent one."
        " EVERY field is required: answer every one, and never omit a field."
    )


AGENT_INSTRUCTIONS = (
    "Use the provided tools to gather evidence before answering."
    f" You have exactly {MAX_TOOL_CALLS} searches; a good answer from records already gathered BEATS"
    " another search, so once results cover the question, stop searching and answer."
    " If a tool answers that search is unavailable (rate limited), stop searching and leave every"
    " output empty; the row will be retried later."
    " Tool results are the best matches for your QUERY, not facts about your task: one may"
    " describe a different company, person, or time. Judging which ones concern your task is"
    " your job." + _scoring_conduct(source="the evidence", url_source="tool results") + " An empty string is for"
    " an output you found NOTHING for; anything you did find goes in with the score it earned."
)
# The verdict call after a run spent its whole tool budget still
# searching: no tools, the records it gathered rendered into the task,
# and the same scoring conduct, so the confidence floor judges what it
# did find instead of the budget deciding for it.
CAPPED_INSTRUCTIONS = (
    "Your search budget is spent. Answer ONLY from the records listed in the task; they are"
    " everything you gathered. Records are the best matches for your QUERIES, not facts about"
    " your task: one may describe a different company, person, or time. Judging which ones"
    " concern your task is your job."
    + _scoring_conduct(source="the records", url_source="the records")
    + " An empty string is for"
    " an output the records say NOTHING about; anything they do say goes in with the score it earned."
)
DIRECT_INSTRUCTIONS = (
    "Answer from your own knowledge."
    f" For every output you fill, use its {CONFIDENCE_REASON_SUFFIX} field BEFORE you score it:"
    " explain what you are relying on, and what you could not confirm or may be out of date."
    " Then state"
    f" your confidence in its {CONFIDENCE_SUFFIX} field, where 1 is certain and 0 is no basis at"
    " all."
    " EVERY field is required: answer every one, and never omit a field. An empty string is for"
    " an output you know NOTHING about; anything you do know goes in with the score it earned."
    " Never invent URLs."
)


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
    # include/extends parse clean but detonate at RENDER (there is no
    # template filesystem behind a prompt, so TemplateDoesNotExist
    # fires per row, past every config gate). Nothing legitimate is
    # refused: a prompt has no other templates to reach.
    if parsed.nodelist.get_nodes_by_type(IncludeNode) or parsed.nodelist.get_nodes_by_type(ExtendsNode):
        raise TemplateSyntaxError("include and extends tags are not available in prompts")


def prompt_variables(template: str) -> set[str]:
    """The ROOT variable names the prompt's {{tokens}} reference, read
    off the PARSED nodelist (the render grammar itself, so filters and
    attribute paths resolve to their root and a mirroring regex can
    never drift). Literals carry no name: a string token parses to a
    plain str and a number's lookups are None, so both fall out of the
    guard. Fill admission keys these against row columns to decide
    which rows the prompt can act on."""
    parsed = _ENGINE.from_string(template)
    return {
        node.filter_expression.var.lookups[0]
        for node in parsed.nodelist.get_nodes_by_type(VariableNode)
        if getattr(node.filter_expression.var, "lookups", None)
    }


def render_prompt(template: str, row_data: dict) -> str:
    """The prompt for one row: full Django-template semantics over the
    row's values ({{key}}, filters, {% if %}), inside the sandbox
    described above. The web extracts ROOT variables with a mirrored
    regex to drive the bench inputs and chips."""
    return _ENGINE.from_string(template).render(Context(row_data, autoescape=False))
