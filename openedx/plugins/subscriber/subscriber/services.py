from common.djangoapps.student.models import CourseEnrollment

# POC-only: hardcoded subscription catalog
SUBSCRIPTION_COURSE_IDS = [
    "course-v1:edX+DemoX+Demo_Course",
]


def get_categorized_courses(user):
    """
    Fetch user's enrolled courses and categorize them
    for the Subscriber Learner Dashboard.
    """

    enrollments = CourseEnrollment.objects.filter(
        user=user,
        is_active=True
    )

    subscription_courses = []
    upgradeable_courses = []
    non_upgradeable_courses = []

    # POC assumption: user is already a subscriber
    user_is_subscriber = True

    for enrollment in enrollments:
        course_id = str(enrollment.course_id)

        if course_id in SUBSCRIPTION_COURSE_IDS:
            if user_is_subscriber:
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
