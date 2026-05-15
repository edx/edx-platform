"""Tests for Audit Expiry Urgency (v1) backend experiment."""

from datetime import timedelta
from unittest import mock

from django.test.utils import override_settings
from django.utils import timezone
from edx_toggles.toggles.testutils import override_waffle_flag
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollmentAttribute
from common.djangoapps.student.signals import ENROLLMENT_TRACK_UPDATED
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig

from lms.djangoapps.experiments.flags import AUDIT_EXPIRY_URGENCY_V1_ENABLED


class TestAuditExpiryUrgencyExperiment(SharedModuleStoreTestCase):
    """Tests for persistence and stickiness behavior."""

    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.now = timezone.now().replace(microsecond=0)
        self.course_1 = CourseOverviewFactory.create(start=self.now - timedelta(days=1), self_paced=True)
        self.course_2 = CourseOverviewFactory.create(start=self.now - timedelta(days=1), self_paced=True)

        # Make these "verified+audit" courses so duration limits apply.
        CourseModeFactory.create(course_id=self.course_1.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory.create(course_id=self.course_1.id, mode_slug=CourseMode.VERIFIED)
        CourseModeFactory.create(course_id=self.course_2.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory.create(course_id=self.course_2.id, mode_slug=CourseMode.VERIFIED)

        CourseDurationLimitConfig.objects.create(
            enabled=True,
            enabled_as_of=self.now - timedelta(days=365),
        )

    @override_settings(AUDIT_EXPIRY_EXPERIMENT_COURSES=[])
    def test_noop_when_not_allowlisted(self):
        enrollment = CourseEnrollmentFactory.create(course=self.course_1, mode=CourseMode.AUDIT, is_active=True)

        with override_waffle_flag(AUDIT_EXPIRY_URGENCY_V1_ENABLED, active=True):
            enrollment.save()

        assert not CourseEnrollmentAttribute.objects.filter(enrollment=enrollment).exists()

    def test_defaults_to_control_when_optimizely_unavailable(self):
        enrollment = CourseEnrollmentFactory.create(course=self.course_1, mode=CourseMode.AUDIT, is_active=True)

        with override_waffle_flag(AUDIT_EXPIRY_URGENCY_V1_ENABLED, active=True):
            with override_settings(AUDIT_EXPIRY_EXPERIMENT_COURSES=[str(self.course_1.id)]):
                with mock.patch(
                    'lms.djangoapps.experiments.audit_expiry_urgency.'
                    'OptimizelyClient.get_optimizely_client',
                    return_value=None,
                ):
                    enrollment.save()

        variant = CourseEnrollmentAttribute.objects.get(
            enrollment=enrollment,
            namespace='audit_expiry_experiment',
            name='variant',
        ).value
        assert variant == 'control_5_7_weeks'

        experiment_key = CourseEnrollmentAttribute.objects.get(
            enrollment=enrollment,
            namespace='audit_expiry_experiment',
            name='experiment_key',
        ).value
        expiry_days = CourseEnrollmentAttribute.objects.get(
            enrollment=enrollment,
            namespace='audit_expiry_experiment',
            name='expiry_days',
        ).value
        assigned_at = CourseEnrollmentAttribute.objects.get(
            enrollment=enrollment,
            namespace='audit_expiry_experiment',
            name='assigned_at',
        ).value
        decision_source = CourseEnrollmentAttribute.objects.get(
            enrollment=enrollment,
            namespace='audit_expiry_experiment',
            name='decision_source',
        ).value

        assert experiment_key == 'audit_expiry_urgency_v1'
        assert expiry_days.isdigit()
        assert assigned_at
        assert decision_source == 'fallback_control'

    def test_reuses_variant_across_allowlisted_courses(self):
        user = None
        enrollment_1 = CourseEnrollmentFactory.create(
            course=self.course_1,
            mode=CourseMode.AUDIT,
            is_active=True,
        )
        user = enrollment_1.user
        enrollment_2 = CourseEnrollmentFactory.create(
            user=user,
            course=self.course_2,
            mode=CourseMode.AUDIT,
            is_active=True,
        )

        with override_waffle_flag(AUDIT_EXPIRY_URGENCY_V1_ENABLED, active=True):
            with override_settings(AUDIT_EXPIRY_EXPERIMENT_COURSES=[str(self.course_1.id), str(self.course_2.id)]):
                # First enrollment activates to expiry_7_days.
                client = mock.Mock()
                client.activate.return_value = 'expiry_7_days'
                with mock.patch(
                    'lms.djangoapps.experiments.audit_expiry_urgency.OptimizelyClient.get_optimizely_client',
                    return_value=client,
                ):
                    enrollment_1.save()

                # Second enrollment should not call activate (it should reuse persisted variant)
                client_2 = mock.Mock()
                client_2.activate.return_value = 'control_5_7_weeks'
                with mock.patch(
                    'lms.djangoapps.experiments.audit_expiry_urgency.OptimizelyClient.get_optimizely_client',
                    return_value=client_2,
                ):
                    enrollment_2.save()
                    assert client_2.activate.call_count == 0

        v1 = CourseEnrollmentAttribute.objects.get(
            enrollment=enrollment_1,
            namespace='audit_expiry_experiment',
            name='variant',
        ).value
        v2 = CourseEnrollmentAttribute.objects.get(
            enrollment=enrollment_2,
            namespace='audit_expiry_experiment',
            name='variant',
        ).value
        assert v1 == v2 == 'expiry_7_days'

    def test_idempotent_does_not_overwrite_existing_audit_expiry_at(self):
        enrollment = CourseEnrollmentFactory.create(course=self.course_1, mode=CourseMode.AUDIT, is_active=True)

        # Pre-seed attributes.
        existing_expiry = (timezone.now() + timedelta(days=123)).replace(microsecond=0)
        CourseEnrollmentAttribute.objects.create(
            enrollment=enrollment,
            namespace='audit_expiry_experiment',
            name='audit_expiry_at',
            value=existing_expiry.isoformat(),
        )

        with override_waffle_flag(AUDIT_EXPIRY_URGENCY_V1_ENABLED, active=True):
            with override_settings(AUDIT_EXPIRY_EXPERIMENT_COURSES=[str(self.course_1.id)]):
                client = mock.Mock()
                client.activate.return_value = 'expiry_7_days'
                with mock.patch(
                    'lms.djangoapps.experiments.audit_expiry_urgency.OptimizelyClient.get_optimizely_client',
                    return_value=client,
                ):
                    enrollment.save()
                    assert client.activate.call_count == 0

        persisted = CourseEnrollmentAttribute.objects.filter(
            enrollment=enrollment,
            namespace='audit_expiry_experiment',
            name='audit_expiry_at',
        ).order_by('id').last().value
        assert persisted == existing_expiry.isoformat()

    def test_force_variant_setting_skips_optimizely(self):
        enrollment = CourseEnrollmentFactory.create(course=self.course_1, mode=CourseMode.AUDIT, is_active=True)

        with override_waffle_flag(AUDIT_EXPIRY_URGENCY_V1_ENABLED, active=True):
            with override_settings(
                AUDIT_EXPIRY_EXPERIMENT_COURSES=[str(self.course_1.id)],
                AUDIT_EXPIRY_FORCE_VARIANT='expiry_7_days',
                DEBUG=False,
            ):
                client = mock.Mock()
                client.activate.return_value = 'control_5_7_weeks'
                with mock.patch(
                    'lms.djangoapps.experiments.audit_expiry_urgency.OptimizelyClient.get_optimizely_client',
                    return_value=client,
                ):
                    enrollment.save()
                    assert client.activate.call_count == 0

        variant = CourseEnrollmentAttribute.objects.get(
            enrollment=enrollment,
            namespace='audit_expiry_experiment',
            name='variant',
        ).value
        assert variant == 'expiry_7_days'

        decision_source = CourseEnrollmentAttribute.objects.get(
            enrollment=enrollment,
            namespace='audit_expiry_experiment',
            name='decision_source',
        ).value
        assert decision_source == 'force_variant'

    def test_tracks_exposure_only_for_new_assignment(self):
        enrollment_1 = CourseEnrollmentFactory.create(
            course=self.course_1,
            mode=CourseMode.AUDIT,
            is_active=True,
        )
        enrollment_2 = CourseEnrollmentFactory.create(
            user=enrollment_1.user,
            course=self.course_2,
            mode=CourseMode.AUDIT,
            is_active=True,
        )

        with override_waffle_flag(AUDIT_EXPIRY_URGENCY_V1_ENABLED, active=True):
            with override_settings(AUDIT_EXPIRY_EXPERIMENT_COURSES=[str(self.course_1.id), str(self.course_2.id)]):
                client = mock.Mock()
                client.activate.return_value = 'expiry_7_days'
                with mock.patch(
                    'lms.djangoapps.experiments.audit_expiry_urgency.OptimizelyClient.get_optimizely_client',
                    return_value=client,
                ):
                    enrollment_1.save()
                    enrollment_2.save()

        assert client.track.call_count == 1
        assert client.track.call_args[0][0] == 'audit_expiry_urgency_exposed'

    def test_tracks_verified_conversion_for_experiment_participant(self):
        enrollment = CourseEnrollmentFactory.create(course=self.course_1, mode=CourseMode.AUDIT, is_active=True)

        with override_waffle_flag(AUDIT_EXPIRY_URGENCY_V1_ENABLED, active=True):
            with override_settings(AUDIT_EXPIRY_EXPERIMENT_COURSES=[str(self.course_1.id)]):
                client = mock.Mock()
                client.activate.return_value = 'expiry_7_days'
                with mock.patch(
                    'lms.djangoapps.experiments.audit_expiry_urgency.OptimizelyClient.get_optimizely_client',
                    return_value=client,
                ):
                    enrollment.save()

                conversion_client = mock.Mock()
                with mock.patch(
                    'lms.djangoapps.experiments.signals.OptimizelyClient.get_optimizely_client',
                    return_value=conversion_client,
                ):
                    ENROLLMENT_TRACK_UPDATED.send(
                        sender=None,
                        user=enrollment.user,
                        course_key=enrollment.course_id,
                        mode=CourseMode.VERIFIED,
                    )

        assert conversion_client.track.call_count == 1
        assert conversion_client.track.call_args[0][0] == 'audit_expiry_urgency_upgraded_to_verified'

    def test_does_not_track_verified_conversion_when_not_in_experiment(self):
        enrollment = CourseEnrollmentFactory.create(course=self.course_1, mode=CourseMode.AUDIT, is_active=True)

        with override_waffle_flag(AUDIT_EXPIRY_URGENCY_V1_ENABLED, active=True):
            conversion_client = mock.Mock()
            with mock.patch(
                'lms.djangoapps.experiments.signals.OptimizelyClient.get_optimizely_client',
                return_value=conversion_client,
            ):
                ENROLLMENT_TRACK_UPDATED.send(
                    sender=None,
                    user=enrollment.user,
                    course_key=enrollment.course_id,
                    mode=CourseMode.VERIFIED,
                )

        assert conversion_client.track.call_count == 0
