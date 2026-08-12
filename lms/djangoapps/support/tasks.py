""" Celery Tasks for the Instructor App. """

from datetime import datetime
import logging
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from completion.models import BlockCompletion
from edx_django_utils.monitoring import set_code_owner_attribute

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models.course_enrollment import (
    ENROLLED_TO_UNENROLLED,
    CourseEnrollment,
    ManualEnrollmentAudit,
)
from common.djangoapps.student.models.user import get_user_by_username_or_email
from lms.djangoapps.support.models import BulkUnenrollBatch, BulkUnenrollChunk, BulkUnenrollCourseState
from lms.djangoapps.courseware.courses import get_course
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.instructor.enrollment import reset_student_attempts
from lms.djangoapps.support.models import CourseResetAudit
from lms.djangoapps.grades.api import clear_user_course_grades
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context

from edx_ace import ace
from django.contrib.sites.models import Site
from edx_ace.recipient import Recipient
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from lms.djangoapps.support.message_types import WholeCourseReset

log = logging.getLogger(__name__)

User = get_user_model()


@shared_task(
    rate_limit=settings.BULK_UNENROLL_CHUNK_RATE_LIMIT,
    soft_time_limit=settings.BULK_UNENROLL_SOFT_TIME_LIMIT,
)
@set_code_owner_attribute
def bulk_unenroll_chunk(batch_uuid, course_id, user_ids, chunk_index, attempt, continuation=0):
    """
    Deactivate a chunk of learners in one course — level 3, the only place
    enrollments are mutated.

    Unenrolls via ``CourseEnrollment.unenroll(skip_refund=True)`` under
    ``select_for_update`` on the *active* enrollment, writes a
    ``ManualEnrollmentAudit`` per learner, and tallies the outcomes. Re-running is
    safe: an already-inactive learner counts as ``already_inactive``, and one bad
    learner never aborts the chunk. ``(attempt, chunk_index, continuation)`` is the
    chunk's identity; the *accounting* is claimed exactly once by
    ``_record_chunk_completion``. Work stops as soon as ``_chunk_stop_reason``
    reports a revocation (checked up front and every ``check_every`` learners).
    """
    batch = BulkUnenrollBatch.objects.get(uuid=batch_uuid)
    course_key = CourseKey.from_string(course_id)
    state = BulkUnenrollCourseState.objects.get(batch=batch, course_id=course_key)

    # Hard stop before touching anything: cancelled, superseded by a retry, or a
    # course that has already settled (see _chunk_stop_reason).
    stop_reason = _chunk_stop_reason(state.pk, attempt)
    if stop_reason:
        log.info(
            "bulk_unenroll_chunk: discarding chunk %s of course %s (batch %s) because "
            "%s",
            chunk_index, course_key, batch_uuid, stop_reason,
        )
        return

    # A non-positive interval falls back to checking every learner: never divide
    # by zero, and never disable the checks that bound a revoked chunk's overrun.
    check_every = max(1, settings.BULK_UNENROLL_CANCEL_CHECK_EVERY or 1)
    processed_ids = []
    unenrolled = already_inactive = failed = 0
    remaining = None
    try:
        for user in User.objects.filter(id__in=user_ids).iterator():
            try:
                with transaction.atomic():
                    enrollment = CourseEnrollment.objects.select_for_update().get(
                        user=user, course_id=course_key, is_active=True,
                    )
                    CourseEnrollment.unenroll(user, course_key, skip_refund=True)
                    ManualEnrollmentAudit.create_manual_enrollment_audit(
                        batch.requester, user.email, ENROLLED_TO_UNENROLLED,
                        reason=batch.reason, enrollment=enrollment,
                    )
                unenrolled += 1
            except CourseEnrollment.DoesNotExist:
                # Already inactive — someone else got there first, or this is a retry.
                already_inactive += 1
            except SoftTimeLimitExceeded:
                # The chunk is out of time; this is not a bad learner. Re-raised so
                # the handler below can hand the untouched tail to a fresh chunk.
                raise
            except Exception:  # pylint: disable=broad-except
                # Never let one learner kill the chunk; record and move on.
                failed += 1
                log.exception(
                    "bulk_unenroll_chunk: failed to unenroll user %s in course %s (batch %s)",
                    user.id, course_key, batch_uuid,
                )

            processed_ids.append(user.id)
            # Honour a mid-chunk revocation (cancel / retry / course failed)
            # without a DB check per learner.
            if len(processed_ids) % check_every == 0 and _chunk_stop_reason(state.pk, attempt):
                break
    except SoftTimeLimitExceeded:
        done = set(processed_ids)
        remaining = [user_id for user_id in user_ids if user_id not in done]
        log.warning(
            "bulk_unenroll_chunk: soft time limit hit in chunk %s of course %s (batch %s); "
            "re-queueing %s learner(s)",
            chunk_index, course_key, batch_uuid, len(remaining),
        )

    _record_chunk_completion(
        state.pk, attempt, chunk_index, continuation, unenrolled, already_inactive, failed,
        tail=(batch_uuid, course_id, remaining) if remaining else None,
    )


