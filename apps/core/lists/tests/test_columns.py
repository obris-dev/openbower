"""The sheet's column writes, POST /v1/lists/{id}/columns (the
blank-column add) and PATCH /v1/lists/{id}/column-order (the reorder),
through real cookie auth (the IdP mocked at its httpx boundary via the shared login
helper); responses validate back through the contract models (the
parity idiom).

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_columns
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from agents.models import Agent
from agents.services import AgentService
from common.testing import TEST_IDENTITY, login_session
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.lists import AiColumn, ListSummary

from ..constants import MAX_LIST_COLUMNS, FillStatus, StoredCellState
from ..models import Fill, ListCellState, ListRow, Node
from ..services.columns import ColumnKeysNotUnique, ColumnOrderStale, ColumnService
from ..services.lists import ListService
from ..services.workflows import WorkflowService


def _config() -> AgentConfig:
    return AgentConfig(
        prompt="Find the contact for {{company}}",
        provider="openai_compatible",
        source="ollama",
        model="test-model",
        tools=AgentTools(),
        outputs=[
            AgentOutput(key="contact_name", label="Contact", type="text"),
            AgentOutput(key="contact_url", label="Profile", type="url"),
        ],
    )


class ColumnsViewTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.lists = ListService(account_id=TEST_IDENTITY["account_id"])
        self.sheet = self.lists.create(
            owner_id=TEST_IDENTITY["id"],
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )

    def post_column(self, list_id: str = "", **overrides):
        body: dict = {"label": "Contact Email", "type": "email"}
        body.update(overrides)
        return self.client.post(
            reverse("lists_columns", kwargs={"id": list_id or str(self.sheet.id)}),
            body,
            content_type="application/json",
        )

    def test_appends_the_blank_column_with_the_derived_key(self) -> None:
        resp = self.post_column()
        self.assertEqual(resp.status_code, 200, resp.content)
        wire = ListSummary(**resp.json())
        self.assertEqual(wire.id, str(self.sheet.id))
        added = wire.columns[-1]
        self.assertEqual(added.key, "contact_email")
        self.assertEqual(added.label, "Contact Email")
        self.assertEqual(added.type, "email")
        # Blank means blank: no fill member, ever. The add starts
        # nothing, and a column that exists refuses an AI column that
        # would land on the same key.
        self.assertEqual(added.kind, "plain")
        self.sheet.refresh_from_db()
        self.assertEqual([column.key for column in self.sheet.columns], ["company", "contact_email"])

    def test_duplicate_key_refuses_with_the_envelope(self) -> None:
        resp = self.post_column(label="Company!", type="text")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "column_exists", "detail": "A column named company already exists."})

    def test_reserved_and_empty_derived_keys_refuse(self) -> None:
        resp = self.post_column(label="Model Config", type="text")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "reserved_key")
        self.assertIn("reserved column key", body["detail"])
        self.assertEqual(self.post_column(label="!!!", type="text").json()["error"], "reserved_key")

    def test_column_cap_refuses(self) -> None:
        self.sheet.columns = [
            {"kind": "plain", "key": f"col_{n}", "label": f"Col {n}", "type": "text"} for n in range(MAX_LIST_COLUMNS)
        ]
        self.sheet.save(update_fields=["columns"])
        resp = self.post_column()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json(),
            {"error": "columns_full", "detail": f"A sheet holds at most {MAX_LIST_COLUMNS} columns."},
        )

    def test_foreign_list_reads_as_missing(self) -> None:
        foreign = ListService(account_id="01AC" + "Z" * 22).create(
            owner_id=TEST_IDENTITY["id"], label="Not yours", columns=[], origin="manual"
        )
        self.assertEqual(self.post_column(list_id=str(foreign.id)).status_code, 404)

    def test_blank_label_and_unknown_type_fail_validation(self) -> None:
        self.assertEqual(self.post_column(label="   ").status_code, 400)
        self.assertEqual(self.post_column(type="picture").status_code, 400)


class ColumnOrderTests(TestCase):
    """PATCH /v1/lists/{id}/column-order: the one columns write that
    may only decide WHERE a column sits."""

    def setUp(self) -> None:
        login_session(self.client)
        self.lists = ListService(account_id=TEST_IDENTITY["account_id"])
        self.sheet = self.lists.create(
            owner_id=TEST_IDENTITY["id"],
            label="Prospects",
            # Labels deliberately NOT derivable from their keys, and
            # one carrying a fill member: this is what proves the
            # column dicts are carried across rather than rebuilt.
            columns=[
                {"kind": "plain", "key": "company", "label": "Company Name", "type": "text"},
                {"key": "contact", "label": "Primary Contact", "type": "email", "kind": "ai", "node_id": "01NODE"},
                {"kind": "plain", "key": "notes", "label": "Free Notes", "type": "text"},
            ],
            origin="manual",
        )

    def reorder(self, keys, list_id: str = ""):
        return self.client.patch(
            reverse("lists_columns_order", kwargs={"id": list_id or str(self.sheet.id)}),
            {"keys": keys},
            content_type="application/json",
        )

    def test_reorders_and_echoes_the_new_order(self) -> None:
        resp = self.reorder(["notes", "company", "contact"])
        self.assertEqual(resp.status_code, 200, resp.content)
        wire = ListSummary(**resp.json())
        self.assertEqual([c.key for c in wire.columns], ["notes", "company", "contact"])
        self.sheet.refresh_from_db()
        self.assertEqual([c.key for c in self.sheet.columns], ["notes", "company", "contact"])

    def test_it_carries_each_column_across_verbatim(self) -> None:
        # The guard that keeps this from being a mutation path: the
        # request names keys and nothing else, so a label, a type, or a
        # fill member cannot be edited through an ordering request.
        before = {c.key: c for c in self.sheet.columns}
        self.assertEqual(self.reorder(["notes", "contact", "company"]).status_code, 200)
        self.sheet.refresh_from_db()
        self.assertEqual({c.key: c for c in self.sheet.columns}, before)

    def test_a_missing_key_refuses(self) -> None:
        resp = self.reorder(["company", "contact"])
        self.assertEqual(resp.status_code, 409, resp.content)
        # The WHOLE envelope, like the sibling refusal tests: `detail`
        # is tier-1 copy the client renders verbatim, so a rewrite of
        # it is a user-visible change and has to be deliberate.
        self.assertEqual(
            resp.json(),
            {
                "error": "column_order_stale",
                "detail": "This sheet's columns changed while you were reordering; try the move again.",
            },
        )

    def test_an_unknown_key_refuses(self) -> None:
        resp = self.reorder(["company", "contact", "invented"])
        self.assertEqual(resp.status_code, 409, resp.content)

    def test_a_duplicate_key_is_a_bad_request_not_a_conflict(self) -> None:
        # A repeat is the request being wrong, and no change to the
        # world makes it right, so it must not borrow the 409's "try
        # again" copy.
        resp = self.reorder(["company", "contact", "notes", "notes"])
        self.assertEqual(resp.status_code, 400, resp.content)
        self.sheet.refresh_from_db()
        self.assertEqual([c.key for c in self.sheet.columns], ["company", "contact", "notes"])

    def test_a_duplicate_that_also_drops_a_key_is_a_bad_request(self) -> None:
        resp = self.reorder(["company", "company", "contact"])
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_the_service_names_a_repeat_and_a_stale_set_differently(self) -> None:
        # One rule in one place: the wire and a direct caller get the
        # same verdict, and the two refusals stay distinct because they
        # owe the caller different answers.
        columns = ColumnService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        with self.assertRaises(ColumnKeysNotUnique):
            columns.reorder(str(self.sheet.id), keys=["company", "contact", "notes", "notes"])
        with self.assertRaises(ColumnKeysNotUnique):
            columns.reorder(str(self.sheet.id), keys=["company", "company", "contact"])
        with self.assertRaises(ColumnOrderStale):
            columns.reorder(str(self.sheet.id), keys=["company", "contact", "gone"])
        self.sheet.refresh_from_db()
        self.assertEqual([c.key for c in self.sheet.columns], ["company", "contact", "notes"])

    def test_a_key_no_column_could_have_is_a_bad_request(self) -> None:
        # The round-trip that was answering "try the move again" to a
        # request that could never succeed: a key outside the derived
        # shape names no column that can exist.
        for bad in ("Notes", "no tes", "../etc"):
            with self.subTest(key=bad):
                resp = self.reorder(["company", bad])
                self.assertEqual(resp.status_code, 400, resp.content)
        self.sheet.refresh_from_db()
        self.assertEqual([c.key for c in self.sheet.columns], ["company", "contact", "notes"])

    def test_a_foreign_sheet_reads_as_missing(self) -> None:
        other = ListService(account_id="01OTHERACCOUNTBBBBBBBBBBBB").create(
            owner_id=TEST_IDENTITY["id"],
            label="Theirs",
            columns=[{"kind": "plain", "key": "a", "label": "A", "type": "text"}],
            origin="manual",
        )
        self.assertEqual(self.reorder(["a"], list_id=str(other.id)).status_code, 404)


class ColumnDeleteTests(TestCase):
    """DELETE and PATCH /v1/lists/{id}/columns/{key}: the column's own
    life. Delete takes any column, not only an AI one."""

    def setUp(self) -> None:
        login_session(self.client)
        self.lists = ListService(account_id=TEST_IDENTITY["account_id"])
        self.agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        self.agent = self.agents.create_ephemeral(owner_id=TEST_IDENTITY["id"], label="Contact", config=_config())
        self.sheet = self.lists.create(
            owner_id=TEST_IDENTITY["id"],
            label="Prospects",
            columns=[
                {"kind": "plain", "key": "company", "label": "Company", "type": "text"},
                {"kind": "plain", "key": "contact_name", "label": "Contact", "type": "text"},
                {"kind": "plain", "key": "contact_url", "label": "Profile", "type": "url"},
            ],
            origin="manual",
        )
        # The node binds the agent to the sheet the way admission does;
        # both columns then point at it (one multi-output agent, one node).
        self.node = WorkflowService(account_id=TEST_IDENTITY["account_id"]).get_or_create_column_agent_node(
            self.sheet, agent_id=str(self.agent.id)
        )
        self.sheet.columns = [
            self.sheet.columns[0],
            *[
                AiColumn(key=column.key, label=column.label, type=column.type, node_id=str(self.node.id))
                for column in self.sheet.columns[1:]
            ],
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self.lists.add_rows(
            self.sheet,
            [
                {"company": "acme.com", "contact_name": "A Person", "contact_url": "https://x/1"},
                {"company": "example.io", "contact_name": "B Person", "contact_url": "https://x/2"},
            ],
        )
        for row in ListRow.objects.filter(list_id=str(self.sheet.id)):
            for key in ("contact_name", "contact_url"):
                ListCellState.objects.create(
                    account_id=TEST_IDENTITY["account_id"],
                    list_id=str(self.sheet.id),
                    row_id=str(row.id),
                    column_key=key,
                    state=StoredCellState.FILLED,
                )

    def url(self, key: str, list_id: str = "") -> str:
        return reverse("lists_column_detail", kwargs={"id": list_id or str(self.sheet.id), "key": key})

    def columns(self) -> list[str]:
        self.sheet.refresh_from_db()
        return [c.key for c in self.sheet.columns]

    def test_deleting_a_column_takes_its_values_out_of_every_row(self) -> None:
        resp = self.client.delete(self.url("contact_name"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.columns(), ["company", "contact_url"])
        for row in ListRow.objects.filter(list_id=str(self.sheet.id)):
            self.assertNotIn("contact_name", row.data)
            # The neighbours are untouched: the purge is one key, not
            # the row.
            self.assertIn("company", row.data)
            self.assertIn("contact_url", row.data)

    def test_it_purges_only_that_column_s_cell_states(self) -> None:
        self.client.delete(self.url("contact_name"))
        states = ListCellState.objects.filter(list_id=str(self.sheet.id))
        self.assertEqual(states.filter(column_key="contact_name").count(), 0)
        self.assertEqual(states.filter(column_key="contact_url").count(), 2)

    def test_a_PLAIN_column_deletes_too(self) -> None:
        resp = self.client.delete(self.url("company"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.columns(), ["contact_name", "contact_url"])
        for row in ListRow.objects.filter(list_id=str(self.sheet.id)):
            self.assertNotIn("company", row.data)

    def test_the_ephemeral_agent_survives_while_a_SIBLING_column_uses_it(self) -> None:
        # One multi-output agent made both columns; deleting the first
        # must not strand the second's config.
        self.client.delete(self.url("contact_name"))
        self.assertTrue(Agent.objects.filter(id=self.agent.id).exists())

    def test_the_ephemeral_agent_dies_with_the_LAST_column_that_used_it(self) -> None:
        self.client.delete(self.url("contact_name"))
        self.client.delete(self.url("contact_url"))
        self.assertFalse(Agent.objects.filter(id=self.agent.id).exists())

    def test_a_column_whose_node_is_gone_still_deletes(self) -> None:
        # Corruption (nodes die only with their list) must not make a
        # column undeletable: the tidy lands and the orphan agent is logged.
        Node.objects.filter(id=self.node.id).delete()
        self.assertEqual(self.client.delete(self.url("contact_name")).status_code, 200)
        with self.assertLogs("lists.services.columns", level="WARNING") as logs:
            resp = self.client.delete(self.url("contact_url"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.columns(), ["company"])
        self.assertIn(f"node {self.node.id} is gone", logs.output[0])
        self.assertTrue(Agent.objects.filter(id=self.agent.id).exists())

    def test_a_live_fill_touching_the_column_is_cancelled(self) -> None:
        fill = Fill.objects.create(
            account_id=TEST_IDENTITY["account_id"],
            list_id=str(self.sheet.id),
            agent_id=str(self.agent.id),
            column_keys=["contact_name", "contact_url"],
            status=FillStatus.RUNNING,
            confirmed_row_count=2,
        )
        self.client.delete(self.url("contact_name"))
        fill.refresh_from_db()
        # The sibling is stopped too rather than left writing into a
        # column that no longer exists.
        self.assertEqual(fill.status, FillStatus.CANCELLED)

    def test_an_unknown_key_is_not_found(self) -> None:
        self.assertEqual(self.client.delete(self.url("nope")).status_code, 404)

    def test_a_foreign_sheet_reads_as_missing(self) -> None:
        other = ListService(account_id="01OTHERACCOUNTBBBBBBBBBBBB").create(
            owner_id=TEST_IDENTITY["id"],
            label="Theirs",
            columns=[{"kind": "plain", "key": "a", "label": "A", "type": "text"}],
            origin="manual",
        )
        self.assertEqual(self.client.delete(self.url("a", list_id=str(other.id))).status_code, 404)

    def test_rename_changes_the_label_and_never_the_key(self) -> None:
        resp = self.client.patch(self.url("contact_name"), {"label": "Decision maker"}, content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.sheet.refresh_from_db()
        column = next(c for c in self.sheet.columns if c.key == "contact_name")
        self.assertEqual(column.label, "Decision maker")
        # The key stays, so the cells it holds stay reachable.
        self.assertEqual((column.kind, column.node_id), ("ai", str(self.node.id)))
        for row in ListRow.objects.filter(list_id=str(self.sheet.id)):
            self.assertIn("contact_name", row.data)

    def test_renaming_an_unknown_key_is_not_found(self) -> None:
        resp = self.client.patch(self.url("nope"), {"label": "X"}, content_type="application/json")
        self.assertEqual(resp.status_code, 404)

    def test_refilling_an_ORPHANED_column_answers_in_the_users_terms(self) -> None:
        # Deleting an agent leaves its columns orphaned ON PURPOSE, so
        # this is a normal state, not an internal error: it must not
        # 404 about an agent id the user never saw.
        from ..services.fill_admission import ColumnAgentMissing, FillAdmissionService

        Agent.objects.filter(id=self.agent.id).delete()
        admission = FillAdmissionService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        with self.assertRaises(ColumnAgentMissing) as caught:
            admission.refill(
                list_id=str(self.sheet.id),
                column_key="contact_name",
                rows=None,
                resume_fill_id="",
                confirmed_row_count=2,
            )
        self.assertIn("deleted", str(caught.exception))
        self.assertEqual(caught.exception.code, "column_agent_missing")

    def test_the_purge_pages_until_the_column_is_empty(self) -> None:
        # The fixtures are smaller than one page, so the loop only
        # runs once and its termination is never exercised. Shrink the
        # page instead of making 1,001 rows.
        from unittest.mock import patch

        from ..services import cell_truth

        extra = [
            ListCellState(
                account_id=TEST_IDENTITY["account_id"],
                list_id=str(self.sheet.id),
                row_id=f"01ROW{n:021d}",
                column_key="contact_name",
                state=StoredCellState.FILLED,
            )
            for n in range(7)
        ]
        ListCellState.objects.bulk_create(extra)
        total = ListCellState.objects.filter(list_id=str(self.sheet.id), column_key="contact_name").count()
        self.assertGreater(total, 2)

        with patch.object(cell_truth, "FILL_WRITE_BATCH", 2):
            cell_truth.purge_column(str(self.sheet.id), "contact_name")

        self.assertEqual(ListCellState.objects.filter(list_id=str(self.sheet.id), column_key="contact_name").count(), 0)
        # The neighbour is untouched: paging never widens the filter.
        self.assertEqual(ListCellState.objects.filter(list_id=str(self.sheet.id), column_key="contact_url").count(), 2)

    def test_the_LAST_column_can_go(self) -> None:
        # A sheet with no columns is a real state (every column
        # deleted), so it must not be refused or crash the summary the
        # response is built from.
        for key in ("company", "contact_name", "contact_url"):
            resp = self.client.delete(self.url(key))
            self.assertEqual(resp.status_code, 200, resp.content)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.columns, [])
        # The rows survive with their data emptied of every key.
        rows = list(ListRow.objects.filter(list_id=str(self.sheet.id)))
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.data for r in rows], [{}, {}])
        # And the sheet still reads back through the contract.
        read = self.client.get(reverse("lists_detail", kwargs={"id": str(self.sheet.id)}))
        self.assertEqual(read.status_code, 200, read.content)
        ListSummary.model_validate(read.json())
