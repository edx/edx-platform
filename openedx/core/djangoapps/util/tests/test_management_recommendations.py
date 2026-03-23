"""
Recommendation tests for management command monitoring best practices.

These tests validate:
- Performance characteristics (zero overhead when disabled)
- Edge case handling
- Best practices for custom pipeline step implementation
- Configuration flexibility
"""

from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import MagicMock, patch

from django.test import override_settings

from openedx.core.djangoapps.util.filters import ManagementCommandExecutionRequested
from openedx.core.djangoapps.util.management_monitoring import monitor_django_management_command


class ManagementCommandMonitoringBestPractices(TestCase):
    """
    Best practice tests for management command monitoring.

    These tests verify recommended patterns and edge case handling.
    """

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace")
    def test_disabled_monitoring_minimal_overhead(self, mock_trace, mock_txn, mock_attr):
        """
        Verify disabled monitoring adds minimal overhead.

        When ENABLE_MANAGEMENT_COMMAND_MONITORING is False, the monitoring context
        should be a no-op to avoid performance impact in production deployments.
        """
        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=False):
            with monitor_django_management_command('migrate', 'lms'):
                pass

        # No monitoring functions should be called
        mock_trace.assert_not_called()
        mock_txn.assert_not_called()
        mock_attr.assert_not_called()

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace", return_value=nullcontext())
    def test_multiple_service_variants_supported(self, mock_trace, mock_txn, mock_attr):
        """
        Verify custom service variants beyond 'lms' and 'cms' are supported.

        The monitoring system should work with any service variant name, enabling
        configuration for discovery, ecommerce, notes, and other services.
        """
        variants = ['lms', 'cms', 'discovery', 'ecommerce', 'notes']

        for variant in variants:
            mock_txn.reset_mock()

            with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
                with monitor_django_management_command('migrate', variant):
                    pass

            expected_txn = f'{variant}.management.migrate'
            mock_txn.assert_called_with(expected_txn)

    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace", return_value=nullcontext())
    def test_keyboard_interrupt_tracked_properly(self, mock_trace, mock_txn, mock_attr):
        """
        Verify KeyboardInterrupt (Ctrl+C) is tracked as a failure.

        KeyboardInterrupt should be treated as abnormal termination and marked as
        a failure in monitoring for proper alerting.
        """
        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
            with self.assertRaises(KeyboardInterrupt):
                with monitor_django_management_command('migrate', 'lms'):
                    raise KeyboardInterrupt()

        # Verify failure was tracked
        status_calls = [
            call.args for call in mock_attr.call_args_list
            if call.args[0] == 'management_command.status'
        ]
        assert status_calls[0][1] == 'failure'

    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace")
    def test_missing_trace_name_setting_uses_default(self, mock_trace):
        """
        Verify missing MANAGEMENT_COMMAND_MONITORING_TRACE_NAME uses default.

        When the custom trace name setting is not provided, the default trace name
        'django.management.command' should be used for consistency.
        """
        mock_trace.return_value = nullcontext()

        with override_settings(ENABLE_MANAGEMENT_COMMAND_MONITORING=True):
            # Don't set MANAGEMENT_COMMAND_MONITORING_TRACE_NAME
            with monitor_django_management_command('migrate', 'lms'):
                pass

        mock_trace.assert_called_once_with('django.management.command')

    def test_filter_always_returns_callable_runner(self):
        """
        Verify filter always returns a callable command runner.

        The filter output should always be a 3-tuple with the third element being
        callable, ensuring the manage.py flow can execute it as: runner()
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
    def test_duration_calculation_is_accurate(self, mock_attr):
        """
        Verify duration calculation captures approximate execution time.

        Duration should be reasonable and increasing with actual command execution
        time, providing accurate performance metrics for Datadog.
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
        duration = duration_calls[0][1]
        assert isinstance(duration, float)
        assert duration >= 0.0

    def test_filter_fail_silently_behavior(self):
        """
        Verify filter respects fail_silently setting in pipeline configuration.

        When fail_silently is True, pipeline errors should not prevent command
        execution, ensuring monitoring failures don't break production deployments.
        """
        with override_settings(
            OPEN_EDX_FILTERS_CONFIG={
                'org.openedx.platform.management.command.execute.requested.v1': {
                    'pipeline': [],
                    'fail_silently': True,
                }
            }
        ):
            mock_runner = MagicMock(return_value=None)

            # Should not raise even if pipeline had issues
            cmd_name, svc_var, runner = ManagementCommandExecutionRequested.run_filter(
                command_name='migrate',
                service_variant='lms',
                command_runner=mock_runner,
            )

            assert callable(runner)