def _record_chunk_completion(
    state_pk, attempt, chunk_index, continuation, unenrolled, already_inactive, failed, tail=None,
):
    """
    Claim this chunk's completion, then roll its tally into the course counters.

    A conditional ``pending -> finished`` update on the chunk's durable row means
    exactly one delivery is ever counted; a duplicate is dropped here rather than
    inflating the counters or letting ``chunks_finished`` reach ``chunks_total``
    with real chunks outstanding. Claim and tally commit together — a claim without
    its counters would wedge the course ``running`` forever.

    ``tail`` is ``(batch_uuid, course_id, user_ids)`` when a soft-time-limit left a
    remainder. It is queued as ``continuation + 1`` of *this* chunk (never a fresh
    fan-out index, which would collide with a chunk still in flight) and only by the
    delivery that won the claim. A chunk that handed on a tail does not count as
    finished — its chain reports once, via its last continuation — so ``chunks_total``
    stays the fan-out's number and is never written here. The course row is locked
    and its ``attempt`` re-checked, since a retry can land after the caller's check.
    """
    finalized_batch_pk = None
    with transaction.atomic():
        state = BulkUnenrollCourseState.objects.select_for_update().get(pk=state_pk)
        if state.attempt != attempt:
            log.info(
                "bulk_unenroll_chunk: course-state %s moved to attempt %s while chunk %s "
                "of attempt %s was running; discarding its tally",
                state_pk, state.attempt, chunk_index, attempt,
            )
            return

        chunk, _ = BulkUnenrollChunk.objects.get_or_create(
            course_state_id=state_pk, attempt=attempt,
            chunk_index=chunk_index, continuation=continuation,
        )
        claimed = BulkUnenrollChunk.objects.filter(
            pk=chunk.pk, state=BulkUnenrollChunk.State.PENDING,
        ).update(
            state=BulkUnenrollChunk.State.FINISHED,
            finished=timezone.now(),
            modified=timezone.now(),
        )
        if not claimed:
            log.info(
                "bulk_unenroll_chunk: chunk %s of course-state %s already reported in; "
                "discarding duplicate tally",
                chunk_index, state_pk,
            )
            return

        BulkUnenrollCourseState.objects.filter(pk=state_pk).update(
            unenrolled=F("unenrolled") + unenrolled,
            already_inactive=F("already_inactive") + already_inactive,
            failed_count=F("failed_count") + failed,
            # A chunk that handed on a tail is not finished: its chain counts once,
            # via its last continuation. Only the fan-out ever writes chunks_total.
            chunks_finished=F("chunks_finished") + (0 if tail else 1),
            modified=timezone.now(),
        )
        finalized_batch_pk = _finalize_course_if_complete(state_pk)

    # Publish/settle only after the accounting commits: the tail must not be
    # runnable before its row exists, and course+batch locks together can deadlock.
    if tail:
        batch_uuid, course_id, user_ids = tail
        try:
            # The tail keeps this chunk's index and takes its next continuation —
            # that triple is the identity the worker claims against.
            _queue_engine_task(bulk_unenroll_chunk, [
                batch_uuid, course_id, list(user_ids), chunk_index, attempt, continuation + 1,
            ])
        except Exception as exc:  # pylint: disable=broad-except
            # The tail is unreachable (a redelivery discards itself as a duplicate),
            # so the chain can never finish — fail the course so retry can re-run it.
            log.exception(
                "bulk_unenroll_chunk: could not queue the continuation of chunk %s "
                "(course-state %s, attempt %s); failing the course",
                chunk_index, state_pk, attempt,
            )
            _fail_course(state_pk, f"Could not queue remaining learners: {exc}")
            raise
    if finalized_batch_pk:
        _finalize_batch_if_complete(finalized_batch_pk)


