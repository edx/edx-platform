"""Tests for management command monitoring context manager and APM integration.

Test Coverage:
- Monitoring enabled/disabled states
- Datadog custom attribute collection
- APM trace wrapping
- Exception and SystemExit handling
- Configurable trace names
"""

from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import patch

from django.test import override_settings

from openedx.core.djangoapps.util.management_monitoring import monitor_django_management_command


class ManagementCommandMonitoringTests(TestCase):
    """Tests for management command monitoring helpers."""

    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    def test_monitoring_disabled(self, mock_set_custom_attribute, mock_set_transaction_name, mock_function_trace):
        """No monitoring calls should be made when disabled."""
        with override_settings(FEATURES={'ENABLE_MANAGEMENT_COMMAND_MONITORING': False}):
            with monitor_django_management_command("migrate", "lms"):
                pass

        mock_function_trace.assert_not_called()
        mock_set_transaction_name.assert_not_called()
        mock_set_custom_attribute.assert_not_called()

    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    def test_monitoring_enabled_success(
        self,
        mock_set_custom_attribute,
        mock_set_transaction_name,
        mock_function_trace,
    ):
        """Expected monitoring metadata is set during successful execution."""
        mock_function_trace.return_value = nullcontext()

        with override_settings(FEATURES={'ENABLE_MANAGEMENT_COMMAND_MONITORING': True}):
            with monitor_django_management_command("migrate", "lms"):
                pass

        mock_function_trace.assert_called_once_with("django.management.command")
        mock_set_transaction_name.assert_called_once_with("lms.management.migrate")
        mock_set_custom_attribute.assert_any_call("management_command.name", "migrate")
        mock_set_custom_attribute.assert_any_call("management_command.service_variant", "lms")
        mock_set_custom_attribute.assert_any_call("management_command.transaction_name", "lms.management.migrate")
        mock_set_custom_attribute.assert_any_call("management_command.status", "success")

        duration_calls = [
            call_args for call_args in mock_set_custom_attribute.call_args_list
            if call_args.args[0] == "management_command.duration_seconds"
        ]
        assert len(duration_calls) == 1
        assert isinstance(duration_calls[0].args[1], float)

    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    def test_custom_trace_name(self, mock_set_custom_attribute, mock_set_transaction_name, mock_function_trace):
        """Custom trace name should be honored when configured."""
        mock_function_trace.return_value = nullcontext()

        with override_settings(
            FEATURES={'ENABLE_MANAGEMENT_COMMAND_MONITORING': True},
            MANAGEMENT_COMMAND_MONITORING_TRACE_NAME="custom.management.trace",
        ):
            with monitor_django_management_command("shell", "cms"):
                pass

        mock_function_trace.assert_called_once_with("custom.management.trace")
        mock_set_transaction_name.assert_called_once_with("cms.management.shell")
        mock_set_custom_attribute.assert_any_call("management_command.status", "success")

    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    def test_monitoring_enabled_failure(
        self,
        mock_set_custom_attribute,
        mock_set_transaction_name,
        mock_function_trace,
    ):
        """Failure metadata should be set when command raises an exception."""
        mock_function_trace.return_value = nullcontext()

        with override_settings(FEATURES={'ENABLE_MANAGEMENT_COMMAND_MONITORING': True}):
            with self.assertRaises(ValueError):
                with monitor_django_management_command("migrate", "lms"):
                    raise ValueError("boom")

        mock_function_trace.assert_called_once_with("django.management.command")
        mock_set_transaction_name.assert_called_once_with("lms.management.migrate")
        mock_set_custom_attribute.assert_any_call("management_command.status", "failure")
        mock_set_custom_attribute.assert_any_call("management_command.exception_class", "ValueError")

    @patch("openedx.core.djangoapps.util.management_monitoring.function_trace")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_monitoring_transaction_name")
    @patch("openedx.core.djangoapps.util.management_monitoring.set_custom_attribute")
    def test_monitoring_enabled_system_exit_failure(
        self,
        mock_set_custom_attribute,
        mock_set_transaction_name,
        mock_function_trace,
    ):
        """SystemExit failures should include exit code custom attribute."""
        mock_function_trace.return_value = nullcontext()

        with override_settings(FEATURES={'ENABLE_MANAGEMENT_COMMAND_MONITORING': True}):
            with self.assertRaises(SystemExit):
                with monitor_django_management_command("migrate", "lms"):
                    raise SystemExit(2)

        mock_function_trace.assert_called_once_with("django.management.command")
        mock_set_transaction_name.assert_called_once_with("lms.management.migrate")
        mock_set_custom_attribute.assert_any_call("management_command.status", "failure")
        mock_set_custom_attribute.assert_any_call("management_command.exception_class", "SystemExit")
        mock_set_custom_attribute.assert_any_call("management_command.exit_code", 2)
