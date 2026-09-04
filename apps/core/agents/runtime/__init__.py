"""The cell runtime. DELIBERATELY inert: consumers import the module
that owns the name (`.answer` for CellAnswerer, `.deps` for CellDeps,
`.outcomes` for the tool records), the way the answer package and the
worker's harness already do. A re-export here would sit on every
submodule import and turn the tools package's imports of `.deps` into
a cycle through `.answer`."""
