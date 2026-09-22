"""Between a webhook column's two vocabularies: the drawer speaks
column KEYS (what a user picks), the wait node stores inbound PATH ids
(what survives a column being renamed, reordered, or joined by a
sibling on its path). Pure, over the sheet's typed columns and a
node -> path mapping the caller read; refusals stay with the caller."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from openbower_schema.lists import AiColumn, ListColumn, WebhookColumn


def inbound_paths_for(
    wait_keys: list[str], *, columns: Sequence[ListColumn], path_by_node: Mapping[str, str]
) -> list[str]:
    """The paths the given AI columns' nodes sit on, first-seen order,
    deduplicated: two outputs of one agent share a node and a path, so
    waiting on both is waiting on one path."""
    node_by_key = {column.key: column.node_id for column in columns if isinstance(column, (AiColumn, WebhookColumn))}
    paths: list[str] = []
    for key in wait_keys:
        path_id = path_by_node.get(node_by_key.get(key, ""), "")
        if path_id and path_id not in paths:
            paths.append(path_id)
    return paths


def wait_keys_for(
    inbound_path_ids: list[str], *, columns: Sequence[ListColumn], node_by_path: Mapping[str, str]
) -> list[str]:
    """The columns ending the given paths, in SHEET order (the order a
    user sees), every column of a multi-output node included: waiting
    on a path is waiting on everything it fills. Any node column
    counts: an AI column done, or a Send webhook column sent."""
    wanted = {node_by_path[path_id] for path_id in inbound_path_ids if path_id in node_by_path}
    return [
        column.key for column in columns if isinstance(column, (AiColumn, WebhookColumn)) and column.node_id in wanted
    ]
