"""The roster walker: the DIRECTORY is the roster. ready() points
this at a package and every module inside registers itself by being
imported (each registering module ends in register(...)), so adding
a provider or a tool is adding a file, never editing ready().

Deliberately loud: no exception handling, so a broken module fails
boot with its own traceback instead of vanishing from a catalog.
Walk order is alphabetical per directory (deterministic); nothing
semantic rides it, because every load-bearing ordering is a DECLARED
field on the spec it orders (a tool's blame_order), never an import
side effect."""

from __future__ import annotations

import importlib
import pkgutil


def import_submodules(package_name: str) -> None:
    """Import every non-underscore module under the package,
    recursively, in alphabetical order per directory."""
    package = importlib.import_module(package_name)
    for module in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        leaf = module.name.rsplit(".", 1)[-1]
        if leaf.startswith("_"):
            continue
        importlib.import_module(module.name)
