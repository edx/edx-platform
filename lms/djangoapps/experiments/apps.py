# lint-amnesty, pylint: disable=missing-module-docstring
from django.apps import AppConfig


class ExperimentsConfig(AppConfig):
    """
    Application Configuration for experiments.
    """
    name = 'lms.djangoapps.experiments'

    def ready(self):
        # Import signal handlers.
        from . import signals  # pylint: disable=unused-import, import-outside-toplevel
