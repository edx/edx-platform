"""
Unit tests for the course waffle flags view
"""

from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag

from cms.djangoapps.contentstore import toggles
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel


class CourseWaffleFlagsViewTest(CourseTestCase):
    """
    Basic test for the CourseWaffleFlagsView endpoint, which returns waffle flag states
    for a specific course or globally if no course ID is provided.
    """

    maxDiff = None  # Show the whole dictionary in the diff

    defaults = {
        "enable_course_optimizer": False,
        "use_new_advanced_settings_page": True,
        "use_new_certificates_page": True,
        "use_new_course_outline_page": True,
        "use_new_course_team_page": True,
        "use_new_custom_pages": True,
        "use_new_export_page": True,
        "use_new_files_uploads_page": True,
        "use_new_grading_page": True,
        "use_new_group_configurations_page": True,
        "use_new_home_page": True,
        "use_new_import_page": True,
        "use_new_schedule_details_page": True,
        "use_new_textbooks_page": True,
        "use_new_unit_page": True,
        "use_new_updates_page": True,
        "use_new_video_uploads_page": False,
        "use_react_markdown_editor": False,
        "use_video_gallery_flow": False,
        "enable_course_optimizer_check_prev_run_links": False,
        "enable_unit_expanded_view": False,
        "enable_outline_component_creation": False,
        "enable_audio_description": False,
        "enable_transcript_editor": False,
    }

    def setUp(self):
        super().setUp()
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=toggles.ENABLE_COURSE_OPTIMIZER.name,
            course_id=self.course.id,
            enabled=True,
        )
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=toggles.ENABLE_COURSE_OPTIMIZER_CHECK_PREV_RUN_LINKS.name,
            course_id=self.course.id,
            enabled=True,
        )

    def test_global_defaults(self):
        url = reverse("cms.djangoapps.contentstore:v1:course_waffle_flags")
        response = self.client.get(url)
        assert response.data == self.defaults

    def test_course_override(self):
        url = reverse(
            "cms.djangoapps.contentstore:v1:course_waffle_flags",
            kwargs={"course_id": self.course.id},
        )
        response = self.client.get(url)
        assert response.data == {
            **self.defaults,
            "enable_course_optimizer": True,
            "enable_course_optimizer_check_prev_run_links": True,
        }

    @override_waffle_flag(toggles.ENABLE_AUDIO_DESCRIPTION, active=True)
    def test_audio_description_upload_flag_enabled(self):
        """
        When the global AD upload flag is on, the serializer should
        report it as True regardless of which course (or no course) is
        in the request.
        """
        url = reverse("cms.djangoapps.contentstore:v1:course_waffle_flags")
        response = self.client.get(url)
        assert response.data["enable_audio_description"] is True

    def test_enable_transcript_editor_flag_default_is_false(self):
        """
        The contentstore.enable_transcript_editor flag should default to False when not
        overridden, both globally and for a specific course.
        """
        global_url = reverse("cms.djangoapps.contentstore:v1:course_waffle_flags")
        course_url = reverse(
            "cms.djangoapps.contentstore:v1:course_waffle_flags",
            kwargs={"course_id": self.course.id},
        )
        for url in (global_url, course_url):
            response = self.client.get(url)
            assert response.data["enable_transcript_editor"] is False

    @override_waffle_flag(toggles.ENABLE_TRANSCRIPT_EDITOR, active=True)
    def test_enable_transcript_editor_flag_enabled_globally(self):
        """
        When the contentstore.enable_transcript_editor flag is active globally, the
        serializer should return True for both the global endpoint and any
        course-specific endpoint.
        """
        global_url = reverse("cms.djangoapps.contentstore:v1:course_waffle_flags")
        course_url = reverse(
            "cms.djangoapps.contentstore:v1:course_waffle_flags",
            kwargs={"course_id": self.course.id},
        )
        for url in (global_url, course_url):
            response = self.client.get(url)
            assert response.data["enable_transcript_editor"] is True

    def test_enable_transcript_editor_flag_enabled_per_course(self):
        """
        When the contentstore.enable_transcript_editor flag is enabled via a
        WaffleFlagCourseOverrideModel entry for a specific course, the
        course-scoped endpoint should return True while the global endpoint
        should remain False.
        """
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=toggles.ENABLE_TRANSCRIPT_EDITOR.name,
            course_id=self.course.id,
            enabled=True,
        )
        global_url = reverse("cms.djangoapps.contentstore:v1:course_waffle_flags")
        course_url = reverse(
            "cms.djangoapps.contentstore:v1:course_waffle_flags",
            kwargs={"course_id": self.course.id},
        )
        global_response = self.client.get(global_url)
        course_response = self.client.get(course_url)
        assert global_response.data["enable_transcript_editor"] is False
        assert course_response.data["enable_transcript_editor"] is True
