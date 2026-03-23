"""Tests for management command execution filter and pipeline integration.

Test Coverage:
- Filter functionality and pipeline traversal
- Pipeline step wrapping and decoration of command runners
- Settings-based pipeline configuration
"""

from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import override_settings
from openedx_filters import PipelineStep

from openedx.core.djangoapps.util.filters import ManagementCommandExecutionRequested
from openedx.core.djangoapps.util.management_monitoring import ManagementCommandMonitoringPipelineStep


class TestCommandRunnerDecoratorStep(PipelineStep):
    """Test pipeline step used to verify filter-based command decoration."""

    def run_filter(self, command_name, service_variant, command_runner):  # pylint: disable=arguments-differ
        def wrapped_runner():
            return command_runner()

        return {
            "command_name": command_name,
            "service_variant": service_variant,
            "command_runner": wrapped_runner,
        }


class ManagementCommandExecutionRequestedTests(TestCase):
    """Tests for util management command execution filter."""

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_run_filter_without_configuration(self):
        """Filter returns original values when no pipeline is configured."""
        command_runner = Mock()

        command_name, service_variant, returned_runner = ManagementCommandExecutionRequested.run_filter(
            command_name="migrate",
            service_variant="lms",
            command_runner=command_runner,
        )

        self.assertEqual(command_name, "migrate")
        self.assertEqual(service_variant, "lms")
        self.assertIs(returned_runner, command_runner)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.platform.management.command.execute.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.util.tests.test_management_command_filters.TestCommandRunnerDecoratorStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_run_filter_with_pipeline_step(self):
        """Filter applies configured pipeline step and returns wrapped runner."""
        command_runner = Mock()

        _, _, wrapped_runner = ManagementCommandExecutionRequested.run_filter(
            command_name="migrate",
            service_variant="lms",
            command_runner=command_runner,
        )

        self.assertTrue(callable(wrapped_runner))
        wrapped_runner()
        command_runner.assert_called_once_with()


class ManagementCommandMonitoringPipelineStepTests(TestCase):
    """Tests for the default monitoring pipeline step."""

    def setUp(self):
        super().setUp()
        self.step = ManagementCommandMonitoringPipelineStep(
            filter_type="org.openedx.platform.management.command.execute.requested.v1",
            running_pipeline=[
                "openedx.core.djangoapps.util.management_monitoring.ManagementCommandMonitoringPipelineStep",
            ],
        )

    @patch("openedx.core.djangoapps.util.management_monitoring.monitor_django_management_command")
    def test_step_wraps_callable_runner(self, mock_monitor_context):
        """Step decorates callable command runner with monitoring context."""
        mock_monitor_context.return_value = nullcontext()
        command_runner = Mock()

        result = self.step.run_filter(
            command_name="shell",
            service_variant="cms",
            command_runner=command_runner,
        )

        wrapped_runner = result.get("command_runner")
        self.assertTrue(callable(wrapped_runner))

        wrapped_runner()

        mock_monitor_context.assert_called_once_with(command_name="shell", service_variant="cms")
        command_runner.assert_called_once_with()

    def test_step_preserves_non_callable_runner(self):
        """Step preserves runner when provided command_runner is not callable."""
        result = self.step.run_filter(
            command_name="help",
            service_variant="lms",
            command_runner=None,
        )

        self.assertIsNone(result.get("command_runner"))
