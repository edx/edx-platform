"""
Tests for Course Home API toggles.
"""
from unittest.mock import Mock, patch

from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode

from ..toggles import learner_can_preview_verified_content


class TestLearnerCanPreviewVerifiedContent(TestCase):
    """Test cases for learner_can_preview_verified_content function."""

    def setUp(self):
        """Set up test fixtures."""
        self.course_key = CourseKey.from_string("course-v1:TestX+CS101+2024")
        self.user = Mock()

        # Set up patchers
        self.feature_enabled_patcher = patch(
            "lms.djangoapps.course_home_api.toggles.audit_learner_verified_preview_is_enabled"
        )
        self.has_verified_mode_patcher = patch(
            "common.djangoapps.course_modes.models.CourseMode.has_verified_mode"
        )
        self.get_enrollment_patcher = patch(
            "common.djangoapps.student.models.CourseEnrollment.get_enrollment"
        )

        # Start patchers
        self.mock_feature_enabled = self.feature_enabled_patcher.start()
        self.mock_has_verified_mode = self.has_verified_mode_patcher.start()
        self.mock_get_enrollment = self.get_enrollment_patcher.start()

    def _enroll_user(self, mode):
        """Helper method to set up user enrollment mock."""
        mock_enrollment = Mock()
        mock_enrollment.mode = mode
        self.mock_get_enrollment.return_value = mock_enrollment

    def tearDown(self):
        """Clean up patchers."""
        self.feature_enabled_patcher.stop()
        self.has_verified_mode_patcher.stop()
        self.get_enrollment_patcher.stop()

    def test_all_conditions_met_returns_true(self):
        """Test that function returns True when all conditions are met."""
        # Given the feature is enabled, course has verified mode, and user is enrolled as audit
        self.mock_feature_enabled.return_value = True
        self.mock_has_verified_mode.return_value = True
        self._enroll_user(CourseMode.AUDIT)

        # When I check if the learner can preview verified content
        result = learner_can_preview_verified_content(self.course_key, self.user)

        # Then the result should be True
        self.assertTrue(result)

    def test_feature_disabled_returns_false(self):
        """Test that function returns False when feature is disabled."""
        # Given the feature is disabled
        self.mock_feature_enabled.return_value = False

        # ... even if all other conditions are met
        self.mock_has_verified_mode.return_value = True
        self._enroll_user(CourseMode.AUDIT)

        # When I check if the learner can preview verified content
        result = learner_can_preview_verified_content(self.course_key, self.user)

        # Then the result should be False
        self.assertFalse(result)

    def test_no_verified_mode_returns_false(self):
        """Test that function returns False when course has no verified mode."""
        # Given the course does not have a verified mode
        self.mock_has_verified_mode.return_value = False

        # ... even if all other conditions are met
        self.mock_feature_enabled.return_value = True
        self._enroll_user(CourseMode.AUDIT)

        # When I check if the learner can preview verified content
        result = learner_can_preview_verified_content(self.course_key, self.user)

        # Then the result should be False
        self.assertFalse(result)

    def test_no_enrollment_returns_false(self):
        """Test that function returns False when user is not enrolled."""
        # Given the user is unenrolled
        self.mock_get_enrollment.return_value = None

        # ... even if all other conditions are met
        self.mock_feature_enabled.return_value = True
        self.mock_has_verified_mode.return_value = True

        # When I check if the learner can preview verified content
        result = learner_can_preview_verified_content(self.course_key, self.user)

        # Then the result should be False
        self.assertFalse(result)

    def test_verified_enrollment_returns_false(self):
        """Test that function returns False when user is enrolled in verified mode."""
        # Given the user is not enrolled as audit
        self._enroll_user(CourseMode.VERIFIED)

        # ... even if all other conditions are met
        self.mock_feature_enabled.return_value = True
        self.mock_has_verified_mode.return_value = True

        # When I check if the learner can preview verified content
        result = learner_can_preview_verified_content(self.course_key, self.user)

        # Then the result should be False
        self.assertFalse(result)