def _fail_course(state_pk, error):
    """
    Mark a course failed outright, without waiting for its chunks to report in.

    Used when the *fan-out itself* breaks: the course then expects work that will
    never run, so chunk accounting can never complete it and it would wedge its
    batch ``running`` forever — the one state the retry endpoint refuses. ``failed``
    is terminal, lets the batch settle, and is what retry picks up (bumping
    ``attempt``, so anything still in flight is discarded). The batch is settled
    here too, after the course row's lock is released.
    """
    with transaction.atomic():
        state = BulkUnenrollCourseState.objects.select_for_update().get(pk=state_pk)
        if state.state in BulkUnenrollCourseState.TERMINAL_STATES:
            return
        state.state = BulkUnenrollCourseState.State.FAILED
        # The column is 255 chars; a broker traceback can be far longer.
        state.error = error[:255]
        state.finished = timezone.now()
        state.save(update_fields=["state", "error", "finished", "modified"])
        batch_pk = state.batch_id
    _finalize_batch_if_complete(batch_pk)


def _fail_unqueued_courses(course_pks, error):
    """
    Fail the specific courses a dispatcher never managed to queue.

    ``_fail_course``'s counterpart one level up: nothing else will ever queue them,
    so ``pending`` is a state they can never leave and their batch never settles.

    Only pass courses the caller knows it did not reach — "still ``pending``" is not
    the test, since a published course stays pending until a worker claims it. The
    update is nonetheless conditional on ``pending``, because a publish error does
    not prove the message was undelivered: a course a worker already claimed is left
    to run normally.

    Returns the number of courses failed.
    """
    if not course_pks:
        return 0
    return BulkUnenrollCourseState.objects.filter(
        pk__in=course_pks, state=BulkUnenrollCourseState.State.PENDING,
    ).update(
        state=BulkUnenrollCourseState.State.FAILED,
        # The column is 255 chars; a broker traceback can be far longer.
        error=error[:255],
        finished=timezone.now(),
        modified=timezone.now(),
    )


@shared_task
@set_code_owner_attribute
def bulk_unenroll_batch(batch_uuid):
    """
    Start a whole bulk-unenroll batch — level 1, the dispatcher.

    Marks the batch ``running`` and queues one ``bulk_unenroll_course`` per
    still-pending course. Fan-out is intentionally unbounded: concurrency is
    governed by the (ideally dedicated) queue's worker count, the same shape
    bulk-email uses.

    **Resumable, not exclusive** — it queues whatever is still ``pending``, and
    re-queuing a course already under way is harmless because
    ``bulk_unenroll_course`` claims its own ``pending -> running`` flip. But nothing
    guarantees a redelivery (acked on delivery, no retry policy), so a fan-out that
    raises fails the courses it could not queue (``_fail_unqueued_courses``),
    leaving a settled batch the operator can retry.
    """
    batch = BulkUnenrollBatch.objects.get(uuid=batch_uuid)
    # `modified` is set explicitly at every queryset .update() in this feature:
    # TimeStampedModel maintains it on save(), not on UPDATE.
    started = BulkUnenrollBatch.objects.filter(
        pk=batch.pk,
        state__in=(BulkUnenrollBatch.State.PENDING, BulkUnenrollBatch.State.RUNNING),
    ).update(state=BulkUnenrollBatch.State.RUNNING, modified=timezone.now())
    if not started:
        # Cancelled or already finished — there is nothing left to dispatch.
        log.info(
            "bulk_unenroll_batch: batch %s is not dispatchable (state=%s); nothing queued",
            batch_uuid, batch.state,
        )
        return

    # Materialized up front: queued courses are still 'pending' too, so position
    # in this list — not row state — separates "queued" from "never reached".
    pending = list(
        batch.courses
        .filter(state=BulkUnenrollCourseState.State.PENDING)
        .values_list("pk", "course_id")
    )
    queued = 0
    try:
        for _, course_id in pending:
            # Cheap EXISTS check: stop queuing as soon as a cancel lands.
            if BulkUnenrollBatch.objects.filter(
                pk=batch.pk, state=BulkUnenrollBatch.State.CANCELLED,
            ).exists():
                break
            _queue_engine_task(bulk_unenroll_course, [batch_uuid, str(course_id)])
            queued += 1
    except Exception as exc:  # pylint: disable=broad-except
        # Nothing else will ever queue the unreached courses, so fail them. The
        # slice starts at the ambiguous raiser; _fail_unqueued_courses is safe there.
        unreached = [pk for pk, _ in pending[queued:]]
        log.exception(
            "bulk_unenroll_batch: fan-out of batch %s failed after queueing %s of %s "
            "course(s); failing the %s it never reached",
            batch_uuid, queued, len(pending), len(unreached),
        )
        _fail_unqueued_courses(unreached, f"Could not queue unenroll work: {exc}")
        _finalize_batch_if_complete(batch.pk)
        raise

    _finalize_batch_if_complete(batch.pk)


