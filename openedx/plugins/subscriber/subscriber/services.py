"""
Subscriber services.

This module handles course categorization logic for Subscriber Learner Dashboard.

Structured to allow future integration with:
- Subscription Catalog API
- Subscriber entitlement service
"""
import requests 
from django.conf import settings
from common.djangoapps.student.models import CourseEnrollment


def get_segment_traits(user_id):
    """
    Fetch Segment profile traits for given LMS user_id.
    """
    # Safety check — prevents crashes if settings are not configured
    if not getattr(settings, "SEGMENT_SPACE_ID", None) or not getattr(settings, "SEGMENT_PROFILE_API_TOKEN", None):
        return {}
    url = (
        f"https://profiles.segment.com/v1/spaces/"
        f"{settings.SEGMENT_SPACE_ID}/collections/users/"
        f"profiles/user_id:{user_id}/traits"
    )

    try:
        response = requests.get(
            url,
            auth=(settings.SEGMENT_PROFILE_API_TOKEN, ""),
            timeout=5,
        )

        if response.status_code != 200:
            return {}

        data = response.json()
        return data.get("traits", {})

    except Exception as e:
        return {}

def get_segment_profile_data(user):
    traits = get_segment_traits(user.id)

    return {
        "email": traits.get("email"),
        "username": traits.get("username"),
        "is_disabled": traits.get("is_disabled"),
        "disabled_users": traits.get("disabled_users"),
        "last_enrollment": traits.get("last_enrollment"),
    }

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
            
    # Fetch Segment Profile Data
    segment_profile = get_segment_profile_data(user)

    # Example personalization flag derived from Segment
    account_disabled = segment_profile.get("is_disabled")

    return {
        "subscription_courses": subscription_courses,
        "upgradeable_courses": upgradeable_courses,
        "non_upgradeable_courses": non_upgradeable_courses,
        "segment_profile": segment_profile,
        "account_disabled_from_segment": account_disabled,
    }
