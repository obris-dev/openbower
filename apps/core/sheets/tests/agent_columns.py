"""A sheet whose AI column is an entry action, so an arriving row that holds
something is offered to it: what a test of arrivals needs, on either kind.

The column's agent is a real ephemeral agent behind a real column-agent node
(the workflow service mints the node and its entry marker), so the trigger
reaches it the way production does.
"""

from __future__ import annotations

from agents.services import AgentService
from common.testing import TEST_IDENTITY
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.sheets import PlainColumn
from sheets.constants import SheetKind, SheetOrigin
from sheets.models import Sheet
from sheets.services.sheets import SheetService
from sheets.services.workflows import WorkflowService

ACCOUNT = TEST_IDENTITY["account_id"]


def sheet_with_an_ai_column(*, kind: SheetKind = SheetKind.STANDARD) -> Sheet:
    """A `company` column (immutable on a log, as a log's base columns are)
    and an `answer` AI column reading it."""
    sheets = SheetService(account_id=ACCOUNT)
    target = sheets.create(
        owner_id=TEST_IDENTITY["id"],
        label="Funding log" if kind == SheetKind.LOG else "Accounts",
        columns=[PlainColumn(key="company", label="Company", type="text", immutable=kind == SheetKind.LOG)],
        origin=SheetOrigin.MANUAL,
        kind=kind,
    )
    config = AgentConfig(
        prompt="Find the answer for {{company}}",
        provider="openai_compatible",
        source="ollama",
        model="scripted",
        tools=AgentTools(),
        outputs=[AgentOutput(key="answer", label="Answer", type="text")],
    )
    agent = AgentService(account_id=ACCOUNT).create_ephemeral(
        owner_id=TEST_IDENTITY["id"], label="Answer", config=config
    )
    node = WorkflowService(account_id=ACCOUNT).get_or_create_column_agent_node(target, agent_id=str(agent.id))
    target.columns = [
        *target.columns,
        {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": str(node.id)},
    ]
    target.save(update_fields=["columns", "updated_at"])
    return target
