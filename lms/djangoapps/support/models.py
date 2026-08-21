"""
Models used to implement support related models in such as SSO History model
"""
import uuid

from django.contrib.auth import get_user_model
from django.db.models import ForeignKey, DO_NOTHING, CASCADE, TextChoices
from django.db.models.fields import (
    BooleanField,
    CharField,
    DateTimeField,
    PositiveIntegerField,
    UUIDField,
)

from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField
from simple_history import register
from social_django.models import UserSocialAuth

from common.djangoapps.student.models import CourseEnrollment

User = get_user_model()

# Registers UserSocialAuth with simple-django-history.
register(UserSocialAuth, app=__package__)


class CourseResetCourseOptIn(TimeStampedModel):
    """
    Model that represents a course which has opted in to the course reset feature.

    .. no_pii:
    """
    course_id = CourseKeyField(max_length=255, unique=True)
    active = BooleanField()

    def __str__(self):
        return f'{self.course_id} - {"ACTIVE" if self.active else "INACTIVE"}'

    @staticmethod
    def all_active():
        return CourseResetCourseOptIn.objects.filter(active=True)

    @staticmethod
    def all_active_course_ids():
        return [course.course_id for course in CourseResetCourseOptIn.all_active()]


class CourseResetAudit(TimeStampedModel):
    """
    Model which records the course reset action's status and metadata

    .. no_pii:
    """
    class CourseResetStatus(TextChoices):
        IN_PROGRESS = "in_progress"
        COMPLETE = "complete"
        ENQUEUED = "enqueued"
        FAILED = "failed"

    course = ForeignKey(
        CourseResetCourseOptIn,
        on_delete=CASCADE
    )
    course_enrollment = ForeignKey(
        CourseEnrollment,
        on_delete=DO_NOTHING
    )
    reset_by = ForeignKey(
        User,
        on_delete=DO_NOTHING
    )
    status = CharField(
        max_length=12,
        choices=CourseResetStatus.choices,
        default=CourseResetStatus.ENQUEUED,
    )
    completed_at = DateTimeField(default=None, null=True, blank=True)
    comment = CharField(max_length=255, default="", blank=True)

    def status_message(self):
        """ Return a string message about the status of this audit """
        if self.status == self.CourseResetStatus.FAILED:
            return f"Failed on {self.modified}"
        if self.status == self.CourseResetStatus.ENQUEUED:
            return f"Enqueued - Created {self.created} by {self.reset_by.username}"
        if self.status == self.CourseResetStatus.COMPLETE:
            return f"Completed on {self.completed_at} by {self.reset_by.username}"
        if self.status == self.CourseResetStatus.IN_PROGRESS:
            return f"In progress - Started on {self.modified} by {self.reset_by.username}"
        return self.status


class BulkUnenrollBatch(TimeStampedModel):
    """
    A single bulk-unenroll upload: the batch layer over per-course work.

    Holds the operator-supplied metadata and the aggregate state the UI polls;
    per-course rows hang off it via ``BulkUnenrollCourseState``. ``uuid`` is the
    public identifier used in URLs, so the auto-increment pk is never exposed.

    .. no_pii:
    """
    class State(TextChoices):
        """Lifecycle states for a bulk-unenroll batch."""
        VALIDATED = "validated"    # dry-run parsed & counted; awaiting confirm
        PENDING = "pending"        # confirmed; queued for the workers
        CANCELLED = "cancelled"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        PARTIAL = "partial"
        FAILED = "failed"

    uuid = UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    requester = ForeignKey(User, on_delete=DO_NOTHING)
    csv_filename = CharField(max_length=255, default="", blank=True)
    reason = CharField(max_length=255, default="", blank=True)
    state = CharField(max_length=20, choices=State.choices, default=State.VALIDATED)
    total_courses = PositiveIntegerField(default=0)

    def __str__(self):
        return f"BulkUnenrollBatch {self.uuid} ({self.state}, {self.total_courses} courses)"


