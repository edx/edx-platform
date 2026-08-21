"""
Tests for the bulk-unenroll Celery engine (lms/djangoapps/support/tasks.py).
"""
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from common.djangoapps.student.models.course_enrollment import (
    ENROLLED_TO_UNENROLLED,
    CourseEnrollment,
    ManualEnrollmentAudit,
)
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.support.models import BulkUnenrollBatch, BulkUnenrollChunk, BulkUnenrollCourseState
from lms.djangoapps.support.tasks import (
    _chunk_stop_reason,
    _finalize_batch_if_complete,
    bulk_unenroll_batch,
    bulk_unenroll_chunk,
    bulk_unenroll_course,
)
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory


class BulkUnenrollTaskTestCase(TestCase):
    """
    One batch holding one course, plus the helpers every engine test needs.
    Subclasses declare the situation under test via the class attributes below.
    """

    batch_state = BulkUnenrollBatch.State.RUNNING
    course_state = BulkUnenrollCourseState.State.PENDING
    chunks_total = 0

    def setUp(self):
        super().setUp()
        self.requester = UserFactory()
        self.course = CourseOverviewFactory.create(org="edX", run="A", display_name="A")
        self.batch = BulkUnenrollBatch.objects.create(
            requester=self.requester, reason="offboarding", total_courses=1,
            state=self.batch_state,
        )
        self.state = BulkUnenrollCourseState.objects.create(
            batch=self.batch, course_id=self.course.id,
            state=self.course_state, chunks_total=self.chunks_total,
        )

    def _enroll(self, count, is_active=True):
        """Create `count` learners enrolled in `self.course`; return them."""
        users = [UserFactory() for _ in range(count)]
        for user in users:
            CourseEnrollment.objects.create(
                user=user, course_id=self.course.id, is_active=is_active,
            )
        return users

    def _active(self):
        return CourseEnrollment.objects.filter(course_id=self.course.id, is_active=True).count()

    def _refresh(self):
        self.state.refresh_from_db()
        self.batch.refresh_from_db()
        return self.state

    def _run_chunk(self, users, chunk_index=0, attempt=1):
        bulk_unenroll_chunk(
            str(self.batch.uuid), str(self.course.id), [u.id for u in users],
            chunk_index, attempt,
        )

    def _timeout_on(self, victim):
        """Patch unenroll so it raises Celery's soft-timeout when it reaches `victim`."""
        real_unenroll = CourseEnrollment.unenroll

        def timing_out(user, course_id, skip_refund=False):
            if user.id == victim.id:
                raise SoftTimeLimitExceeded()
            return real_unenroll(user, course_id, skip_refund=skip_refund)

        return patch.object(CourseEnrollment, "unenroll", side_effect=timing_out)