def _chunk_stop_reason(state_pk, attempt):
    """
    Why this chunk must not (or must no longer) touch enrollments — or ``None``.

    One query for the three ways a chunk's mandate can be revoked: the batch was
    cancelled; a retry bumped ``attempt``, superseding this chunk's generation; or
    the course already settled — most importantly as ``failed``, which a broken
    fan-out sets while chunks it did publish are still queued. That last case is
    what stops a destructive operation from outliving the status the operator sees.

    Called up front *and* every ``BULK_UNENROLL_CANCEL_CHECK_EVERY`` learners, so a
    mid-chunk revocation is honoured within a bounded number of learners.
    """
    row = (
        BulkUnenrollCourseState.objects
        .filter(pk=state_pk)
        .values_list("state", "attempt", "batch__state")
        .first()
    )
    if row is None:
        return "its course-state row no longer exists"
    course_state, current_attempt, batch_state = row
    if batch_state == BulkUnenrollBatch.State.CANCELLED:
        return "the batch was cancelled"
    if current_attempt != attempt:
        return f"attempt {attempt} was superseded by attempt {current_attempt}"
    if course_state in BulkUnenrollCourseState.TERMINAL_STATES:
        return f"the course already settled as '{course_state}'"
    return None


def _queue_engine_task(task, args):
    """Publish one engine task on the (optionally dedicated) bulk-unenroll queue."""
    # queue= (not routing_key=) — the codebase convention (see EXPLICIT_QUEUES),
    # and it resolves via the queue table instead of relying on exchange bindings.
    task.apply_async(args=args, queue=settings.BULK_UNENROLL_ROUTING_KEY)


def _finalize_batch_if_complete(batch_pk):
    """
    Mark the batch done once every one of its courses has reached a terminal state.

    Claimed transactionally (``select_for_update`` + a running-only guard) so that
    when courses finish concurrently, exactly one of them derives the batch's final
    state: ``succeeded`` (no course failed), ``failed`` (none succeeded), or
    ``partial`` (a mix). Skipped courses count as neither.
    """
    with transaction.atomic():
        batch = BulkUnenrollBatch.objects.select_for_update().get(pk=batch_pk)
        if batch.state != BulkUnenrollBatch.State.RUNNING:
            return
        course_states = list(batch.courses.values_list("state", flat=True))
        if any(cs not in BulkUnenrollCourseState.TERMINAL_STATES for cs in course_states):
            return
        failed = sum(1 for cs in course_states if cs == BulkUnenrollCourseState.State.FAILED)
        succeeded = sum(1 for cs in course_states if cs == BulkUnenrollCourseState.State.SUCCEEDED)
        if failed == 0:
            batch.state = BulkUnenrollBatch.State.SUCCEEDED
        elif succeeded == 0:
            batch.state = BulkUnenrollBatch.State.FAILED
        else:
            batch.state = BulkUnenrollBatch.State.PARTIAL
        batch.save(update_fields=["state", "modified"])


