from common.djangoapps.student.models import CourseEnrollment

# TODO: Replace with Subscription Catalog API call
SUBSCRIPTION_COURSE_IDS = [
    "course-v1:edX+DemoX+Demo_Course",
]


def get_categorized_courses(user):
    enrollments = CourseEnrollment.objects.filter(
        user=user,
        is_active=True
    )

    subscription_courses = []
    upgradeable_courses = []
    non_upgradeable_courses = []

    # TODO: Replace with real subscription status check
    user_is_subscriber = True

    subscription_catalog_courses = SUBSCRIPTION_COURSE_IDS

    for enrollment in enrollments:
        course_id = str(enrollment.course_id)

        is_in_catalog = course_id in subscription_catalog_courses

        # TODO: Replace with enrollment mode / upgrade check
        is_full_access = user_is_subscriber

        if is_in_catalog:
            if is_full_access:
                subscription_courses.append(course_id)
            else:
                upgradeable_courses.append(course_id)
        else:
            non_upgradeable_courses.append(course_id)

    return {
        "subscription_courses": subscription_courses,
        "upgradeable_courses": upgradeable_courses,
        "non_upgradeable_courses": non_upgradeable_courses,
    }
