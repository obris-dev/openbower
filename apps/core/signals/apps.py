from django.apps import AppConfig


class SignalsConfig(AppConfig):
    name = "signals"

    def ready(self) -> None:
        # The source-kind roster: the directory is the roster, the node
        # kinds' shape (sheets.apps).
        from agents.registration import import_submodules

        import_submodules("signals.sources")
        # The services write a kind by name, so a roster without it refuses
        # here, at boot.
        from .sources.registry import validate_source_kinds

        validate_source_kinds()
