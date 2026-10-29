"""
Tests for commerce toggles.
"""
from django.test import TestCase
from edx_toggles.toggles.testutils import override_waffle_flag

from lms.djangoapps.commerce.toggles import ENABLE_REFUND_POLICY_CHANGE, should_apply_refund_policy_change


class RefundPolicyChangeToggleTests(TestCase):
    """
    Tests for the commerce.enable_refund_policy_change waffle flag.
    """

    def test_disabled_by_default(self):
        assert not should_apply_refund_policy_change()

    @override_waffle_flag(ENABLE_REFUND_POLICY_CHANGE, active=True)
    def test_enabled(self):
        assert should_apply_refund_policy_change()

    @override_waffle_flag(ENABLE_REFUND_POLICY_CHANGE, active=False)
    def test_explicitly_disabled(self):
        assert not should_apply_refund_policy_change()
