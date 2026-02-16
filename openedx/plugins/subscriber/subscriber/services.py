"""
Subscriber services.

This module handles course categorization logic for Subscriber Learner Dashboard.

Structured to allow future integration with:
- Subscription Catalog API
- Subscriber entitlement service
"""

from common.djangoapps.student.models import CourseEnrollment


# TODO: Replace this with Subscription Catalog API call
def get_subscription_catalog_course_ids():
    """
    Returns list of course IDs that are part of Subscription Catalog.

    Currently hardcoded for POC.
    Future: Fetch from Subscription Catalog Service API.
    """
    return [
        "course-v1:edX+DemoX+Demo_Course",
    ]


# TODO: Replace this with real subscription entitlement check
def is_user_subscriber(user):
    """
    Returns True if user is an active subscriber.

    Currently hardcoded for POC.
    Future: Fetch from Subscription Entitlement Service.
    """
    return True


def get_user_enrollments(user):
    """
    Fetch all active enrollments for the user.
    """
    return CourseEnrollment.objects.filter(
        user=user,
        is_active=True
    )


def get_categorized_courses(user):
    """
    Categorize user courses into:

    - subscription_courses
    - upgradeable_courses
    - non_upgradeable_courses
    """

    enrollments = get_user_enrollments(user)

    subscription_catalog = get_subscription_catalog_course_ids()

    user_is_subscriber = is_user_subscriber(user)

    subscription_courses = []
    upgradeable_courses = []
    non_upgradeable_courses = []

    for enrollment in enrollments:

        course_id = str(enrollment.course_id)

        # Course is part of subscription catalog
        if course_id in subscription_catalog:

            # User has full access OR is subscriber
            if enrollment.mode != "audit" or user_is_subscriber:
                subscription_courses.append(course_id)

            else:
                upgradeable_courses.append(course_id)

        # Course not part of subscription catalog
        else:
            non_upgradeable_courses.append(course_id)

    return {
        "subscription_courses": subscription_courses,
        "upgradeable_courses": upgradeable_courses,
        "non_upgradeable_courses": non_upgradeable_courses,
    }
