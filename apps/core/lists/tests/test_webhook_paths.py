"""The pure hop between wait KEYS and inbound PATH ids.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from django.test import SimpleTestCase

from ..fields import parse_columns
from ..services.webhook_paths import inbound_paths_for, wait_keys_for

NODE_A = "01NDA" + "A" * 21
NODE_B = "01NDB" + "B" * 21
PATH_A = "01PTA" + "A" * 21
PATH_B = "01PTB" + "B" * 21
COLUMNS = parse_columns(
    [
        {"kind": "plain", "key": "company", "label": "Company", "type": "text"},
        {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": NODE_A},
        {"key": "score", "label": "Score", "type": "text", "kind": "ai", "node_id": NODE_A},
        {"key": "country", "label": "Country", "type": "text", "kind": "ai", "node_id": NODE_B},
    ]
)
PATH_BY_NODE = {NODE_A: PATH_A, NODE_B: PATH_B}
NODE_BY_PATH = {PATH_A: NODE_A, PATH_B: NODE_B}


class InboundPathsTests(SimpleTestCase):
    def test_keys_map_to_their_nodes_paths_deduplicated_in_first_seen_order(self):
        # answer and score share a node (one agent, two outputs): one path.
        self.assertEqual(
            inbound_paths_for(["country", "answer", "score"], columns=COLUMNS, path_by_node=PATH_BY_NODE),
            [PATH_B, PATH_A],
        )

    def test_a_plain_or_unknown_key_contributes_no_path(self):
        self.assertEqual(inbound_paths_for(["company", "nope"], columns=COLUMNS, path_by_node=PATH_BY_NODE), [])


class WaitKeysTests(SimpleTestCase):
    def test_paths_resolve_to_every_column_their_node_fills_in_sheet_order(self):
        self.assertEqual(
            wait_keys_for([PATH_B, PATH_A], columns=COLUMNS, node_by_path=NODE_BY_PATH), ["answer", "score", "country"]
        )

    def test_a_path_with_no_node_on_this_sheet_resolves_to_nothing(self):
        self.assertEqual(wait_keys_for(["01GONE" + "Z" * 20], columns=COLUMNS, node_by_path=NODE_BY_PATH), [])
