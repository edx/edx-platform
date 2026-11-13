"""
Toggles for the commerce app.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from edx_toggles.toggles import WaffleFlag

log = logging.getLogger(__name__)

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


def get_refund_policy_effective_at():
    """
    Return the moment the refund policy change takes effect, as an aware datetime, or None if
    ``settings.REFUND_POLICY_EFFECTIVE_AT`` is missing or unparseable.

    The setting may be a datetime or an ISO 8601 string. A value without a UTC offset is read as
    America/New_York time.
    """
    value = getattr(settings, 'REFUND_POLICY_EFFECTIVE_AT', None)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            log.error('REFUND_POLICY_EFFECTIVE_AT is not a valid ISO 8601 datetime: %r', value)
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo('America/New_York'))
    return value


def is_order_under_refund_policy_change(placed_at):
    """
    Whether an order placed at ``placed_at`` follows the new refund policy (no automatic refund on unenroll).

    True only when the flag is on and the order was placed at or after the effective date. Orders with an
    unknown date, or a missing or invalid effective date, keep the existing automatic-refund behavior.
    """
    if not should_apply_refund_policy_change() or placed_at is None:
        return False
    effective_at = get_refund_policy_effective_at()
    if effective_at is None:
        return False
    if placed_at.tzinfo is None:
        placed_at = placed_at.replace(tzinfo=ZoneInfo('UTC'))
    return placed_at >= effective_at


def get_enrollment_order_date(course_enrollment, fetch_missing=False):
    """
    Return the aware UTC datetime the enrollment's order was placed, or None if unknown.

    Reads the ``date_placed`` order attribute. With ``fetch_missing`` it falls back to ecommerce when the
    attribute is not stored locally; leave it off on read paths that must not make a network call.
    """
    # Imported here to avoid circular imports.
    from openedx.core.djangoapps.commerce.utils import ECOMMERCE_DATE_FORMAT

    date_placed = course_enrollment.get_order_attribute_value('date_placed')
    if not date_placed and fetch_missing and course_enrollment.get_order_attribute_value('order_number'):
        date_placed = course_enrollment.get_order_attribute_from_ecommerce('date_placed')
    if not date_placed:
        return None
    try:
        return datetime.strptime(date_placed, ECOMMERCE_DATE_FORMAT).replace(tzinfo=ZoneInfo('UTC'))
    except (TypeError, ValueError):
        log.warning('Unparseable date_placed %r on enrollment %s', date_placed, course_enrollment.id)
        return None


def enrollment_follows_refund_policy_change(course_enrollment, fetch_missing=False):
    """Whether this enrollment's order follows the new refund policy."""
    if course_enrollment is None or not should_apply_refund_policy_change():
        return False
    return is_order_under_refund_policy_change(get_enrollment_order_date(course_enrollment, fetch_missing))


def entitlement_follows_refund_policy_change(course_entitlement):
    """
    Whether this entitlement follows the new refund policy.

    Entitlements store no order date, so the entitlement's creation time stands in for it.
    """
    return is_order_under_refund_policy_change(course_entitlement.created)


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
