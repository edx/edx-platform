"""Monitoring utilities for Django management commands."""

import logging
import time
from contextlib import contextmanager

from django.conf import settings
from edx_django_utils.monitoring import function_trace, set_custom_attribute, set_monitoring_transaction_name
from openedx_filters import PipelineStep

log = logging.getLogger(__name__)


class ManagementCommandMonitoringPipelineStep(PipelineStep):
    """
    Pipeline step that wraps management command execution with APM monitoring.

    This step is invoked by the ManagementCommandExecutionRequested filter pipeline to wrap
    the command_runner callable with monitoring context. When executed, the original command
    runs within monitoring context that emits APM traces and custom Datadog attributes.
    """

    def run_filter(self, command_name, service_variant, command_runner):  # pylint: disable=arguments-differ
        """
        Decorate command runner with monitoring context.

        Arguments:
            command_name (str): Django management command being executed.
            service_variant (str): Service variant (lms, cms, etc.).
            command_runner (Callable): Executable that runs the management command.

        Returns:
            dict: Filter data with potentially wrapped command_runner.
        """
        if not callable(command_runner):
            return {
                "command_name": command_name,
                "service_variant": service_variant,
                "command_runner": command_runner,
            }

        def wrapped_runner() -> None:
            with monitor_django_management_command(command_name=command_name, service_variant=service_variant):
                command_runner()

        return {
            "command_name": command_name,
            "service_variant": service_variant,
            "command_runner": wrapped_runner,
        }


@contextmanager
def monitor_django_management_command(command_name, service_variant='unknown'):
    """
    Context manager that monitors Django management command execution.

    Emits Datadog APM traces and custom attributes tracking command execution,
    performance, and failure information. When monitoring is disabled via settings,
    this context manager is a no-op with minimal overhead.

    Custom Attributes Emitted:
        - management_command.name: Command name
        - management_command.service_variant: Service (lms/cms/etc.)
        - management_command.transaction_name: APM transaction name
        - management_command.status: success/failure
        - management_command.duration_seconds: Execution duration
        - management_command.exception_class: Exception type (on failure)
        - management_command.exit_code: Exit code (for SystemExit)

    Arguments:
        command_name (str): Django management command name.
        service_variant (str): Service variant (default: 'unknown').

    Yields:
        None

    Raises:
        Any exception raised by the command under monitoring context.
    """
    monitoring_enabled = getattr(settings, "ENABLE_MANAGEMENT_COMMAND_MONITORING", False)
    if not monitoring_enabled:
        yield
        return

    trace_name = getattr(settings, "MANAGEMENT_COMMAND_MONITORING_TRACE_NAME", "django.management.command")
    transaction_name = f"{service_variant}.management.{command_name}"

    set_monitoring_transaction_name(transaction_name)
    set_custom_attribute("management_command.name", command_name)
    set_custom_attribute("management_command.service_variant", service_variant)
    set_custom_attribute("management_command.transaction_name", transaction_name)

    start_time = time.monotonic()
    status = "failure"
    try:
        with function_trace(trace_name):
            log.info(
                "Management command started",
                extra={
                    "command": command_name,
                    "service_variant": service_variant,
                }
            )
            yield
            status = "success"
    except Exception as exc:
        set_custom_attribute("management_command.exception_class", exc.__class__.__name__)
        log.exception(
            "Management command failed",
            extra={
                "command": command_name,
                "service_variant": service_variant,
                "exception_class": exc.__class__.__name__,
            }
        )
        raise
    except (SystemExit, KeyboardInterrupt) as exc:
        set_custom_attribute("management_command.exception_class", exc.__class__.__name__)
        if isinstance(exc, SystemExit):
            set_custom_attribute("management_command.exit_code", exc.code)
        log.error(
            "Management command failed",
            extra={
                "command": command_name,
                "service_variant": service_variant,
                "exception_class": exc.__class__.__name__,
            }
        )
        raise
    finally:
        duration_seconds = time.monotonic() - start_time
        set_custom_attribute("management_command.status", status)
        set_custom_attribute("management_command.duration_seconds", duration_seconds)
        log.info(
            "Management command completed",
            extra={
                "command": command_name,
                "service_variant": service_variant,
                "status": status,
                "duration_seconds": duration_seconds,
            }
        )
