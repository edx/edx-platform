"""
Tests for support app models.
"""
from django.db import IntegrityError
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.support.models import BulkUnenrollBatch, BulkUnenrollChunk, BulkUnenrollCourseState

COURSE_ID = CourseKey.from_string("course-v1:edX+DemoX+2024")


class BulkUnenrollModelsTest(TestCase):
    """
    The uniqueness constraints the engine's claims are built on.

    Everything else about these models (field defaults, reverse accessors) is
    Django's own behaviour and is exercised by the task and API tests instead.
    """

    def setUp(self):
        super().setUp()
        self.requester = UserFactory.create()

    def _batch(self):
        return BulkUnenrollBatch.objects.create(requester=self.requester)

    def _course_state(self, batch=None, course=COURSE_ID):
        return BulkUnenrollCourseState.objects.create(
            batch=batch or self._batch(), course_id=course,
        )

    def test_a_course_appears_at_most_once_per_batch(self):
        """One row per course per batch — the unit the whole engine addresses."""
        batch = self._batch()
        self._course_state(batch=batch)
        with self.assertRaises(IntegrityError):
            self._course_state(batch=batch)

    def test_the_same_course_may_appear_in_other_batches(self):
        """The constraint is per batch: re-running a course later must stay possible."""
        self._course_state(batch=self._batch())
        self._course_state(batch=self._batch())
        assert BulkUnenrollCourseState.objects.filter(course_id=COURSE_ID).count() == 2

    def test_chunk_identity_is_unique_within_a_course(self):
        """The uniqueness that makes the completion claim a claim."""
        course_state = self._course_state()
        BulkUnenrollChunk.objects.create(course_state=course_state, chunk_index=0)
        with self.assertRaises(IntegrityError):
            BulkUnenrollChunk.objects.create(course_state=course_state, chunk_index=0)

    def test_a_retry_or_continuation_gets_a_fresh_identity(self):
        """
        attempt and continuation are part of the key, so a re-run's chunk 0 and a
        timed-out chunk's tail never collide with the row already in the ledger.
        """
        course_state = self._course_state()
        for attempt, continuation in [(1, 0), (1, 1), (2, 0)]:
            BulkUnenrollChunk.objects.create(
                course_state=course_state, chunk_index=0,
                attempt=attempt, continuation=continuation,
            )
        assert course_state.chunks.count() == 3
