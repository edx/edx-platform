"""
Tests for commerce toggles.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import ddt
from django.test import TestCase, override_settings
from edx_toggles.toggles.testutils import override_waffle_flag

from lms.djangoapps.commerce.toggles import (
    ENABLE_REFUND_POLICY_CHANGE,
    SHOW_REFUND_POLICY_CHANGE_NOTICE,
    get_refund_policy_effective_at,
    is_order_under_refund_policy_change,
    should_apply_refund_policy_change,
    should_show_refund_policy_change_notice,
)


class RefundPolicyChangeToggleTests(TestCase):
    """
    Tests for the commerce.enable_refund_policy_change waffle flag.
    """

    def test_disabled_by_default(self):
        assert not should_apply_refund_policy_change()

    @override_waffle_flag(ENABLE_REFUND_POLICY_CHANGE, active=True)
    def test_enabled(self):
        assert should_apply_refund_policy_change()


class RefundPolicyChangeNoticeToggleTests(TestCase):
    """
    Tests for the commerce.show_refund_policy_change_notice waffle flag.
    """

    def test_disabled_by_default(self):
        assert not should_show_refund_policy_change_notice()

    @override_waffle_flag(SHOW_REFUND_POLICY_CHANGE_NOTICE, active=True)
    def test_enabled(self):
        assert should_show_refund_policy_change_notice()

    @override_waffle_flag(SHOW_REFUND_POLICY_CHANGE_NOTICE, active=True)
    def test_independent_of_policy_flag(self):
        assert should_show_refund_policy_change_notice()
        assert not should_apply_refund_policy_change()


@ddt.ddt
class RefundPolicyEffectiveDateTests(TestCase):
    """
    Tests for the date check that decides which orders follow the refund policy change.
    """

    CUTOFF = '2026-12-10T00:00:00-05:00'  # Dec 10, 00:00 ET

    @ddt.data(
        (datetime(2026, 12, 9, 23, 59, 59, tzinfo=ZoneInfo('America/New_York')), False),
        (datetime(2026, 12, 10, 0, 0, 0, tzinfo=ZoneInfo('America/New_York')), True),
        (datetime(2026, 12, 10, 0, 0, 1, tzinfo=ZoneInfo('America/New_York')), True),
        (datetime(2026, 12, 10, 4, 59, 59, tzinfo=ZoneInfo('UTC')), False),
        (datetime(2026, 12, 10, 5, 0, 0, tzinfo=ZoneInfo('UTC')), True),
        (datetime(2026, 12, 10, 5, 0, 0), True),  # Naive datetimes are read as UTC.
        (datetime(2026, 12, 10, 4, 59, 59), False),
        (None, False),  # Unknown order date keeps the old behavior.
    )
    @ddt.unpack
    @override_waffle_flag(ENABLE_REFUND_POLICY_CHANGE, active=True)
    @override_settings(REFUND_POLICY_EFFECTIVE_AT=CUTOFF)
    def test_order_date_at_cutoff(self, placed_at, expected):
        assert is_order_under_refund_policy_change(placed_at) is expected

    @override_waffle_flag(ENABLE_REFUND_POLICY_CHANGE, active=False)
    @override_settings(REFUND_POLICY_EFFECTIVE_AT=CUTOFF)
    def test_flag_off_wins_over_date(self):
        assert not is_order_under_refund_policy_change(datetime(2027, 1, 1, tzinfo=ZoneInfo('UTC')))

    @override_settings(REFUND_POLICY_EFFECTIVE_AT='2026-12-10T00:00:00')
    def test_setting_without_offset_is_eastern_time(self):
        assert get_refund_policy_effective_at() == datetime(2026, 12, 10, 5, 0, tzinfo=ZoneInfo('UTC'))

    @ddt.data('not a date', None, 12345)
    @override_waffle_flag(ENABLE_REFUND_POLICY_CHANGE, active=True)
    def test_bad_setting_keeps_old_behavior(self, value):
        with override_settings(REFUND_POLICY_EFFECTIVE_AT=value):
            assert get_refund_policy_effective_at() is None
            assert not is_order_under_refund_policy_change(datetime(2030, 1, 1, tzinfo=ZoneInfo('UTC')))