class BulkUnenrollChunkTaskTest(BulkUnenrollTaskTestCase):
    """The level-3 chunk task: actually deactivates a batch of learners."""

    course_state = BulkUnenrollCourseState.State.RUNNING
    chunks_total = 1

    def test_unenrolls_active_learners_and_writes_audit(self):
        users = self._enroll(3)
        self._run_chunk(users)

        self.assertEqual(self._active(), 0)
        audits = ManualEnrollmentAudit.objects.filter(enrollment__course_id=self.course.id)
        self.assertEqual(audits.count(), 3)
        for audit in audits:
            self.assertEqual(audit.state_transition, ENROLLED_TO_UNENROLLED)
            self.assertEqual(audit.reason, "offboarding")
            self.assertEqual(audit.enrolled_by, self.requester)
        self.assertEqual(self._refresh().unenrolled, 3)

    def test_skip_refund_true_is_passed_to_unenroll(self):
        """Refunds are never issued for a bulk run."""
        users = self._enroll(2)
        real_unenroll = CourseEnrollment.unenroll
        seen = []

        def spy(user, course_id, skip_refund=False):
            seen.append(skip_refund)
            return real_unenroll(user, course_id, skip_refund=skip_refund)

        with patch.object(CourseEnrollment, "unenroll", side_effect=spy):
            self._run_chunk(users)

        self.assertEqual(seen, [True, True])
        self.assertEqual(self._active(), 0)

    def test_one_failing_learner_does_not_abort_the_chunk(self):
        users = self._enroll(3)
        bad = users[1]
        real_unenroll = CourseEnrollment.unenroll

        def flaky(user, course_id, skip_refund=False):
            if user.id == bad.id:
                raise ValueError("boom")
            return real_unenroll(user, course_id, skip_refund=skip_refund)

        with patch.object(CourseEnrollment, "unenroll", side_effect=flaky):
            self._run_chunk(users)

        state = self._refresh()
        self.assertEqual(state.unenrolled, 2)
        self.assertEqual(state.failed_count, 1)
        self.assertEqual(state.state, "failed")
        self.assertTrue(
            CourseEnrollment.objects.get(user=bad, course_id=self.course.id).is_active
        )

    def test_already_inactive_learners_counted_not_unenrolled(self):
        """The is_active filter is what makes every level of the engine re-runnable."""
        users = self._enroll(2, is_active=False)
        self._run_chunk(users)

        state = self._refresh()
        self.assertEqual(state.unenrolled, 0)
        self.assertEqual(state.already_inactive, 2)
        self.assertEqual(ManualEnrollmentAudit.objects.count(), 0)

    def test_duplicate_delivery_of_one_chunk_reports_in_exactly_once(self):
        """
        Otherwise a redelivery pushes chunks_finished to chunks_total while a
        different chunk has never run, succeeding a course with learners enrolled.
        """
        self.state.chunks_total = 2      # chunk 1 will never run in this test
        self.state.save()
        users = self._enroll(2)
        self._run_chunk(users, chunk_index=0)
        self._run_chunk(users, chunk_index=0)  # duplicate delivery of the SAME chunk

        state = self._refresh()
        self.assertEqual(state.chunks_finished, 1)
        self.assertEqual(state.unenrolled, 2)
        self.assertEqual(state.already_inactive, 0)   # the redelivery's tally is discarded
        self.assertEqual(state.state, "running")      # not finalized: chunk 1 is outstanding
        self.assertIsNone(state.finished)

    def test_a_failed_counter_update_leaves_the_chunk_unclaimed(self):
        """
        A claim without its tally would leave the chunk finished but uncounted, and
        every redelivery would then discard itself as a duplicate — course wedged.
        """
        users = self._enroll(2)
        boom = Exception("counter update died")
        real_filter = BulkUnenrollCourseState.objects.filter

        def fail_on_counter_update(*args, **kwargs):
            queryset = real_filter(*args, **kwargs)
            original_update = queryset.update

            def exploding_update(**fields):
                if "chunks_finished" in fields:
                    raise boom
                return original_update(**fields)

            queryset.update = exploding_update
            return queryset

        with patch.object(BulkUnenrollCourseState.objects, "filter", side_effect=fail_on_counter_update):
            with self.assertRaises(Exception):
                self._run_chunk(users, chunk_index=0)

        # The ledger must not remember a chunk whose tally was never recorded.
        self.assertFalse(
            BulkUnenrollChunk.objects.filter(
                course_state=self.state, chunk_index=0,
                state=BulkUnenrollChunk.State.FINISHED,
            ).exists()
        )
        # ...so a redelivery can still count it.
        self._run_chunk(users, chunk_index=0)
        state = self._refresh()
        self.assertEqual(state.chunks_finished, 1)
        self.assertEqual(state.already_inactive, 2)   # they were removed by the failed run

    def test_chunk_from_a_superseded_attempt_is_discarded(self):
        """
        Its learner set is stale, and claiming chunk 0 of the new attempt would get
        the real chunk dropped as a duplicate.
        """
        users = self._enroll(2)
        BulkUnenrollCourseState.objects.filter(pk=self.state.pk).update(attempt=2)

        self._run_chunk(users, chunk_index=0, attempt=1)   # superseded generation

        self.assertEqual(self._active(), 2)
        self.assertEqual(self._refresh().chunks_finished, 0)
        self.assertFalse(BulkUnenrollChunk.objects.filter(course_state=self.state).exists())

    def test_attempt_bumped_while_the_chunk_runs_discards_its_tally(self):
        """
        The attempt is checked before the work and acted on after it, so a retry can
        land in between — its counters belong to a different fan-out.
        """
        users = self._enroll(2)
        real_unenroll = CourseEnrollment.unenroll

        def retry_lands_mid_chunk(user, course_id, skip_refund=False):
            BulkUnenrollCourseState.objects.filter(pk=self.state.pk).update(attempt=2)
            return real_unenroll(user, course_id, skip_refund=skip_refund)

        with patch.object(CourseEnrollment, "unenroll", side_effect=retry_lands_mid_chunk):
            self._run_chunk(users, chunk_index=0, attempt=1)

        state = self._refresh()
        self.assertEqual(state.chunks_finished, 0)   # nothing counted for attempt 2
        self.assertEqual(state.unenrolled, 0)
        self.assertEqual(state.state, "running")

    def test_soft_time_limit_hands_on_the_tail_without_failing_the_course(self):
        """
        A timeout is not a bad learner, and the chain it starts counts as finished
        only when its last continuation reports — chunks_total stays the fan-out's.
        """
        users = self._enroll(3)
        with self._timeout_on(users[1]):
            with patch("lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async") as mock_apply:
                self._run_chunk(users, chunk_index=0)

        state = self._refresh()
        self.assertEqual(state.failed_count, 0)        # a timeout is not a learner failure
        self.assertEqual(state.chunks_total, 1)        # only the fan-out writes the denominator
        self.assertEqual(state.chunks_finished, 0)     # the chain has not finished yet
        self.assertEqual(state.state, "running")
        self.assertIsNone(state.finished)

        args = mock_apply.call_args.kwargs["args"]
        still_active = set(
            CourseEnrollment.objects
            .filter(course_id=self.course.id, is_active=True)
            .values_list("user_id", flat=True)
        )
        self.assertEqual(set(args[2]), still_active)   # exactly the learners not yet done
        # Numbered within its own chunk: a fresh fan-out index would collide with a
        # sibling that is queued but has not reported yet.
        self.assertEqual(args[3], 0)                   # same chunk_index...
        self.assertEqual(args[5], 1)                   # ...next continuation

    def test_timeout_reporting_before_the_fan_out_records_totals_does_not_finalize(self):
        """
        A chunk can time out while the fan-out is still streaming a huge course, so
        its report can land while chunks_total is still 0. Counting the tail into
        the denominator there would finalize the course at 1/1 — reported succeeded
        with real chunks still being queued, all then discarded as settled.
        """
        BulkUnenrollCourseState.objects.filter(pk=self.state.pk).update(chunks_total=0)
        users = self._enroll(3)
        with self._timeout_on(users[1]):
            with patch("lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async"):
                self._run_chunk(users, chunk_index=0)

        state = self._refresh()
        self.assertEqual(state.state, "running")     # not finalized out from under the fan-out
        self.assertEqual(state.chunks_total, 0)
        self.assertEqual(state.chunks_finished, 0)

    def test_course_finalizes_only_when_the_last_chunk_reports(self):
        self.state.chunks_total = 2
        self.state.save()
        first, second = self._enroll(1), self._enroll(1)

        self._run_chunk(first, chunk_index=0)
        state = self._refresh()
        self.assertEqual(state.chunks_finished, 1)
        self.assertEqual(state.state, "running")       # one chunk still outstanding
        self.assertIsNone(state.finished)

        self._run_chunk(second, chunk_index=1)
        state = self._refresh()
        self.assertEqual(state.chunks_finished, 2)
        self.assertEqual(state.unenrolled, 2)          # F() accumulation, no clobbering
        self.assertEqual(state.state, "succeeded")
        self.assertIsNotNone(state.finished)


