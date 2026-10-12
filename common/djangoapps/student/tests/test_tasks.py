"""
Celery task tests
"""
from datetime import datetime
from unittest.mock import patch, Mock, PropertyMock

import pytest
from django.conf import settings
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag

from common.djangoapps.student.tasks import (
    MAX_RETRIES,
    ENABLE_SES_FOR_COURSE_ENROLLMENT,
    _build_enrollment_email_image_urls,
    send_course_enrollment_email,
)
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

TASK_LOGGER = 'common.djangoapps.student.tasks.log'
BRAZE_COURSE_ENROLLMENT_CANVAS_ID = "braze-canvas-id"


@override_settings(
    BRAZE_COURSE_ENROLLMENT_CANVAS_ID=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
    LEARNING_MICROFRONTEND_URL="https://learningmfe.openedx.org",
)
class TestCourseEnrollmentEmailTask(ModuleStoreTestCase):
    """
    Tests for send_course_enrollment_email task.
    """

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()
        self.user = UserFactory.create(
            username="joe", email="joe@joe.com", password="password"
        )
        self.course = CourseFactory.create()
        self.course_uuid = "d08af18e-7fd5-45eb-a834-a9decc6d9afa"
        self.send_course_enrollment_email_kwargs = {
            "user_id": self.user.id,
            "course_id": str(self.course.id),
            "course_title": "Test course",
            "short_description": "Short description of course",
            "course_ended": False,
            "pacing_type": "self-paced",
            "track_mode": "audit",
        }

    @staticmethod
    def _get_course_run():
        """
        Helper method for course run details.
        """
        return {
            "title": "Test Course",
            "short_description": "An introduction to computer science.",
            "weeks_to_complete": 8,
            "min_effort": 5,
            "max_effort": 10,
            "pacing_type": "self-paced",
            "image": {
                "src": "https://prod/media/course/image/a3d1899c3344.png",
            },
            "staff": [
                {
                    "given_name": "Mario",
                    "family_name": "Ricci",
                    "slug": "mario-ricci",
                    "position": {
                        "organization_name": "University of Adelaide",
                    },
                    "profile_image_url": "https://prod.org/media/people/profile_images/0ad.jpg",
                },
            ],
            "learners_count": "12345",
        }

    @staticmethod
    def _get_course_owners():
        """
        Helper method for course owner details.
        """
        return [
            {
                "logo_image_url": "https://prod/organization/logos/2cc39992c67a.png",
                "name": "edX University",
            }
        ]

    @staticmethod
    def _get_course_dates():
        """
        Helper method for course dates.
        """
        return [
            {
                "due_date": "Thu, Jul 28, 2022",
                "title": "Course starts",
                "assignment_type": "",
                "link": "",
                "assignment_count": 0,
                "due_time": "",
            },
            {
                "due_date": "Thu, Aug 25, 2022",
                "title": "",
                "assignment_type": "",
                "link": "",
                "assignment_count": 0,
                "due_time": "",
            },
            {
                "due_date": "Mon, Aug 29, 2022",
                "title": "Importance of an Operations Mindset",
                "assignment_type": "Ops Challenge",
                "link": "https://courses.edx.org/courses/course-v1:BabsonX+EPS03x+3T2018",
                "assignment_count": 5,
                "due_time": "2:25 AM GMT+5",
            },
        ]

    def _get_canvas_properties(
        self, add_course_run_details=True, add_course_dates=True
    ):
        """
        Helper method that returns canvas entry properties.
        """
        canvas_properties = {
            "course_run_key": str(self.course.id),
            "learning_base_url": "https://learningmfe.openedx.org",
            "lms_base_url": settings.LMS_ROOT_URL,
            "course_price": 0,
            "current_year": datetime.now().year,
            "goals_enabled": False,
            "course_date_blocks": [],
            "course_title": self.send_course_enrollment_email_kwargs["course_title"],
            "short_description": self.send_course_enrollment_email_kwargs["short_description"],
            "pacing_type": self.send_course_enrollment_email_kwargs["pacing_type"],
            "track_mode": self.send_course_enrollment_email_kwargs["track_mode"],
            "user_name": self.user.get_full_name() or self.user.first_name or self.user.username,
        }

        # Payload now always includes image URLs used by SES and ignored by Braze.
        canvas_properties.update(_build_enrollment_email_image_urls(language='en'))

        if add_course_dates:
            canvas_properties.update({"course_date_blocks": self._get_course_dates()})

        if add_course_run_details:
            course_run = self._get_course_run()
            canvas_properties.update(
                {
                    "instructors": [
                        {
                            "name": "Mario Ricci",
                            "profile_image_url": "https://prod.org/media/people/profile_images/0ad.jpg",
                            "organization_name": "University of Adelaide",
                            "bio_url": "None/bio/mario-ricci",
                        }
                    ],
                    "instructors_count": "odd",
                    "min_effort": course_run["min_effort"],
                    "max_effort": course_run["max_effort"],
                    "weeks_to_complete": course_run["weeks_to_complete"],
                    "learners_count": "",
                    "banner_image_url": course_run["image"]["src"],
                    "course_title": course_run["title"],
                    "short_description": course_run["short_description"],
                    "pacing_type": course_run["pacing_type"],
                    "partner_image_url": self._get_course_owners()[0]["logo_image_url"],
                    "org_name": self._get_course_owners()[0]["name"],
                }
            )

        return canvas_properties

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    def test_success_calls_for_canvas_properties(
        self,
        mock_get_email_client,
        mock_get_course_dates_for_email,
        mock_get_course_run_details,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test to verify the "canvas entry properties" for enrollment email when
        all external calls are successful.
        """
        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = self._get_course_owners()
        mock_get_course_run_details.return_value = self._get_course_run()
        mock_get_course_dates_for_email.return_value = self._get_course_dates()

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch(
        "common.djangoapps.student.tasks.get_course_dates_for_email",
        Mock(side_effect=Exception),
    )
    def test_canvas_properties_without_course_dates(
        self,
        mock_get_email_client,
        mock_get_course_run_details,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test that if exception is raised for the course dates call, correct
        canvas properties are sent to Braze.
        """
        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = self._get_course_owners()
        mock_get_course_run_details.return_value = self._get_course_run()

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(add_course_dates=False),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch(
        "common.djangoapps.student.tasks.get_course_run_details",
        Mock(side_effect=Exception),
    )
    def test_canvas_properties_on_get_course_run_details_failure(
        self,
        mock_get_email_client,
        mock_get_course_dates_for_email,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test to verify the "canvas entry properties" in the enrollment email when
        get_course_run_details fails.
        """
        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = self._get_course_owners()
        mock_get_course_dates_for_email.return_value = self._get_course_dates()

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(
                add_course_run_details=False
            ),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch(TASK_LOGGER)
    def test_email_task_when_course_uuid_is_missing(
        self,
        mocked_logger,
        mock_get_email_client,
        mock_get_course_dates_for_email,
        mock_get_course_uuid_for_course,
    ):
        """
        Test that exception is logged when course_uuid returned by
        get_course_uuid_for_course is None and that email is sent with
        appropriate canvas properties.
        """
        mock_get_course_uuid_for_course.return_value = None
        mock_get_course_dates_for_email.return_value = self._get_course_dates()

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mocked_logger.warning.assert_called_once_with(
            f"[Course Enrollment] Course run call failed for "
            f"user: {self.user.id} course: {self.course.id} error: Missing course_uuid"
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(add_course_run_details=False),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch(TASK_LOGGER)
    def test_email_task_when_course_run_is_missing(
        self,
        mocked_logger,
        mock_get_email_client,
        mock_get_course_dates_for_email,
        mock_get_course_run_details,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test that exception is logged when course_run returned by
        get_course_run_details is an empty dictionary and that email is sent with
        appropriate canvas properties.
        """
        mock_get_course_dates_for_email.return_value = self._get_course_dates()
        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = []
        mock_get_course_run_details.return_value = {}

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mocked_logger.warning.assert_called_once_with(
            f"[Course Enrollment] Course run call failed for "
            f"user: {self.user.id} course: {self.course.id} error: Missing course_run"
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(add_course_run_details=False),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    def test_retry_with_email_client_exception(
        self,
        mock_get_course_dates_for_email,
        mock_get_course_run_details,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test that we retry when an exception occurs from Braze Client
        """

        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = self._get_course_owners()
        mock_get_course_run_details.return_value = self._get_course_run()
        mock_get_course_dates_for_email.return_value = self._get_course_dates()

        with patch(
            'common.djangoapps.student.tasks.get_email_client',
            new_callable=PropertyMock,
            side_effect=Exception('Braze Client Exception')
        ) as mock_get_email_client:
            task = send_course_enrollment_email.apply_async(
                kwargs=self.send_course_enrollment_email_kwargs
            )
        pytest.raises(Exception, task.get)
        self.assertEqual(mock_get_email_client.call_count, (MAX_RETRIES + 1))


@override_settings(
    BRAZE_COURSE_ENROLLMENT_CANVAS_ID=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
    LEARNING_MICROFRONTEND_URL="https://learningmfe.openedx.org",
    DEFAULT_FROM_EMAIL="no-reply@edx.org",
    LMS_ROOT_URL="https://courses.edx.org",
)
class TestCourseEnrollmentEmailSESRouting(ModuleStoreTestCase):
    """
    Tests for waffle-flag-based SES/Braze routing in send_course_enrollment_email.

    Mirrors the pattern used for account-activation SES routing in
    openedx/core/djangoapps/user_authn/tasks.py.
    """

    COURSE_ID = "course-v1:edX+DemoX+2024"

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username="ses_user", email="ses@example.com")
        # Use a string course ID — no CourseFactory needed since all external
        # calls are mocked.
        self.task_kwargs = {
            "user_id": self.user.id,
            "course_id": self.COURSE_ID,
            "course_title": "SES Test Course",
            "short_description": "SES description",
            "course_ended": False,
            "pacing_type": "self_paced",
            "track_mode": "audit",
        }

    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=False)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch("common.djangoapps.student.tasks._send_ses_enrollment_email")
    def test_braze_path_used_when_waffle_disabled(
        self,
        mock_ses_send,
        mock_get_email_client,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """When the waffle flag is off, Braze canvas message is sent and SES is NOT called."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)

        mock_get_email_client.return_value.send_canvas_message.assert_called_once()
        mock_ses_send.assert_not_called()

    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=True)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch("common.djangoapps.student.tasks._send_ses_enrollment_email")
    def test_ses_path_used_when_waffle_enabled(
        self,
        mock_ses_send,
        mock_get_email_client,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """When the waffle flag is on, SES send is called and Braze is NOT called."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)

        mock_ses_send.assert_called_once()
        mock_get_email_client.return_value.send_canvas_message.assert_not_called()

    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=True)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch(
        "common.djangoapps.student.tasks._send_ses_enrollment_email",
        side_effect=Exception("SES failure"),
    )
    def test_ses_failure_falls_back_to_braze(
        self,
        mock_ses_send,
        mock_get_email_client,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """If SES send fails, task should fall back to Braze without retrying."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)

        mock_ses_send.assert_called_once()
        mock_get_email_client.return_value.send_canvas_message.assert_called_once()

    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=True)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch(
        "common.djangoapps.student.tasks._send_ses_enrollment_email",
        side_effect=Exception("SES failure"),
    )
    @patch(
        "common.djangoapps.student.tasks.get_email_client",
        new_callable=PropertyMock,
        side_effect=Exception("Braze fallback failure"),
    )
    def test_ses_and_braze_fallback_failure_triggers_retry(
        self,
        mock_get_email_client,
        mock_ses_send,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """If both SES and Braze fallback fail, task should retry up to MAX_RETRIES."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        task = send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)
        pytest.raises(Exception, task.get)
        self.assertEqual(mock_ses_send.call_count, MAX_RETRIES + 1)
        self.assertEqual(mock_get_email_client.call_count, MAX_RETRIES + 1)

    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=True)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.render_to_string", return_value="<html></html>")
    @patch("common.djangoapps.student.tasks.EmailMultiAlternatives")
    def test_ses_send_uses_correct_template_and_recipient(
        self,
        mock_email_cls,
        mock_render,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """_send_ses_enrollment_email renders the namespaced template and addresses to user.email."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)

        # Template should use the new namespaced path
        render_call_args = mock_render.call_args
        assert render_call_args[0][0] == "emails/enrollment_en.html"

        # Email should go to the correct user — EmailMultiAlternatives(subject, body, from, to=[...])
        _, email_kwargs = mock_email_cls.call_args
        assert self.user.email in email_kwargs.get('to', [])
        assert email_kwargs.get('subject') == f"You're enrolled in {self.task_kwargs['course_title']}"
        assert email_kwargs.get('body')
        assert email_kwargs.get('reply_to')

    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=True)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.UserPreference.get_value", return_value="es")
    @patch("common.djangoapps.student.tasks.render_to_string", return_value="<html></html>")
    @patch("common.djangoapps.student.tasks.EmailMultiAlternatives")
    def test_ses_send_uses_spanish_template_when_user_pref_is_spanish(
        self,
        mock_email_cls,
        mock_render,
        _mock_user_pref,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """SES should render Spanish enrollment template when user language preference is Spanish."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)

        render_call_args = mock_render.call_args
        assert render_call_args[0][0] == "emails/enrollment_es.html"

        _, email_kwargs = mock_email_cls.call_args
        assert self.user.email in email_kwargs.get('to', [])
        assert email_kwargs.get('subject') == f"Te has inscrito en {self.task_kwargs['course_title']}"
        assert email_kwargs.get('body')

    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=False)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.UserPreference.get_value", return_value="es")
    @patch("common.djangoapps.student.tasks.get_email_client")
    def test_braze_canvas_context_includes_spanish_image_keys_when_user_pref_is_spanish(
        self,
        mock_get_email_client,
        _mock_user_pref,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """Braze payload should include Spanish image URL keys when user language preference is Spanish."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)

        _, send_kwargs = mock_get_email_client.return_value.send_canvas_message.call_args
        canvas_props = send_kwargs["canvas_entry_properties"]
        assert "timer_icon_es" in canvas_props
        assert "arrow_icon_es" in canvas_props

    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=True)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch("common.djangoapps.student.tasks._send_ses_enrollment_email")
    def test_braze_not_called_when_ses_succeeds(
        self,
        mock_ses_send,
        mock_get_email_client,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """Verify Braze is NOT called when SES succeeds (no duplicate sends)."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)

        mock_ses_send.assert_called_once()
        mock_get_email_client.return_value.send_canvas_message.assert_not_called()

    @override_settings(BRAZE_COURSE_ENROLLMENT_CANVAS_ID=None)
    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=False)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch(
        "common.djangoapps.student.tasks._send_braze_enrollment_email",
        side_effect=Exception("Braze fallback failure"),
    )
    def test_braze_path_retries_when_canvas_id_missing(
        self,
        _mock_braze_send,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """Braze path should retry when BRAZE_COURSE_ENROLLMENT_CANVAS_ID is missing."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        task = send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)
        pytest.raises(Exception, task.get)

    @override_settings(BRAZE_COURSE_ENROLLMENT_CANVAS_ID=None)
    @override_waffle_flag(ENABLE_SES_FOR_COURSE_ENROLLMENT, active=True)
    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch(
        "common.djangoapps.student.tasks._send_braze_enrollment_email",
        side_effect=Exception("Braze fallback failure"),
    )
    @patch(
        "common.djangoapps.student.tasks._send_ses_enrollment_email",
        side_effect=Exception("SES failure"),
    )
    def test_ses_fallback_retries_when_canvas_id_missing(
        self,
        _mock_ses_send,
        _mock_braze_send,
        mock_get_course_dates,
        mock_get_course_run,
        mock_get_owners,
        mock_get_uuid,
    ):
        """SES path should retry when SES fails and Braze fallback is misconfigured."""
        mock_get_uuid.return_value = "some-uuid"
        mock_get_owners.return_value = []
        mock_get_course_run.return_value = {}
        mock_get_course_dates.return_value = []

        task = send_course_enrollment_email.apply_async(kwargs=self.task_kwargs)
        pytest.raises(Exception, task.get)
