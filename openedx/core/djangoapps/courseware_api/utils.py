"""
Courseware API Mixins.
"""

from babel.numbers import get_currency_symbol

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollmentCelebration, UserCelebration
from lms.djangoapps.courseware.utils import (
    can_show_verified_upgrade,
    track_selection_upgrade_link,
    verified_upgrade_deadline_link,
)
from openedx.features.course_duration_limits.access import get_user_course_expiration_date
from openedx.features.discounts.applicability import can_show_streak_discount_coupon


def get_celebrations_dict(user, enrollment, course, browser_timezone):
    """
    Returns a dict of celebrations that should be performed.
    """
    if not enrollment:
        return {
            'first_section': False,
            'streak_length_to_celebrate': None,
            'streak_discount_enabled': False,
            'weekly_goal': False,
        }

    streak_length_to_celebrate = UserCelebration.perform_streak_updates(
        user, course.id, browser_timezone
    )
    celebrations = {
        'first_section': CourseEnrollmentCelebration.should_celebrate_first_section(enrollment),
        'streak_length_to_celebrate': streak_length_to_celebrate,
        'streak_discount_enabled': False,
        'weekly_goal': CourseEnrollmentCelebration.should_celebrate_weekly_goal(enrollment),
    }

    if streak_length_to_celebrate:
        # We only want to offer the streak discount
        # if the course has not ended, is upgradeable and the user is not an enterprise learner

        if can_show_streak_discount_coupon(user, course):
            # Send course streak coupon event
            course_key = str(course.id)
            modes_dict = CourseMode.modes_for_course_dict(course_id=course_key, include_expired=False)
            verified_mode = modes_dict.get('verified', None)
            if verified_mode:
                celebrations['streak_discount_enabled'] = True

    return celebrations


def serialize_upgrade_info(user, course_overview, enrollment):
    """
    Return verified mode upgrade information, or None.

    This is used in a few API views to provide consistent upgrade info to frontends.

    ``upgrade_url`` is the link of the Upgrade buttons: the track selection page with the upgrade intent when the
    track selection flags are on for the course (see track_selection_upgrade_link), else the checkout.
    ``checkout_url`` is always the checkout, for the buttons that buy the certificate straight away (Buy).
    """
    if not can_show_verified_upgrade(user, enrollment):
        return None

    mode = CourseMode.verified_mode_for_course(course=course_overview)
    return {
        'access_expiration_date': get_user_course_expiration_date(user, course_overview),
        'currency': mode.currency.upper(),
        'currency_symbol': get_currency_symbol(mode.currency.upper()),
        'price': mode.min_price,
        'sku': mode.sku,
        'upgrade_url': track_selection_upgrade_link(user, course=course_overview),
        'checkout_url': verified_upgrade_deadline_link(user, course_overview),
    }
