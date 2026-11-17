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
# .. toggle_warning: Set to Everyone (Yes or No) only. Testing, Percent, Users and Groups modes give different
#   answers per request or per user, and are always off outside a request (celery tasks, management commands).
# .. toggle_tickets: LP-1413, LP-1429
ENABLE_REFUND_POLICY_CHANGE = WaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.enable_refund_policy_change', __name__
)


def should_apply_refund_policy_change():
    return ENABLE_REFUND_POLICY_CHANGE.is_enabled()


# .. toggle_name: commerce.show_refund_policy_change_notice
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Shows learners the one-time acknowledgement modal announcing the LP-1429 refund policy change.
#   Independent of commerce.enable_refund_policy_change so the notice can be shown before the policy takes effect and
#   removed later without changing the policy.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-11-02
# .. toggle_target_removal_date: None
# .. toggle_warning: Turn this on before commerce.enable_refund_policy_change so learners see the notice before the
#   policy applies. Set to Everyone (Yes or No) only, so every learner gets the same answer.
# .. toggle_tickets: LP-1413, LP-1429
SHOW_REFUND_POLICY_CHANGE_NOTICE = WaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.show_refund_policy_change_notice', __name__
)


def should_show_refund_policy_change_notice():
    return SHOW_REFUND_POLICY_CHANGE_NOTICE.is_enabled()


# .. toggle_name: commerce.redirect_to_alacarte_checkout
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Points every purchase link the LMS builds at the checkout redirect view
#   (/commerce/checkout/), which sends the learner to the a la carte checkout. Purchases that checkout doesn't take go
#   to ALACARTE_CHECKOUT_FALLBACK_URL, or to the ecommerce checkout while it isn't set. When disabled, purchase links
#   point at the ecommerce checkout as before, and the view sends the links it already gave out there too.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-11-17
# .. toggle_target_removal_date: None
# .. toggle_warning: Users, Groups, Percent and Testing modes are always off outside a request, so links built in
#   celery tasks, such as the upgrade emails, follow only the Everyone setting.
REDIRECT_TO_ALACARTE_CHECKOUT = WaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.redirect_to_alacarte_checkout', __name__
)


def should_redirect_to_alacarte_checkout():
    return REDIRECT_TO_ALACARTE_CHECKOUT.is_enabled()