class BulkUnenrollCourseTaskTest(BulkUnenrollTaskTestCase):
    """The level-2 per-course task: fetches enrollments and fans out chunks."""

    def _run(self):
        bulk_unenroll_course(str(self.batch.uuid), str(self.course.id))

    @override_settings(BULK_UNENROLL_CHUNK_SIZE=2)
    def test_splits_active_enrollments_into_chunks(self):
        self._enroll(5)
        with patch("lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async") as mock_apply:
            self._run()

        self.assertEqual(mock_apply.call_count, 3)                      # 2 + 2 + 1
        sizes = sorted(len(c.kwargs["args"][2]) for c in mock_apply.call_args_list)
        self.assertEqual(sizes, [1, 2, 2])
        state = self._refresh()
        self.assertEqual(state.total_enrollments, 5)
        self.assertEqual(state.chunks_total, 3)
        self.assertEqual(state.state, "running")
        self.assertIsNotNone(state.started)

    @override_settings(BULK_UNENROLL_CHUNK_SIZE=2)
    def test_only_active_enrollments_are_queued(self):
        self._enroll(3, is_active=True)
        self._enroll(2, is_active=False)
        with patch("lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async") as mock_apply:
            self._run()

        queued = sum(len(c.kwargs["args"][2]) for c in mock_apply.call_args_list)
        self.assertEqual(queued, 3)
        self.assertEqual(self._refresh().total_enrollments, 3)

    def test_empty_course_finalizes_succeeded_without_queuing(self):
        with patch("lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async") as mock_apply:
            self._run()

        mock_apply.assert_not_called()
        state = self._refresh()
        self.assertEqual(state.chunks_total, 0)
        self.assertEqual(state.state, "succeeded")
        self.assertIsNotNone(state.finished)

    def test_an_empty_course_cancelled_mid_fan_out_is_not_finalized_succeeded(self):
        """A cancel landing after the claim has already settled the course."""
        real_values_list = BulkUnenrollCourseState.objects.values_list

        def cancel_lands_after_the_claim(*args, **kwargs):
            BulkUnenrollCourseState.objects.filter(pk=self.state.pk).update(
                state=BulkUnenrollCourseState.State.CANCELLED,
            )
            return real_values_list(*args, **kwargs)

        with patch.object(
            BulkUnenrollCourseState.objects, "values_list",
            side_effect=cancel_lands_after_the_claim,
        ):
            self._run()

        state = self._refresh()
        self.assertEqual(state.state, "cancelled")   # not resurrected as succeeded
        self.assertIsNone(state.finished)

    @override_settings(BULK_UNENROLL_CHUNK_SIZE=2)
    def test_duplicate_course_delivery_fans_out_once(self):
        """
        A second chunk set would report in against the same chunks_total and
        finalize the course before the first set had finished.
        """
        self._enroll(5)
        with patch("lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async") as mock_apply:
            self._run()
            self._run()                                # duplicate delivery

        self.assertEqual(mock_apply.call_count, 3)     # 2 + 2 + 1, queued once
        self.assertEqual(self._refresh().chunks_total, 3)

    def test_query_count_independent_of_enrollment_count(self):
        """The worker streams user ids; it must not query per enrollment."""
        def run_for(count):
            course = CourseOverviewFactory.create(org="edX", run=f"R{count}", display_name=f"C{count}")
            batch = BulkUnenrollBatch.objects.create(
                requester=self.requester, total_courses=1, state=BulkUnenrollBatch.State.RUNNING,
            )
            BulkUnenrollCourseState.objects.create(batch=batch, course_id=course.id)
            for _ in range(count):
                CourseEnrollment.objects.create(user=UserFactory(), course_id=course.id, is_active=True)
            with patch("lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async"):
                with CaptureQueriesContext(connection) as ctx:
                    bulk_unenroll_course(str(batch.uuid), str(course.id))
            return len(ctx.captured_queries)

        run_for(2)                                   # warm caches
        self.assertEqual(run_for(3), run_for(30))


