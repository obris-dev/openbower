"""The cell runtime. DELIBERATELY inert: consumers import the module
that owns the name (`.cell` for run_cell, `.deps` for CellDeps), the
way the worker and admission already do. A re-export here would sit on
every submodule import and turn the tools package's imports of `.deps`
into a cycle through `.cell`."""
