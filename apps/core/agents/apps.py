from django.apps import AppConfig


class AgentsConfig(AppConfig):
    name = "agents"

    def ready(self) -> None:
        # The rosters: the DIRECTORY is the roster. Walking a package
        # imports every module in it, and importing a registering
        # module IS its registration (each ends in register(...)), so
        # adding a provider or a tool is adding a file here, never
        # editing this method. ready() is the one point Django
        # promises runs after every app module is importable, so a
        # registering module may import anything without re-entering
        # a half-initialized package. Every load-bearing ordering is
        # a DECLARED field on its spec (a tool's blame_order), never
        # the walk order.
        from . import registration

        registration.import_submodules("agents.providers")
        # The toml parser takes any section as written, so a typo'd
        # providers.toml section refuses HERE, at boot, where the
        # roster is known; the wire Literal is held to the roster the
        # same way.
        from .providers.registry import validate_inference_sources

        validate_inference_sources()
        # One walk covers the tool families AND the search providers
        # nested inside them (tools/search/providers registers the
        # same way).
        registration.import_submodules("agents.tools")