class BulkUnenrollBatchDispatcherTest(TestCase):
    """The level-1 dispatcher + the batch-level finalizer."""

    def setUp(self):
        super().setUp()
        self.requester = UserFactory()

    def _batch_with_courses(self, count, course_state=BulkUnenrollCourseState.State.PENDING):
        """Build a pending batch with `count` courses; return (batch, courses)."""
        batch = BulkUnenrollBatch.objects.create(
            requester=self.requester, total_courses=count, state=BulkUnenrollBatch.State.PENDING,
        )
        courses = []
        for i in range(count):
            course = CourseOverviewFactory.create(org="edX", run=f"D{i}", display_name=f"D{i}")
            BulkUnenrollCourseState.objects.create(batch=batch, course_id=course.id, state=course_state)
            courses.append(course)
        return batch, courses

    def _finalize_states(self, course_states):
        """Build a running batch whose courses already sit in `course_states`."""
        batch = BulkUnenrollBatch.objects.create(
            requester=self.requester, total_courses=len(course_states),
            state=BulkUnenrollBatch.State.RUNNING,
        )
        for i, cstate in enumerate(course_states):
            course = CourseOverviewFactory.create(org="edX", run=f"F{i}", display_name=f"F{i}")
            BulkUnenrollCourseState.objects.create(batch=batch, course_id=course.id, state=cstate)
        _finalize_batch_if_complete(batch.pk)
        batch.refresh_from_db()
        return batch.state

    def test_marks_running_and_queues_one_task_per_course(self):
        batch, courses = self._batch_with_courses(3)
        with patch("lms.djangoapps.support.tasks.bulk_unenroll_course.apply_async") as mock_apply:
            bulk_unenroll_batch(str(batch.uuid))

        self.assertEqual(mock_apply.call_count, 3)
        queued = sorted(c.kwargs["args"][1] for c in mock_apply.call_args_list)
        self.assertEqual(queued, sorted(str(co.id) for co in courses))
        batch.refresh_from_db()
        self.assertEqual(batch.state, "running")

    def test_duplicate_dispatcher_delivery_fans_each_course_out_once(self):
        """
        Re-queuing a course task is cheap and is how a half-finished fan-out
        resumes; fanning one out into chunks twice is not.
        """
        batch, _ = self._batch_with_courses(3)
        for course_state in batch.courses.all():
            CourseEnrollment.objects.create(
                user=UserFactory(), course_id=course_state.course_id, is_active=True,
            )

        course_args = []
        with patch(
            "lms.djangoapps.support.tasks.bulk_unenroll_course.apply_async",
            side_effect=lambda *a, **k: course_args.append(k["args"]),
        ):
            bulk_unenroll_batch(str(batch.uuid))
            bulk_unenroll_batch(str(batch.uuid))      # duplicate delivery

        chunk_args = []
        with patch(
            "lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async",
            side_effect=lambda *a, **k: chunk_args.append(k["args"]),
        ):
            for args in course_args:
                bulk_unenroll_course(*args)

        self.assertEqual(len(chunk_args), 3)   # one per course, however many deliveries

    def test_dispatcher_redelivery_resumes_a_half_finished_fan_out(self):
        """
        A worker that dies mid-queue leaves the batch 'running' with courses
        'pending'; an exclusive claim would strand them forever.
        """
        batch, _ = self._batch_with_courses(3)
        # Simulate a fan-out that got one course out before dying.
        BulkUnenrollBatch.objects.filter(pk=batch.pk).update(state=BulkUnenrollBatch.State.RUNNING)
        first = batch.courses.order_by("pk").first()
        BulkUnenrollCourseState.objects.filter(pk=first.pk).update(
            state=BulkUnenrollCourseState.State.RUNNING,
        )

        with patch("lms.djangoapps.support.tasks.bulk_unenroll_course.apply_async") as mock_apply:
            bulk_unenroll_batch(str(batch.uuid))

        queued = sorted(c.kwargs["args"][1] for c in mock_apply.call_args_list)
        still_pending = sorted(
            str(cs.course_id)
            for cs in batch.courses.filter(state=BulkUnenrollCourseState.State.PENDING)
        )
        self.assertEqual(queued, still_pending)   # exactly the unfinished work
        self.assertEqual(len(queued), 2)

    def test_batch_state_is_derived_from_its_courses(self):
        """Succeeded when none failed, failed when none succeeded, partial in between."""
        for course_states, expected in [
            (["succeeded", "succeeded"], "succeeded"),
            (["failed", "failed"], "failed"),
            (["succeeded", "failed"], "partial"),
            (["succeeded", "pending"], "running"),   # not settled while work remains
        ]:
            with self.subTest(course_states=course_states):
                self.assertEqual(self._finalize_states(course_states), expected)

    def test_dispatch_end_to_end_unenrolls_all_and_finalizes(self):
        batch, courses = self._batch_with_courses(2)
        for course in courses:
            for _ in range(2):
                CourseEnrollment.objects.create(user=UserFactory(), course_id=course.id, is_active=True)

        course_args = []
        with patch(
            "lms.djangoapps.support.tasks.bulk_unenroll_course.apply_async",
            side_effect=lambda *a, **k: course_args.append(k["args"]),
        ):
            bulk_unenroll_batch(str(batch.uuid))

        for cargs in course_args:
            chunk_args = []
            with patch(
                "lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async",
                side_effect=lambda *a, _sink=chunk_args, **k: _sink.append(k["args"]),
            ):
                bulk_unenroll_course(*cargs)
            for chargs in chunk_args:
                bulk_unenroll_chunk(*chargs)

        batch.refresh_from_db()
        self.assertEqual(batch.state, "succeeded")
        self.assertEqual(
            CourseEnrollment.objects.filter(
                course_id__in=[c.id for c in courses], is_active=True,
            ).count(),
            0,
        )


