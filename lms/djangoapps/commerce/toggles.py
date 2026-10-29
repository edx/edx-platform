"""
Toggles for the commerce app.
"""
from edx_toggles.toggles import WaffleFlag

# Namespace for commerce waffle flags.
WAFFLE_FLAG_NAMESPACE = 'commerce'

# .. toggle_name: commerce.enable_refund_policy_change
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Gates the LP-1429 refund policy change for seats and entitlements.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-10-29
# .. toggle_target_removal_date: None
# .. toggle_warning: Defines the flag only; no code path reads it yet.
# .. toggle_tickets: LP-1429
ENABLE_REFUND_POLICY_CHANGE = WaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.enable_refund_policy_change', __name__
)


def should_apply_refund_policy_change():
    return ENABLE_REFUND_POLICY_CHANGE.is_enabled()