@shared_task
@set_code_owner_attribute
def bulk_unenroll_course(batch_uuid, course_id):
    """
    Fan a single course out into chunk tasks — level 2, where the enrollment fetch
    happens (on the worker).

    Streams the course's *active* enrollment user ids with
    ``values_list(...).iterator()`` so a 50k-enrollment course is never materialized,
    queues a ``bulk_unenroll_chunk`` per ``BULK_UNENROLL_CHUNK_SIZE`` group, and
    records ``total_enrollments`` / ``chunks_total`` for the chunk-level finalizer.
    A course with nothing active finalizes ``succeeded`` immediately.

    The fan-out is **claimed** via the ``pending -> running`` flip, so a redelivery
    cannot queue a second chunk set. That same flip means a redelivery would skip a
    *broken* fan-out, so one that raises fails the course on its way out
    (``_fail_course``) instead of leaving it ``running`` with no ``chunks_total``.
    """
    batch = BulkUnenrollBatch.objects.get(uuid=batch_uuid)
    course_key = CourseKey.from_string(course_id)
    state = BulkUnenrollCourseState.objects.get(batch=batch, course_id=course_key)

    claimed = BulkUnenrollCourseState.objects.filter(
        pk=state.pk, state=BulkUnenrollCourseState.State.PENDING,
    ).update(
        state=BulkUnenrollCourseState.State.RUNNING,
        started=timezone.now(),
        modified=timezone.now(),
    )
    if not claimed:
        log.info(
            "bulk_unenroll_course: course %s in batch %s is not pending (state=%s); "
            "skipping duplicate fan-out",
            course_id, batch_uuid, state.state,
        )
        return

    # Read back the generation this fan-out belongs to: every chunk it queues
    # carries it, so a straggler from an earlier attempt can be told apart.
    attempt = BulkUnenrollCourseState.objects.values_list("attempt", flat=True).get(pk=state.pk)

    chunk_size = settings.BULK_UNENROLL_CHUNK_SIZE
    active_user_ids = (
        CourseEnrollment.objects
        .filter(course_id=course_key, is_active=True)
        .values_list("user_id", flat=True)
    )

    total = 0
    chunks = 0
    buffer = []
    try:
        for user_id in active_user_ids.iterator(chunk_size=2000):
            buffer.append(user_id)
            total += 1
            if len(buffer) >= chunk_size:
                _queue_engine_task(bulk_unenroll_chunk, [batch_uuid, course_id, buffer, chunks, attempt, 0])
                chunks += 1
                buffer = []
        if buffer:
            _queue_engine_task(bulk_unenroll_chunk, [batch_uuid, course_id, buffer, chunks, attempt, 0])
            chunks += 1

        BulkUnenrollCourseState.objects.filter(pk=state.pk).update(
            total_enrollments=total,
            chunks_total=chunks,
            modified=timezone.now(),
        )
    except Exception as exc:  # pylint: disable=broad-except
        # A fan-out that dies partway never records chunks_total, and the claim
        # means a redelivery skips it — fail the course so retry can re-run it.
        log.exception(
            "bulk_unenroll_course: fan-out of course %s in batch %s failed after "
            "queueing %s chunk(s); failing the course",
            course_id, batch_uuid, chunks,
        )
        _fail_course(state.pk, f"Could not queue unenroll work: {exc}")
        raise

    if chunks == 0:
        # Nothing to unenroll — done, but only while this task's claim still holds:
        # a cancel landing mid-fan-out has already settled the course terminally.
        settled = BulkUnenrollCourseState.objects.filter(
            pk=state.pk, state=BulkUnenrollCourseState.State.RUNNING,
        ).update(
            state=BulkUnenrollCourseState.State.SUCCEEDED,
            finished=timezone.now(),
            modified=timezone.now(),
        )
        if settled:
            _finalize_batch_if_complete(batch.pk)
    else:
        # Covers the case where every chunk has already finished (e.g. eager mode)
        # by the time chunks_total is recorded.
        finalized_batch_pk = _finalize_course_if_complete(state.pk)
        if finalized_batch_pk:
            _finalize_batch_if_complete(finalized_batch_pk)


