from django.apps import AppConfig


class ListsConfig(AppConfig):
    name = "lists"

    def ready(self) -> None:
        # The node-kind roster: the DIRECTORY is the roster. Walking
        # lists.nodes imports every kind module, and importing a
        # registering module IS its registration (each ends in
        # register(<its class>)), so adding a kind is adding a file there, never
        # editing this method. The walker is the agents app's (lists
        # already depends on agents); ready() is the one point Django
        # promises runs after every app module is importable.
        from agents.registration import import_submodules

        import_submodules("lists.nodes")
        # The processors are the same roster shape: one module per
        # kind, registering at its bottom.
        import_submodules("lists.processors")
        # The services write one kind by name, so a roster without it
        # refuses HERE, at boot, where the roster is known.
        from .nodes.registry import validate_node_kinds

        validate_node_kinds()
