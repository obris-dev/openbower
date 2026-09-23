"""The column kinds: one module per kind, each registering itself at
its own bottom. The roster is ListsConfig.ready()'s walk of this
package, so a new kind is a new file here, never an edit to the
registry. A column kind reacts to CELL-shaped changes (a person typed
a value); run-shaped changes over a node's columns are the node
processor's `on_run_landed`."""
