from django.apps import AppConfig, apps
from django.utils.module_loading import module_has_submodule


class JobsConfig(AppConfig):
    name = "jobs"

    def ready(self) -> None:
        # The job-kind roster: every installed app may ship a `jobs`
        # package whose modules register their kinds at their own
        # bottom (register(<its class>)), so a kind lives beside the
        # domain it works on and this app never imports a domain.
        # Walking those packages IS the registration; adding a kind is
        # adding a file, never editing this method.
        import importlib

        from agents.registration import import_submodules

        for app in apps.get_app_configs():
            if app.name == self.name or not module_has_submodule(app.module, "jobs"):
                continue
            package = f"{app.name}.jobs"
            importlib.import_module(package)
            import_submodules(package)