class BulkUnenrollCancellationTest(BulkUnenrollTaskTestCase):
    """Cancellation is honoured by the dispatcher and the chunk worker."""

    batch_state = BulkUnenrollBatch.State.CANCELLED
    chunks_total = 1

    def test_chunk_removes_nobody_when_batch_already_cancelled(self):
        self._run_chunk(self._enroll(3))
        self.assertEqual(self._active(), 3)
        self.assertEqual(self._refresh().unenrolled, 0)

    @override_settings(BULK_UNENROLL_CANCEL_CHECK_EVERY=2)
    def test_chunk_stops_partway_when_cancelled_midway(self):
        users = self._enroll(4)
        # Start check clear (proceed), then cancelled on the check after learner 2.
        with patch(
            "lms.djangoapps.support.tasks._chunk_stop_reason",
            side_effect=[None, "the batch was cancelled"],
        ):
            self._run_chunk(users)
        self.assertEqual(4 - self._active(), 2)                 # stopped after the 2nd

    @override_settings(BULK_UNENROLL_CANCEL_CHECK_EVERY=0)
    def test_a_zero_check_interval_still_honours_a_cancel_mid_chunk(self):
        """
        0 must degrade to checking every learner, never to checking none: the check
        bounds how many more learners a revoked chunk can still unenroll (and a
        divide-by-zero would escape uncaught, leaving the chunk never recorded).
        """
        users = self._enroll(4)
        with patch(
            "lms.djangoapps.support.tasks._chunk_stop_reason",
            side_effect=[None, "the batch was cancelled"],
        ):
            self._run_chunk(users)
        self.assertEqual(4 - self._active(), 1)                 # stopped after the 1st

    def test_dispatcher_queues_nothing_and_stays_cancelled(self):
        with patch("lms.djangoapps.support.tasks.bulk_unenroll_course.apply_async") as mock_apply:
            bulk_unenroll_batch(str(self.batch.uuid))
        mock_apply.assert_not_called()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.state, "cancelled")


