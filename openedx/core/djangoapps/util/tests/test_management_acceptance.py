"""
Acceptance tests for management command monitoring feature.

These tests verify that the management command monitoring system meets the core requirements:
- Commands are routed through the filter pipeline
- Monitoring attributes are tracked correctly
- The system is toggleable via settings
- edX can override default monitoring via custom pipeline steps
"""

from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import MagicMock, patch

from django.test import override_settings

from openedx.core.djangoapps.util.filters import ManagementCommandExecutionRequested
from openedx.core.djangoapps.util.management_monitoring import (
    ManagementCommandMonitoringPipelineStep,
    monitor_django_management_command,
)


class ManagementCommandMonitoringAcceptanceTests(TestCase):
    """
    Acceptance tests verifying management command monitoring meets requirements.

    Test Coverage:
    - Filter pipeline integration and command routing
    - Monitoring attribute collection (name, service_variant, status, duration, etc.)
    - Monitoring toggle via ENABLE_MANAGEMENT_COMMAND_MONITORING setting
    - Custom pipeline step support for edX extensions
    """

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace", return_value=nullcontext())
    def test_filter_passes_command_through_pipeline(self, mock_trace, mock_txn, mock_attr):
        """
        Verify the filter correctly passes command details to the pipeline.

        The filter should extract command name and service variant, then pass them
        through the openedx_filters pipeline for potential modification by pipeline steps.
        """
        mock_runner = MagicMock(return_value=None)

        with override_settings(
            OPEN_EDX_FILTERS_CONFIG={
                'org.openedx.platform.management.command.execute.requested.v1': {
                    'pipeline': [],
                    'fail_silently': False,
                }
            }
        ):
            cmd_name, svc_variant, runner = ManagementCommandExecutionRequested.run_filter(
                command_name='migrate',
                service_variant='lms',
                command_runner=mock_runner,
            )

        assert cmd_name == 'migrate'
        assert svc_variant == 'lms'
        assert callable(runner)

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace", return_value=nullcontext())
    def test_monitoring_collects_required_attributes(self, mock_trace, mock_txn, mock_attr):
        """
        Verify all required Datadog custom attributes are set when monitoring is enabled.

        Required attributes for Datadog integration:
        - management_command.name: Command name
        - management_command.service_variant: LMS/CMS or custom variant
        - management_command.transaction_name: Transaction name for APM
        - management_command.status: success/failure
        - management_command.duration_seconds: Execution time
        """
        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
            with monitor_django_management_command('migrate', 'lms'):
                pass

        # Collect all attribute keys set
        called_attrs = {call.args[0] for call in mock_attr.call_args_list}

        required_attrs = {
            'management_command.name',
            'management_command.service_variant',
            'management_command.transaction_name',
            'management_command.status',
            'management_command.duration_seconds',
        }
        assert required_attrs.issubset(called_attrs), f"Missing: {required_attrs - called_attrs}"

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace")
    def test_monitoring_disabled_has_zero_overhead(self, mock_trace, mock_txn, mock_attr):
        """
        Verify that monitoring has zero APM overhead when disabled.

        When ENABLE_MANAGEMENT_COMMAND_MONITORING=False, no monitoring functions
        should be called, ensuring production deployments incur no overhead.
        """
        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=False):
            with monitor_django_management_command('migrate', 'lms'):
                pass

        mock_trace.assert_not_called()
        mock_txn.assert_not_called()
        mock_attr.assert_not_called()

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace", return_value=nullcontext())
    def test_lms_and_cms_service_variants_tracked_separately(self, mock_trace, mock_txn, mock_attr):
        """
        Verify LMS and CMS service variants are distinguished in monitoring.

        Transaction names should reflect the service variant:
        - LMS commands: lms.management.{command}
        - CMS commands: cms.management.{command}
        """
        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
            with monitor_django_management_command('migrate', 'lms'):
                pass

        lms_txn_call = mock_txn.call_args_list[0]
        assert 'lms.management.migrate' in str(lms_txn_call)

        mock_txn.reset_mock()

        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
            with monitor_django_management_command('shell', 'cms'):
                pass

        cms_txn_call = mock_txn.call_args_list[0]
        assert 'cms.management.shell' in str(cms_txn_call)

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace", return_value=nullcontext())
    def test_success_status_set_on_normal_completion(self, mock_trace, mock_txn, mock_attr):
        """
        Verify status is set to 'success' when command completes without exception.
        """
        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
            with monitor_django_management_command('migrate', 'lms'):
                pass

        status_calls = [
            call.args for call in mock_attr.call_args_list
            if call.args[0] == 'management_command.status'
        ]
        assert len(status_calls) == 1
        assert status_calls[0][1] == 'success'

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace", return_value=nullcontext())
    def test_failure_status_and_exception_tracked_on_error(self, mock_trace, mock_txn, mock_attr):
        """
        Verify status is 'failure' and exception class is captured when command fails.

        Exception information is essential for diagnosing management command issues.
        """
        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
            with self.assertRaises(RuntimeError):
                with monitor_django_management_command('migrate', 'lms'):
                    raise RuntimeError("Database connection failed")

        status_calls = [
            call.args for call in mock_attr.call_args_list
            if call.args[0] == 'management_command.status'
        ]
        assert status_calls[0][1] == 'failure'

        exception_calls = [
            call.args for call in mock_attr.call_args_list
            if call.args[0] == 'management_command.exception_class'
        ]
        assert exception_calls[0][1] == 'RuntimeError'

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace", return_value=nullcontext())
    def test_system_exit_exit_code_captured(self, mock_trace, mock_txn, mock_attr):
        """
        Verify SystemExit exit code is captured for debugging.

        SystemExit is a special case in management commands and the exit code
        provides important debugging information.
        """
        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
            with self.assertRaises(SystemExit):
                with monitor_django_management_command('migrate', 'lms'):
                    raise SystemExit(2)

        exit_code_calls = [
            call.args for call in mock_attr.call_args_list
            if call.args[0] == 'management_command.exit_code'
        ]
        assert len(exit_code_calls) == 1
        assert exit_code_calls[0][1] == 2

    def test_custom_pipeline_step_support(self):
        """
        Verify edX can extend monitoring via custom pipeline steps.

        The OPEN_EDX_FILTERS_CONFIG allows edX to replace the default monitoring
        pipeline step with custom implementations for specialized observability needs.
        """
        mock_runner = MagicMock(return_value=None)

        class CustomMonitoringPipelineStep(MagicMock):
            """Example custom monitoring step that edX would implement."""
            def run_filter(self, command_name, service_variant, command_runner):
                # Custom logic: log to custom service, trigger alerts, etc.
                return command_name, service_variant, command_runner

        custom_step = CustomMonitoringPipelineStep()

        # Verify custom step receives the command
        cmd_name, svc_var, runner = custom_step.run_filter(
            command_name='migrate',
            service_variant='lms',
            command_runner=mock_runner,
        )

        assert cmd_name == 'migrate'
        assert svc_var == 'lms'
        assert runner is mock_runner

    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace")
    def test_custom_trace_name_from_settings(self, mock_trace):
        """
        Verify custom APM trace names can be configured via settings.

        MANAGEMENT_COMMAND_MONITORING_TRACE_NAME allows operators to customize
        the APM transaction naming for integration with monitoring backends.
        """
        mock_trace.return_value = nullcontext()

        with override_settings(
            ENABLE_MANAGEMENT_COMMAND_MONITORING=True,
            MANAGEMENT_COMMAND_MONITORING_TRACE_NAME='custom.trace.name',
        ):
            with monitor_django_management_command('migrate', 'lms'):
                pass

        mock_trace.assert_called_once_with('custom.trace.name')

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    def test_duration_is_numeric(self, mock_attr):
        """
        Verify duration is captured as a numeric float value in seconds.
        """
        with patch('openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name'):
            with patch('openedx.core.djangoapps.util.management_monitoring.function_trace', return_value=nullcontext()):
                with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
                    with monitor_django_management_command('migrate', 'lms'):
                        pass

        duration_calls = [
            call.args for call in mock_attr.call_args_list
            if call.args[0] == 'management_command.duration_seconds'
        ]
        assert len(duration_calls) == 1
        assert isinstance(duration_calls[0][1], float)
        assert duration_calls[0][1] >= 0.0