class BulkUnenrollCourseState(TimeStampedModel):
    """
    Per-course row within a ``BulkUnenrollBatch`` — one row per *distinct* valid
    course id in the upload (duplicate lines collapse), and the unit of progress,
    retry, and resumability.

    ``active_count`` is the dry-run preview count set at upload; the remaining
    counters and chunk-tracking fields are populated by the Celery layer.

    .. no_pii:
    """
    class State(TextChoices):
        """Lifecycle states for a single course within a batch."""
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        SKIPPED = "skipped"
        # Work was stopped (unlike SKIPPED: never eligible); some learners may
        # already be unenrolled. Terminal — a late chunk must not flip it back.
        CANCELLED = "cancelled"

    #: States a course can never leave — the single definition shared by the
    #: engine's finalizers and the status endpoint's ``courses_finished`` count.
    TERMINAL_STATES = frozenset({
        State.SUCCEEDED, State.FAILED, State.SKIPPED, State.CANCELLED,
    })

    batch = ForeignKey(BulkUnenrollBatch, on_delete=CASCADE, related_name="courses")
    course_id = CourseKeyField(max_length=255, db_index=True)
    active_count = PositiveIntegerField(default=0)
    state = CharField(max_length=20, choices=State.choices, default=State.PENDING)
    error = CharField(max_length=255, default="", blank=True)

    # --- Mutation progress (populated by the Celery workers) ---
    # Active enrollments measured at fan-out (vs active_count, the dry-run preview).
    total_enrollments = PositiveIntegerField(default=0)
    unenrolled = PositiveIntegerField(default=0)
    # Learners already inactive when a chunk reached them — not an error.
    already_inactive = PositiveIntegerField(default=0)
    failed_count = PositiveIntegerField(default=0)
    # Finalization primitive: the course is done when chunks_finished ==
    # chunks_total (and > 0). Updated with F() so concurrent chunks don't clobber.
    chunks_total = PositiveIntegerField(default=0)
    chunks_finished = PositiveIntegerField(default=0)
    # Generation counter, bumped by every retry: a straggler from a superseded
    # attempt is discarded instead of claiming an identity the new attempt needs.
    attempt = PositiveIntegerField(default=1)
    started = DateTimeField(null=True, blank=True)
    finished = DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = (("batch", "course_id"),)

    def __str__(self):
        return f"{self.course_id} ({self.state}) in batch {self.batch.uuid}"


class BulkUnenrollChunk(TimeStampedModel):
    """
    One chunk of learners within a ``BulkUnenrollCourseState`` — the completion ledger.

    Celery delivers at-least-once, so counting "chunks finished" per invocation is
    unsafe: a redelivery could push ``chunks_finished`` to ``chunks_total`` while a
    different chunk had never run, finalizing the course with learners still
    enrolled. This table gives every chunk a durable identity and one atomic
    completion claim — a conditional ``pending -> finished`` flip, where only the
    winner records its counters. Re-doing the *work* stays harmless (every level
    filters on ``is_active=True``); it is the *accounting* that must happen once.

    .. no_pii:
    """
    class State(TextChoices):
        """Lifecycle states for a single chunk."""
        PENDING = "pending"
        FINISHED = "finished"

    course_state = ForeignKey(BulkUnenrollCourseState, on_delete=CASCADE, related_name="chunks")
    #: Which generation of the course's fan-out this chunk belongs to. Rows from
    #: earlier attempts are kept as history and never collide with the current one.
    attempt = PositiveIntegerField(default=1)
    #: 0-based position within that attempt's fan-out.
    chunk_index = PositiveIntegerField()
    #: Which hand-off of the chunk this row is (0 = as fanned out, +1 per timeout
    #: tail). Numbered within its chunk so it never collides with a queued sibling.
    continuation = PositiveIntegerField(default=0)
    state = CharField(max_length=20, choices=State.choices, default=State.PENDING)
    finished = DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = (("course_state", "attempt", "chunk_index", "continuation"),)

    def __str__(self):
        return (
            f"chunk {self.chunk_index}.{self.continuation} ({self.state}) "
            f"of {self.course_state_id} attempt {self.attempt}"
        )