class BulkUnenrollBrokerFailureTest(BulkUnenrollTaskTestCase):
    """
    What happens when the broker, not the work, is what breaks.

    Both levels publish *after* claiming their work, so a publish that never lands
    leaves the course waiting on chunks that will never run — and the claim is what
    stops a redelivery repairing it. The engine has to settle the course itself, or
    the batch never reaches the settled state the retry endpoint requires.
    """

    def _second_course(self):
        """Add a second course to self.batch and return its state row."""
        course = CourseOverviewFactory.create(org="edX", run="B", display_name="B")
        self.batch.total_courses = 2
        self.batch.save()
        return BulkUnenrollCourseState.objects.create(batch=self.batch, course_id=course.id)

    @override_settings(BULK_UNENROLL_CHUNK_SIZE=2)
    def test_fan_out_that_dies_midway_fails_the_course_and_settles_the_batch(self):
        """
        The course was claimed 'running' before the first publish, so a redelivery
        skips it and nothing can ever record chunks_total.
        """
        self._enroll(5)                                     # 3 chunks at size 2
        with patch(
            "lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async",
            side_effect=[None, OSError("broker gone"), None],
        ):
            with self.assertRaises(OSError):
                bulk_unenroll_course(str(self.batch.uuid), str(self.course.id))

        state = self._refresh()
        self.assertEqual(state.state, "failed")
        self.assertIn("Could not queue", state.error)
        self.assertIsNotNone(state.finished)
        # chunks_total was never recorded, so no chunk count can finalize this course.
        self.assertEqual(state.chunks_total, 0)
        self.assertEqual(self.batch.state, "failed")        # settled, so retry can reach it

    @override_settings(BULK_UNENROLL_CHUNK_SIZE=2)
    def test_chunks_already_queued_stop_once_the_broken_fan_out_fails_the_course(self):
        """
        The chunks the fan-out *did* publish are still on the queue. They must not
        deactivate enrollments after the API has reported the batch finished.
        """
        users = self._enroll(4)
        with patch(
            "lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async",
            side_effect=[None, OSError("broker gone")],
        ):
            with self.assertRaises(OSError):
                bulk_unenroll_course(str(self.batch.uuid), str(self.course.id))

        self.assertEqual(self._refresh().state, "failed")
        self.assertEqual(self.batch.state, "failed")

        # Now deliver the chunk that really was published, as a worker would.
        self._run_chunk(users[:2], chunk_index=0)
        self.assertEqual(self._active(), 4)                 # nobody removed post-settlement
        self.assertEqual(self._refresh().unenrolled, 0)

    def test_a_chunk_stops_mid_pass_when_a_retry_supersedes_its_attempt(self):
        """
        Left running, the old chunk keeps unenrolling into a generation whose
        counters no longer accept its tally — work done, never reported.
        """
        BulkUnenrollCourseState.objects.filter(pk=self.state.pk).update(
            state=BulkUnenrollCourseState.State.RUNNING, chunks_total=1,
        )
        users = self._enroll(4)
        state_pk = self.state.pk
        real_stop_reason = _chunk_stop_reason
        calls = []

        def bump_attempt_after_first_check(pk, attempt):
            calls.append(pk)
            reason = real_stop_reason(pk, attempt)
            if len(calls) == 1:
                # The up-front check has passed; a retry now moves the course to a
                # new generation, and the next mid-pass check has to notice.
                BulkUnenrollCourseState.objects.filter(pk=state_pk).update(attempt=2)
            return reason

        with override_settings(BULK_UNENROLL_CANCEL_CHECK_EVERY=2):
            with patch(
                "lms.djangoapps.support.tasks._chunk_stop_reason",
                side_effect=bump_attempt_after_first_check,
            ):
                self._run_chunk(users, chunk_index=0)

        self.assertEqual(4 - self._active(), 2)             # stopped at the first check
        # Its tally is discarded anyway, so every extra learner is unreported work.
        self.assertEqual(self._refresh().unenrolled, 0)

    def test_continuation_that_cannot_be_queued_fails_the_course(self):
        """
        This chunk's ledger row is 'finished', so no redelivery will re-queue the
        tail — a chain whose last link never runs can never report finished.
        """
        BulkUnenrollCourseState.objects.filter(pk=self.state.pk).update(
            state=BulkUnenrollCourseState.State.RUNNING, chunks_total=1,
        )
        users = self._enroll(3)

        with self._timeout_on(users[1]):
            with patch(
                "lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async",
                side_effect=OSError("broker gone"),
            ):
                with self.assertRaises(OSError):
                    self._run_chunk(users, chunk_index=0)

        state = self._refresh()
        self.assertEqual(state.state, "failed")
        self.assertIn("Could not queue", state.error)
        self.assertEqual(self.batch.state, "failed")
        # The chain never reported finished; only the failure releases the course.
        self.assertEqual(state.chunks_total, 1)
        self.assertEqual(state.chunks_finished, 0)

    def test_dispatcher_that_cannot_queue_fails_the_courses_it_never_reached(self):
        """
        The dispatcher is acked on delivery with no retry, so a course it never
        queued would sit 'pending' forever and its batch would never settle.
        """
        second = self._second_course()
        with patch(
            "lms.djangoapps.support.tasks.bulk_unenroll_course.apply_async",
            side_effect=OSError("broker gone"),
        ):
            with self.assertRaises(OSError):
                bulk_unenroll_batch(str(self.batch.uuid))

        state = self._refresh()
        second.refresh_from_db()
        self.assertEqual(state.state, "failed")
        self.assertEqual(second.state, "failed")
        self.assertIn("Could not queue", state.error)
        self.assertEqual(self.batch.state, "failed")            # settled, so retry can reach it

    def test_dispatcher_failure_leaves_the_courses_it_did_queue_alone(self):
        """
        A published course stays 'pending' until a worker claims it, so failing
        every pending row would discard work already on the queue.
        """
        self._second_course()
        with patch(
            "lms.djangoapps.support.tasks.bulk_unenroll_course.apply_async",
            side_effect=[None, OSError("broker gone")],
        ):
            with self.assertRaises(OSError):
                bulk_unenroll_batch(str(self.batch.uuid))

        states = sorted(self.batch.courses.values_list("state", flat=True))
        self.assertEqual(states, ["failed", "pending"])         # the queued one is untouched
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.state, "running")           # its worker has yet to run

    def test_a_course_claimed_between_the_failure_and_the_cleanup_is_not_clobbered(self):
        """The cleanup is conditional on 'pending', so a worker mid-claim wins."""
        second = self._second_course()
        second.state = BulkUnenrollCourseState.State.RUNNING     # a worker got there first
        second.save()

        with patch(
            "lms.djangoapps.support.tasks.bulk_unenroll_course.apply_async",
            side_effect=OSError("broker gone"),
        ):
            with self.assertRaises(OSError):
                bulk_unenroll_batch(str(self.batch.uuid))

        second.refresh_from_db()
        self.assertEqual(second.state, "running")
        self.assertEqual(self._refresh().state, "failed")


