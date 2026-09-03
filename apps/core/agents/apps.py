from django.apps import AppConfig


class AgentsConfig(AppConfig):
    name = "agents"

    def ready(self) -> None:
        # The provider ROSTER: importing a provider module IS its
        # registration (each ends in register(SPEC)). Providers first,
        # then tools; among providers no order is load-bearing (the
        # settings switch names exactly ONE to serve).
        from .tools.search.providers import dataforseo, duckduckgo  # noqa: F401  (the imports register)

        # The tool ROSTER: importing a tool module IS its registration
        # (each ends in register(SPEC)), and ready() is the one point
        # Django promises runs after every app module is importable,
        # so a tool module may import anything without re-entering a
        # half-initialized package. In BLAME order, not alphabetical:
        # a blank cell's cause is named by the FIRST toggled tool that
        # closed unserved, and registration order is that order.
        # isort: off
        from .tools.search import web_search  # noqa: F401  (the import registers)
        from .tools.search import find_contacts  # noqa: F401

        # isort: on
