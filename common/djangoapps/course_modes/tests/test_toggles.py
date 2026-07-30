"""
Tests for course modes toggles.
"""

from unittest.mock import patch

from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.toggles import course_modes_mfe_track_selection_is_active


class TestCourseModesMfeTrackSelectionToggle(TestCase):
    """Test cases for course_modes_mfe_track_selection_is_active."""

    def setUp(self):
        self.course_key = CourseKey.from_string("course-v1:TestX+CS101+2024")

    @patch("common.djangoapps.course_modes.toggles.COURSE_MODES_MFE_TRACK_SELECTION.is_enabled")
    def test_returns_true_when_waffle_enabled(self, mock_is_enabled):
        mock_is_enabled.return_value = True
        self.assertTrue(course_modes_mfe_track_selection_is_active(self.course_key))

    @patch("common.djangoapps.course_modes.toggles.COURSE_MODES_MFE_TRACK_SELECTION.is_enabled")
    def test_returns_false_when_waffle_disabled(self, mock_is_enabled):
        mock_is_enabled.return_value = False
        self.assertFalse(course_modes_mfe_track_selection_is_active(self.course_key))

    @patch("common.djangoapps.course_modes.toggles.COURSE_MODES_MFE_TRACK_SELECTION.is_enabled")
    def test_returns_false_for_deprecated_course_key(self, mock_is_enabled):
        mock_is_enabled.return_value = True
        deprecated_key = CourseKey.from_string("OrgX/Course/Run")
        self.assertFalse(course_modes_mfe_track_selection_is_active(deprecated_key))