class BulkUnenrollModifiedTimestampTest(BulkUnenrollTaskTestCase):
    """
    `modified` must track the lifecycle, not just the calls that happen to save().

    TimeStampedModel maintains it in `save()`, so every queryset `.update()` leaves
    it behind — and it is what an operator reads to answer "is this run moving?".
    """

    batch_state = BulkUnenrollBatch.State.PENDING

    def test_dispatch_and_claim_advance_the_modified_timestamps(self):
        batch_before = BulkUnenrollBatch.objects.values_list("modified", flat=True).get(pk=self.batch.pk)
        state_before = BulkUnenrollCourseState.objects.values_list("modified", flat=True).get(pk=self.state.pk)

        with patch("lms.djangoapps.support.tasks.bulk_unenroll_course.apply_async"):
            bulk_unenroll_batch(str(self.batch.uuid))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.state, "running")
        self.assertGreater(self.batch.modified, batch_before)

        # The course claim is a queryset .update() too, so it needs the same care.
        self._enroll(1)
        with patch("lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async"):
            bulk_unenroll_course(str(self.batch.uuid), str(self.course.id))
        self.state.refresh_from_db()
        self.assertGreater(self.state.modified, state_before)


class BulkUnenrollContinuationIdentityTest(BulkUnenrollTaskTestCase):
    """
    A timed-out chunk's continuation must not take an identity a queued chunk owns.

    A ledger row only appears when a chunk *reports*, so while chunks are in flight
    the ledger is not a record of what has been handed out: deriving "the next free
    index" from it would hand the tail an index a real chunk is already carrying.
    The second to report is then dropped as a duplicate and the course never finishes.
    """

    course_state = BulkUnenrollCourseState.State.RUNNING
    chunks_total = 2      # a fan-out that queued two chunks: 0 and 1 are spoken for

    def test_timed_out_chunk_and_its_sibling_both_get_counted(self):
        """
        Chunk 0 times out while chunk 1 is still queued: every learner in both must
        end up unenrolled *and* counted, and the course must finalize.
        """
        chunk_zero = self._enroll(3)
        chunk_one = self._enroll(2)

        with self._timeout_on(chunk_zero[1]):
            with patch("lms.djangoapps.support.tasks.bulk_unenroll_chunk.apply_async") as mock_apply:
                self._run_chunk(chunk_zero, chunk_index=0)
        self.assertEqual(mock_apply.call_count, 1)
        continuation_args = mock_apply.call_args.kwargs["args"]

        self._run_chunk(chunk_one, chunk_index=1)
        bulk_unenroll_chunk(*continuation_args)

        self.assertEqual(self._active(), 0)
        state = self._refresh()
        self.assertEqual(state.unenrolled, 5)
        self.assertEqual(state.chunks_total, 2)      # the fan-out's number, tails and all
        self.assertEqual(state.chunks_finished, 2)   # chunk 0's chain counted once, at its end
        self.assertEqual(state.state, "succeeded")
