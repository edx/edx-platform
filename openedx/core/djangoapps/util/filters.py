"""Open edX filters used by util app infrastructure code."""

from typing import Callable

from openedx_filters.tooling import OpenEdxPublicFilter


class ManagementCommandExecutionRequested(OpenEdxPublicFilter):
    """
    Filter used to wrap Django management command execution.

    Filter Type:
        org.openedx.platform.management.command.execute.requested.v1

    Trigger:
        - Repository: openedx/edx-platform
        - Path: manage.py
        - Function or Method: __main__ block
    """

    filter_type = "org.openedx.platform.management.command.execute.requested.v1"

    @classmethod
    def run_filter(
        cls,
        command_name: str,
        service_variant: str,
        command_runner: Callable,
    ) -> tuple[str | None, str | None, Callable | None]:
        """
        Run the management command execution filter pipeline.

        This filter allows pipeline steps to intercept and decorate management command
        execution. The default pipeline includes a monitoring step that wraps command
        execution with APM tracing and Datadog custom attributes.

        Pipeline steps can modify or replace any of the filter arguments to customize
        command execution behavior, perform additional monitoring, logging, or security checks.

        Arguments:
            command_name (str): Parsed Django management command name (e.g., 'migrate', 'shell').
            service_variant (str): Service variant running the command ('lms', 'cms', etc.).
            command_runner (Callable): Zero-argument callable that executes the command.

        Returns:
            tuple[str | None, str | None, Callable | None]: Three-element tuple containing:
                - command_name: Command name, possibly modified by pipeline steps.
                - service_variant: Service variant, possibly modified by pipeline steps.
                - command_runner: Command executor callable, possibly wrapped/replaced by pipeline steps.

        Example:
            To add custom monitoring or security checks, implement a pipeline step:

            class MyCustomPipelineStep(PipelineStep):
                def run_filter(self, command_name, service_variant, command_runner):
                    # Custom logic here
                    return command_name, service_variant, command_runner
        """
        data = super().run_pipeline(
            command_name=command_name,
            service_variant=service_variant,
            command_runner=command_runner,
        )
        return data.get("command_name"), data.get("service_variant"), data.get("command_runner")
