"""The cell layer: how a column's cells CHANGE, and the one shape every
change lands as.

Two axes meet here and stay apart. A NODE KIND reacts to run-shaped
changes over its whole column set (its run landed: the processor's
`on_run_landed`, one write per column it fills). A COLUMN KIND reacts
to cell-shaped changes over one cell (a person typed a value:
`kinds/`, one module per kind, a registry). A column's TYPE (text,
number, date) shapes a value at the write and lives in the contract's
cell_types, untouched by either.

Both emit `CellWrite`s (writes.py: one subclass per operation, a
shared resolution to the `StateWrite` the ledger takes): in-memory
intents, persisted by nothing until the list service's `land_row` /
`land_rows` lands the values onto the row and the resolved states
onto the cell ledger, in one lock order. The landing is kind-blind: it dispatches on
nothing, it writes what it is handed."""