def _finalize_course_if_complete(state_pk):
    """
    Mark a course done once every one of its chunks has reported in.

    The finalizer is claimed transactionally (``select_for_update`` + a
    not-already-terminal guard) so that when concurrent chunks finish together,
    exactly one of them flips the course to its terminal state — the reliable
    "is the course truly finished?" primitive.

    Returns the batch pk when *this* call finalized the course, so the caller can
    settle the batch after the course row's lock is released; ``None`` otherwise.
    """
    with transaction.atomic():
        state = BulkUnenrollCourseState.objects.select_for_update().get(pk=state_pk)
        if not state.chunks_total or state.chunks_finished < state.chunks_total:
            return None
        if state.state in BulkUnenrollCourseState.TERMINAL_STATES:
            return None
        state.state = (
            BulkUnenrollCourseState.State.SUCCEEDED
            if state.failed_count == 0
            else BulkUnenrollCourseState.State.FAILED
        )
        state.finished = timezone.now()
        state.save(update_fields=["state", "finished", "modified"])
        return state.batch_id


def update_audit_status(audit_instance, status):
    audit_instance.status = status
    if status == CourseResetAudit.CourseResetStatus.COMPLETE:
        audit_instance.completed_at = datetime.now()
    audit_instance.save()


def get_blocks(course):
    """ Get a list of problem xblock for the course."""
    blocks = []
    for section in course.get_children():
        for subsection in section.get_children():
            for vertical in subsection.get_children():
                for block in vertical.get_children():
                    blocks.append(block)
    return blocks


@shared_task
@set_code_owner_attribute
def send_reset_course_completion_email(course, user):
    """
    Sends email to a learner when whole course reset is complete.
    """
    site = Site.objects.get_current()

    message_context = get_base_template_context(site)
    message_context.update({
        'course_title': course.display_name,
    })

    try:
        log.info(
            f"Sending whole course reset email to {user.profile.name} (Email: {user.email}) "
            f"from course {course.display_name} (CourseId: {course.id})"
        )
        with emulate_http_request(site=site, user=user):
            msg = WholeCourseReset(context=message_context).personalize(
                recipient=Recipient(user.id, user.email),
                language=get_user_preference(user, LANGUAGE_KEY),
                user_context={'full_name': user.profile.name}
            )
            ace.send(msg)
    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            f"Whole course reset email to {user.profile.name} (Email: {user.email}) "
            f"from course {course.display_name} (CourseId: {course.id}) failed."
            f"Error: {exc.response['Error']['Code']}"
        )
        return False
    else:
        log.info(
            f"Whole course reset email sent successfully to {user.profile.name} (Email: {user.email}) "
            f"from course {course.display_name} (CourseId: {course.id})"
        )
        return True


@shared_task
@set_code_owner_attribute
def reset_student_course(course_id, learner_email, reset_by_user_email):
    """
    Resets a learner's course progress
    """
    user = get_user_by_username_or_email(learner_email)
    reset_by_user = get_user_by_username_or_email(reset_by_user_email)
    enrollment = CourseEnrollment.objects.get(
        course=course_id,
        user=user,
        is_active=True,
    )
    course_overview = enrollment.course_overview
    course_reset_audit = CourseResetAudit.objects.get(
        course_enrollment=enrollment,
        status=CourseResetAudit.CourseResetStatus.ENQUEUED
    )
    update_audit_status(course_reset_audit, CourseResetAudit.CourseResetStatus.IN_PROGRESS)

    try:
        course = get_course(course_overview.id, depth=4)
        blocks = get_blocks(course)

        # Clear student state and score
        for data in blocks:
            try:
                reset_student_attempts(
                    course.id,
                    user,
                    data.scope_ids.usage_id,
                    reset_by_user,
                    delete_module=True,
                    emit_signals_and_events=False
                )
            except StudentModule.DoesNotExist:
                pass

        # Clear block completion data
        BlockCompletion.objects.clear_learning_context_completion(user, course.id)
        # Clear a student grades for a course
        clear_user_course_grades(user.id, course.id)

        update_audit_status(course_reset_audit, CourseResetAudit.CourseResetStatus.COMPLETE)

        # Send email upon completion
        send_reset_course_completion_email(course, user)

    except Exception as e:  # pylint: disable=broad-except
        logging.exception(f'Error occurred for Course Audit with ID {course_reset_audit.id}: {e}.')
        update_audit_status(course_reset_audit, CourseResetAudit.CourseResetStatus.FAILED)
