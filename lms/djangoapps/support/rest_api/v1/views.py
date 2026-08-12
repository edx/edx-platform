"""
API Views for course team management in support app.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils.timezone import now
from edx_rest_framework_extensions.paginators import DefaultPagination
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from common.djangoapps.student.api import (
    BulkUnenrollCsvTooManyRows,
    BulkUnenrollCsvUnreadable,
    parse_bulk_unenroll_csv,
)
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.models.user import CourseAccessRole
from lms.djangoapps.support.models import BulkUnenrollBatch, BulkUnenrollCourseState
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

from ..serializers import (
    BulkUnenrollBatchSerializer,
    BulkUnenrollCourseStateSerializer,
    CourseTeamManageSerializer,
)

User = get_user_model()

log = logging.getLogger(__name__)


def _dispatch_bulk_unenroll(batch):
    """
    Queue the level-1 dispatcher (``bulk_unenroll_batch``) for a confirmed batch.

    The request thread never touches enrollments — all fetching and mutation happen
    on the workers. Kept as a module-level seam so ``confirm``/``retry`` have a
    stable call site and tests can assert the hand-off without running the engine.
    """
    from lms.djangoapps.support.tasks import bulk_unenroll_batch  # lazy: avoid import cycle
    bulk_unenroll_batch.apply_async(
        args=[str(batch.uuid)],
        queue=settings.BULK_UNENROLL_ROUTING_KEY,
    )


def _dispatch_or_rollback(batch, action, rollback):
    """
    Publish the dispatcher once the state transition is durably committed.

    Shared by confirm and retry, which both claim a transition and only then publish —
    a batch left ``pending`` with no dispatcher is stranded, since nothing owns it and
    neither endpoint would accept it again. ``rollback`` differs per endpoint (they
    undo different rows) but must always be *conditional*: a publish error does not
    prove the message was undelivered, so a dispatcher that did receive it may already
    be working, and only untouched rows may be reverted.

    Registered with ``transaction.on_commit`` rather than called inline. Deployments
    set ``ATOMIC_REQUESTS`` (prod, stage and ``lms/envs/test.py`` all do), which wraps
    the whole request in a transaction and demotes the callers' ``atomic()`` blocks to
    savepoints — an inline publish would hand the dispatcher a row that is still
    uncommitted and exclusively locked, and it would settle the batch as "not
    dispatchable". The hook fires only after the real outermost commit, under any
    ``ATOMIC_REQUESTS`` setting.

    The trade-off: the publish outcome is not known while the response is being built,
    so a broker failure can no longer answer 503. It rolls the transition back and
    logs; the batch reverts to its previous state and the operator can act again.
    """
    def _publish():
        try:
            _dispatch_bulk_unenroll(batch)
        except Exception:  # pylint: disable=broad-except
            log.exception(
                "bulk_unenroll: failed to queue %s for batch %s; rolling the "
                "transition back so the batch is not stranded", action, batch.uuid,
            )
            rollback()

    transaction.on_commit(_publish)


def _batch_response(batch, http_status):
    """The `{batch_id, state}` body confirm/cancel/retry all answer with."""
    return Response({"batch_id": str(batch.uuid), "state": batch.state}, status=http_status)


def _conflict(detail):
    """A 409 for a lifecycle action the batch's current state does not allow."""
    return Response({"detail": detail}, status=status.HTTP_409_CONFLICT)


#: Per-course progress a retry clears, and the value it clears each one to. Named
#: once so the reset and the rollback that undoes it cannot drift apart.
RESET_FIELDS = {
    "unenrolled": 0, "already_inactive": 0, "failed_count": 0,
    "total_enrollments": 0, "chunks_total": 0, "chunks_finished": 0,
    "started": None, "finished": None, "error": "",
}


def _parse_state_filter(request, state_enum):
    """
    Read a comma-separated ``?state=`` query param, or ``None`` if absent.

    Shared by the batch list and the per-batch course list so the two cannot
    disagree about what ``?state=`` means. Unknown values raise rather than being
    dropped: a filter that silently returns nothing is worse than one that rejects.
    """
    raw = request.query_params.get("state")
    if not raw:
        return None

    states = [value.strip() for value in raw.split(",") if value.strip()]
    unknown = [value for value in states if value not in set(state_enum.values)]
    if unknown:
        raise ValidationError({"state": f"Unknown state(s): {', '.join(unknown)}."})
    return states


class CourseTeamManageAPIView(GenericAPIView):
    """
    Use case:
        - APIs for viewing and managing a user's course team roles.
        - Lists courses where the authenticated user has permission to manage roles.
        - Displays the given user's role in each accessible course.
        - Allows assigning or revoking roles via PUT, limited to courses the user can manage.
    """

    permission_classes = (IsAuthenticated,)
    serializer_class = CourseTeamManageSerializer
    pagination_class = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._course_role_map = {}
        self._access_roles = None

    def get_serializer_context(self):
        """Provide extra context to the serializer."""
        context = super().get_serializer_context()
        context["course_role_map"] = getattr(self, "_course_role_map", {})
        return context

    def get_course_role_map_for_user(self, user):
        """Return a mapping of course_id to role for staff/instructor roles of given user."""
        access_roles = CourseAccessRole.objects.filter(
            user=user, role__in=["staff", "instructor"]
        )
        course_role_map = {}
        for access_role in access_roles:
            course_id = str(access_role.course_id)
            # Prioritize instructor role if present
            if access_role.role == "instructor" or course_id not in course_role_map:
                course_role_map[course_id] = access_role.role
        return course_role_map

    def get_accessible_courses_for_user(self, auth_user):
        """Return queryset of courses accessible by the authenticated user."""
        if auth_user.is_superuser or auth_user.is_staff:
            return CourseOverview.objects.all()

        access_roles = CourseAccessRole.objects.filter(
            user=auth_user, role="instructor"
        ).only("org", "course_id")

        # Collect org-level and course-level permissions
        orgs = set()
        course_keys = set()

        for access_role in access_roles:
            if access_role.course_id:
                course_keys.add(str(access_role.course_id))
            elif access_role.org:
                orgs.add(access_role.org)

        # Build a filter to fetch all courses:
        # - Courses in the orgs where the user has org-level access
        # - Courses explicitly assigned to the user
        course_filter = Q()
        if orgs:
            course_filter |= Q(org__in=orgs)
        if course_keys:
            course_filter |= Q(id__in=course_keys)

        return (
            CourseOverview.objects.filter(course_filter)
            if course_filter
            else CourseOverview.objects.none()
        )

    def get_queryset(self):
        """Main entry to return courses filtered by authenticated user access and requested user roles."""
        email = self.request.query_params.get("email")
        username = self.request.query_params.get("username")
        user_id = self.request.query_params.get("user_id")

        if not any([email, username, user_id]):
            raise ValidationError(
                {
                    "detail": "Missing required query parameters. "
                    "At least one of 'email', 'username', or 'user_id' is required."
                }
            )

        # Build user lookup query
        user_query = Q(is_active=True)
        if email:
            user_query &= Q(email=email)
        elif username:
            user_query &= Q(username=username)
        elif user_id:
            user_query &= Q(id=user_id)

        try:
            user = User.objects.get(user_query)
        except ObjectDoesNotExist as exc:
            identifier = email or username or user_id
            raise NotFound(
                f"User with identifier '{identifier}' not found or not active."
            ) from exc

        self._access_roles = CourseAccessRole.objects.filter(
            user=user, role__in=["staff", "instructor"]
        )
        self._course_role_map = self.get_course_role_map_for_user(user)

        auth_user = self.request.user
        return self.get_accessible_courses_for_user(auth_user)

    def list(self, request, *args, **kwargs):
        """list of courses for the organization and user."""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    from drf_yasg import openapi
    from drf_yasg.utils import swagger_auto_schema

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "email",
                openapi.IN_QUERY,
                description="User's email address",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "username",
                openapi.IN_QUERY,
                description="User's username",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "user_id",
                openapi.IN_QUERY,
                description="User's ID",
                type=openapi.TYPE_INTEGER,
            ),
        ]
    )
    def get(self, request, *args, **kwargs):
        """
        Retrieve a list of courses accessible by the authenticated user

        **Use Case**

            GET API to retrieve a list of courses accessible by the authenticated user,
            along with the specified user's role ("instructor", "staff", or null) in each course.

        **Endpoint**

            GET /api/support/v1/manage_course_team/

        **Query Parameters**

            At least one of the following parameters is required:
            - email: User's email address
            - username: User's username
            - user_id: User's ID

        **Returns**

            List of courses accessible to the authenticated user, each annotated with the
            specified user's role in that course. Each course includes organizational
            information and identifiers.

        **Example Response**

        ```json
            {
                "results": [
                    {
                        "course_id": "course-v1:edX+DemoX+2025_T1",
                        "course_name": "edX Demonstration Course",
                        "course_url": "https://studio.example.com/course/course-v1:edX+DemoX+2025_T1",
                        "role": "instructor",
                        "status": "active",
                        "org": "edX",
                        "run": "2025_T1",
                        "number": "DemoX"
                    },
                    {
                        "course_id": "course-v1:MITx+6.00x+2024_Fall",
                        "course_name": "Introduction to Computer Science",
                        "course_url": "https://studio.example.com/course/course-v1:MITx+6.00x+2024_Fall",
                        "role": "staff",
                        "status": "archived",
                        "org": "MITx",
                        "run": "2024_Fall",
                        "number": "6.00x"
                    }
                ]
            }
        ```
        """
        return self.list(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        """
        Bulk assign or revoke course team roles for a user across multiple courses.

        **Endpoint**

            PUT /api/support/v1/manage_course_team/

        **Permissions**

            - Admin/Staff users: Can manage roles for any course
            - Instructor users: Can only manage roles for courses/orgs they have instructor access to
            - Other users: Access denied

        **Request Data**

            A JSON object containing:
                - email: User's email address
                - bulk_role_operations: List of role operations, each containing:
                    - course_id: Course key string
                    - role: Role to assign or revoke ("instructor" or "staff")
                    - action: "assign" or "revoke"

        **Example Request**

        ```json
        {
            "email": "user1@example.com",
            "bulk_role_operations": [
                {
                    "course_id": "course-v1:edX+DemoX+2025_T1",
                    "role": "instructor",
                    "action": "assign"
                },
                {
                    "course_id": "course-v1:edX+DemoX+2025_T2",
                    "role": "staff",
                    "action": "revoke"
                }
            ]
        }
        ```

        **Returns**

            - HTTP 200 with results for each operation (success or error details)
            - HTTP 400 if the request data is invalid

        **Example Response**

        ```json
        {
            "email": "user1@example.com",
            "results": [
                {
                    "course_id": "course-v1:edX+DemoX+2025_T1",
                    "role": "instructor",
                    "action": "assign",
                    "status": "success"
                },
                {
                    "course_id": "course-v1:edX+DemoX+2025_T2",
                    "role": "staff",
                    "action": "revoke",
                    "status": "failed",
                    "error": "error_message"
                }
            ]
        }
        ```
        """
        results = []

        # Validate request data structure and extract fields
        if not isinstance(request.data, dict):
            return self._error_response(
                "",
                "Request data must be a JSON object with 'email' and 'bulk_role_operations' fields.",
            )

        email = request.data.get("email")
        bulk_role_operations = request.data.get("bulk_role_operations", [])

        # Combined validation for email and bulk_role_operations
        if not email:
            return self._error_response(
                "", "Missing required field: 'email' is required."
            )

        if not isinstance(bulk_role_operations, list) or not bulk_role_operations:
            return self._error_response(
                email,
                "Missing or empty 'bulk_role_operations' field. Must be a non-empty list.",
            )

        auth_user = request.user

        # Get accessible orgs/courses and user validation
        accessible_orgs, accessible_courses, user, validation_error = (
            self._validate_permissions_and_user(auth_user, email)
        )
        if validation_error:
            return validation_error

        course_key_cache = {}

        for operation_data in bulk_role_operations:
            result = self._handle_role_assignment_entry(
                operation_data,
                auth_user,
                accessible_orgs,
                accessible_courses,
                user,
                course_key_cache,
            )
            results.append(result)

        return Response({"email": email, "results": results}, status=status.HTTP_200_OK)

    def _fetch_user_accessible_orgs_and_courses(self, auth_user):
        """Return (orgs, courses, error_response) based on user permissions."""
        accessible_orgs = set()
        accessible_courses = set()
        permission_error = None

        # Admins and staff have full access by default
        if not (auth_user.is_superuser or auth_user.is_staff):
            roles = CourseAccessRole.objects.filter(
                user=auth_user, role="instructor"
            ).only("org", "course_id")

            if roles.exists():
                for role in roles:
                    if role.course_id:
                        accessible_courses.add(str(role.course_id))
                    elif role.org:
                        # Lowercase course org for case-insensitive compare
                        accessible_orgs.add(role.org.lower())
            else:
                permission_error = Response(
                    {
                        "results": [
                            {
                                "status": "failed",
                                "error": "You do not have permission to perform bulk role operations.",
                            }
                        ]
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        return accessible_orgs, accessible_courses, permission_error

    def _error_response(
        self, email, error_message, status_code=status.HTTP_400_BAD_REQUEST
    ):
        """Helper method to create standardized error responses."""
        return Response(
            {
                "email": email,
                "results": [
                    {
                        "status": "failed",
                        "error": error_message,
                    }
                ],
            },
            status=status_code,
        )

    def _validate_permissions_and_user(self, auth_user, email):
        """Combined validation for user permissions and user lookup."""
        error_response = None
        user = None

        # Get accessible orgs/courses
        accessible_orgs, accessible_courses, permission_error = (
            self._fetch_user_accessible_orgs_and_courses(auth_user)
        )

        if permission_error:
            error_response = self._error_response(
                email,
                "You do not have permission to perform bulk role operations.",
                status.HTTP_403_FORBIDDEN,
            )
            return accessible_orgs, accessible_courses, user, error_response

        # Get and validate user
        user = self._get_user(email, {})
        if not user:
            error_response = self._error_response(
                email, "User not found.", status.HTTP_404_NOT_FOUND
            )
        elif not user.is_active:
            error_response = self._error_response(email, "User is not active.")

        return accessible_orgs, accessible_courses, user, error_response

    def _handle_role_assignment_entry(
        self,
        data,
        auth_user,
        accessible_orgs,
        accessible_courses,
        user,
        course_key_cache,
    ):
        """Validate and perform the action for a single operation entry."""

        course_id = data.get("course_id")
        role = data.get("role")
        action = data.get("action")

        # Validate required fields
        if not all([course_id, role, action]):
            return self._make_result(
                data,
                outcome="failed",
                error="Missing required fields: 'course_id', 'role', and 'action' are all required.",
            )

        # Validate role field
        if role not in {"instructor", "staff"}:
            return self._make_result(
                data, "failed", "Invalid role. Must be 'instructor' or 'staff'."
            )

        # Validate action field
        if action not in {"assign", "revoke"}:
            return self._make_result(
                data, "failed", "Invalid action. Must be 'assign' or 'revoke'."
            )

        # Validate course
        course_key = self._validate_course(course_id, course_key_cache)
        if not course_key:
            return self._make_result(
                data, "failed", "Invalid course_id or course not found."
            )

        # Ensure only admin/staff or authorized instructors can manage roles for this course/org
        if not (auth_user.is_staff or auth_user.is_superuser) and (
            course_id not in accessible_courses
            # Lowercase course org for case-insensitive compare
            and course_key.org.lower() not in accessible_orgs
        ):
            return self._make_result(
                data,
                "failed",
                f"You do not have instructor access to course '{course_id}' or org '{course_key.org}'.",
            )

        # Perform role update based on action
        return self._perform_role_action(data, role, action, user, course_key)

    def _validate_course(self, course_id, cache):
        """
        Validate that course_id is well-formed and course exists.
        Caches results to minimize DB queries.
        """
        if course_id in cache:
            return cache[course_id]

        try:
            course_key = CourseKey.from_string(course_id)
            if not CourseOverview.course_exists(course_key):
                course_key = None
        except Exception as exc:  # pylint: disable=broad-except
            course_key = None

        cache[course_id] = course_key
        return course_key

    def _get_user(self, email, cache):
        """Fetch or get cached user by email."""
        if email in cache:
            return cache[email]

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            user = None

        cache[email] = user
        return user

    def _perform_role_action(self, data, role, action, user, course_key):
        """Assign or revoke role for the user on the course."""
        try:
            if action == "assign":
                self._assign_role(user, role, course_key)
            elif action == "revoke":
                self._revoke_role(user, role, course_key)
            else:
                return self._make_result(
                    data, "failed", "Invalid action. Use 'assign' or 'revoke'."
                )
            # Clear user roles cache
            if hasattr(user, '_roles'):
                del user._roles
            return self._make_result(data, "success")
        except Exception as exc:  # pylint: disable=broad-except
            return self._make_result(data, "failed", str(exc))

    def _assign_role(self, user, role, course_key):
        """
        Assign role with single entry.
        """
        existing_role = CourseAccessRole.objects.filter(
            user=user,
            course_id=course_key,
            org=course_key.org,
            role__in=["staff", "instructor"]
        ).first()

        if existing_role:
            existing_role.role = role
            existing_role.save()
        else:
            CourseAccessRole.objects.create(
                user=user,
                role=role,
                course_id=course_key,
                org=course_key.org,
            )
        CourseEnrollment.enroll(user, course_key)

    def _revoke_role(self, user, role, course_key):
        """
        Revoke role with cleanup - instructor revoke removes all access.
        """
        existing_roles = CourseAccessRole.objects.filter(
            user=user, course_id=course_key, role__in=["staff", "instructor"]
        )
        existing_roles.delete()

    def _make_result(self, data, outcome, error=None):
        """
        Formats the response for each role assignment entry.
        """
        result = {
            "course_id": data.get("course_id"),
            "role": data.get("role"),
            "action": data.get("action"),
            "status": outcome,
        }
        if error:
            result["error"] = error
        return result


class BulkUnenrollAPIView(GenericAPIView):
    """
    POST a single-column CSV of course ids to stage a *validated* batch — a dry-run
    preview that unenrolls nothing; GET lists batches, newest first. Global staff
    only, not the support-role gate: whole courses are a far wider blast radius.
    """

    permission_classes = (IsAuthenticated, IsAdminUser)
    parser_classes = (MultiPartParser, FormParser)
    serializer_class = BulkUnenrollBatchSerializer
    pagination_class = DefaultPagination

    def get(self, request):
        """
        List batches, newest first, optionally filtered by state.

        Otherwise a batch is only reachable by its uuid, surfaced once in the URL
        after confirming — an operator who closed that tab has no way back to a run
        with hours left. Not scoped to ``request.user``: every caller is global
        staff, and losing a colleague's batch is the same operational problem.
        """
        batches = BulkUnenrollBatch.objects.select_related("requester").order_by("-created")

        # Comma-separated so one request can ask for all in-flight states at once.
        states = _parse_state_filter(request, BulkUnenrollBatch.State)
        if states:
            batches = batches.filter(state__in=states)

        page = self.paginate_queryset(batches)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def post(self, request):
        """Parse + validate the CSV, count active enrollments, persist a validated batch."""
        file_obj = request.FILES.get("file")
        if not file_obj:
            raise ValidationError({"file": "A CSV file is required."})

        max_file_bytes = settings.BULK_UNENROLL_MAX_FILE_BYTES
        max_rows = settings.BULK_UNENROLL_MAX_ROWS

        if file_obj.size > max_file_bytes:
            return Response(
                {"detail": f"File exceeds the maximum size of {max_file_bytes} bytes."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        # Enforced *while parsing*, over physical data rows: an oversized file —
        # duplicates and invalid ids included — is never parsed in full.
        try:
            course_keys, errors = parse_bulk_unenroll_csv(file_obj, max_rows=max_rows)
        except BulkUnenrollCsvTooManyRows as exc:
            return Response(
                {"detail": f"File exceeds the maximum of {exc.max_rows} rows."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BulkUnenrollCsvUnreadable as exc:
            # A file we cannot decode or parse is bad input, not a server fault:
            # answer it like any other rejected upload instead of raising a 500.
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if not course_keys:
            return Response(
                {"detail": "No valid course ids found.", "errors": errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # One query: which of the course ids actually have a CourseOverview.
        existing = set(
            CourseOverview.objects.filter(id__in=course_keys).values_list("id", flat=True)
        )
        # One grouped query: active-enrollment count per course (0 if absent).
        counts = dict(
            CourseEnrollment.objects
            .filter(course_id__in=course_keys, is_active=True)
            .order_by()   # no sort on a grouped query (CourseEnrollment has Meta.ordering)
            .values_list("course_id")
            .annotate(active=Count("id"))
        )

        with transaction.atomic():
            batch = BulkUnenrollBatch.objects.create(
                requester=request.user,
                csv_filename=getattr(file_obj, "name", "")[:255],
                total_courses=len(course_keys),
                state=BulkUnenrollBatch.State.VALIDATED,
            )
            course_states = [
                BulkUnenrollCourseState(
                    batch=batch,
                    course_id=course_key,
                    active_count=counts.get(course_key, 0),
                    # Flag (do not drop) course ids with no CourseOverview — likely
                    # a typo, but enrollments can exist without one, so it's a signal.
                    error="" if course_key in existing else "Course not found",
                )
                for course_key in course_keys
            ]
            BulkUnenrollCourseState.objects.bulk_create(course_states)

        response = BulkUnenrollBatchSerializer(batch).data
        response["totals"] = {"active": sum(counts.values())}
        response["courses"] = BulkUnenrollCourseStateSerializer(course_states, many=True).data
        response["errors"] = errors
        return Response(response, status=status.HTTP_200_OK)


class BulkUnenrollConfirmAPIView(GenericAPIView):
    """
    Flip a reviewed batch ``validated -> pending`` and dispatch the engine.

    ``reason`` is collected here, not at upload, so the preview-first UI need not
    demand it up front; it is attached to every audit row.
    """

    permission_classes = (IsAuthenticated, IsAdminUser)
    pagination_class = None

    def post(self, request, batch_id):
        """Validate the reason + state, flip to ``pending``, dispatch the worker."""
        batch = _get_batch_or_404(batch_id)

        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "A non-blank reason is required to confirm."})

        # Claim the transition on a locked, freshly-read row, or two concurrent
        # confirms could both pass the guard and dispatch two engines.
        with transaction.atomic():
            batch = BulkUnenrollBatch.objects.select_for_update().get(pk=batch.pk)
            if batch.state != BulkUnenrollBatch.State.VALIDATED:
                return _conflict(f"Batch is '{batch.state}', not 'validated'; it cannot be confirmed.")
            batch.reason = reason[:255]
            batch.state = BulkUnenrollBatch.State.PENDING
            batch.save(update_fields=["reason", "state", "modified"])

        # Queued for after the commit, so the worker can never read a row this
        # request still holds locked. See _dispatch_or_rollback.
        _dispatch_or_rollback(batch, "batch", lambda: (
            BulkUnenrollBatch.objects.filter(
                pk=batch.pk, state=BulkUnenrollBatch.State.PENDING,
            ).update(state=BulkUnenrollBatch.State.VALIDATED, reason="", modified=now())
        ))

        return _batch_response(batch, status.HTTP_202_ACCEPTED)


class BulkUnenrollStatusAPIView(GenericAPIView):
    """
    A batch's progress, polled by the UI: batch summary plus a page of course rows.

    A batch can hold 1000 courses, so the rows are paginated and the summary is one
    aggregate query rather than a sum over 1000 rows in Python.
    """

    permission_classes = (IsAuthenticated, IsAdminUser)
    serializer_class = BulkUnenrollCourseStateSerializer
    pagination_class = DefaultPagination

    def get(self, request, batch_id):
        """Return the batch summary plus a paginated (optionally filtered) course list."""
        batch = _get_batch_or_404(batch_id)

        courses = batch.courses.all().order_by("course_id")
        # Optional comma-separated filter (same as the batch list), e.g.
        # ?state=failed or the UI's "in progress" == ?state=pending,running.
        states = _parse_state_filter(request, BulkUnenrollCourseState.State)
        if states:
            courses = courses.filter(state__in=states)

        # One aggregate query, over the *unfiltered* batch: the state filter
        # narrows the listed rows only, so progress stays batch-wide.
        totals = batch.courses.aggregate(
            active=Sum("active_count"),
            unenrolled=Sum("unenrolled"),
            already_inactive=Sum("already_inactive"),
            failed=Sum("failed_count"),
            # "Finished" is exactly the engine's terminal set, so the progress the
            # UI shows and the state the engine acts on cannot drift apart.
            courses_finished=Count(
                "pk", filter=Q(state__in=BulkUnenrollCourseState.TERMINAL_STATES),
            ),
        )

        page = self.paginate_queryset(courses)
        courses_data = self.get_serializer(page, many=True).data

        response = BulkUnenrollBatchSerializer(batch).data
        # Coalesce to 0: Sum() returns None on an empty batch, and the UI renders
        # these numbers directly rather than null-checking each one.
        response["totals"] = {
            "active": totals["active"] or 0,
            "unenrolled": totals["unenrolled"] or 0,
            "already_inactive": totals["already_inactive"] or 0,
            "failed": totals["failed"] or 0,
            "courses_finished": totals["courses_finished"] or 0,
        }
        response["courses"] = self.get_paginated_response(courses_data).data
        return Response(response, status=status.HTTP_200_OK)


def _get_batch_or_404(batch_id):
    """Look up a batch by its public uuid, raising DRF NotFound on miss/bad id."""
    try:
        return BulkUnenrollBatch.objects.get(uuid=batch_id)
    except (BulkUnenrollBatch.DoesNotExist, DjangoValidationError, ValueError, TypeError) as exc:
        raise NotFound("Batch not found.") from exc


class BulkUnenrollCancelAPIView(GenericAPIView):
    """
    Stop a batch that is still in flight, sweeping its unfinished course rows.

    Cancel stops *future* work only: learners already processed are not re-enrolled,
    and each running chunk stops within ``BULK_UNENROLL_CANCEL_CHECK_EVERY`` learners.
    """

    permission_classes = (IsAuthenticated, IsAdminUser)
    pagination_class = None

    #: States from which a cancel is still meaningful.
    CANCELLABLE = (
        BulkUnenrollBatch.State.VALIDATED,
        BulkUnenrollBatch.State.PENDING,
        BulkUnenrollBatch.State.RUNNING,
    )

    def post(self, request, batch_id):
        """Mark the batch cancelled if it is still in flight."""
        batch = _get_batch_or_404(batch_id)
        # Locked re-read: the engine may have finalized this batch since the row was
        # read, and a stale write would resurrect a terminal batch as 'cancelled'.
        with transaction.atomic():
            batch = BulkUnenrollBatch.objects.select_for_update().get(pk=batch.pk)
            if batch.state not in self.CANCELLABLE:
                return _conflict(f"Batch is '{batch.state}'; it can no longer be cancelled.")
            batch.state = BulkUnenrollBatch.State.CANCELLED
            batch.save(update_fields=["state", "modified"])
            # Conditional on purpose: a course the engine just finalized keeps
            # its earned state; only unfinished work is reported cancelled.
            batch.courses.filter(
                state__in=(
                    BulkUnenrollCourseState.State.PENDING,
                    BulkUnenrollCourseState.State.RUNNING,
                ),
            ).update(
                state=BulkUnenrollCourseState.State.CANCELLED, finished=now(), modified=now(),
            )
        return _batch_response(batch, status.HTTP_200_OK)


class BulkUnenrollRetryAPIView(GenericAPIView):
    """
    Re-run only the ``failed`` courses of a settled batch.

    Their rows are reset to ``pending`` and re-dispatched; the dispatcher queues only
    ``pending`` courses, so successes are untouched and the engine skips learners
    already inactive.
    """

    permission_classes = (IsAuthenticated, IsAdminUser)
    pagination_class = None

    #: A retry only makes sense once the batch has settled with failures.
    RETRYABLE = (
        BulkUnenrollBatch.State.FAILED,
        BulkUnenrollBatch.State.PARTIAL,
    )

    def post(self, request, batch_id):
        """Reset failed courses to pending and re-dispatch."""
        batch = _get_batch_or_404(batch_id)

        # One locked transaction for the whole claim: two retries on the same batch
        # would otherwise both pass the guard and dispatch two engines over it.
        with transaction.atomic():
            batch = BulkUnenrollBatch.objects.select_for_update().get(pk=batch.pk)
            previous_state = batch.state
            if batch.state not in self.RETRYABLE:
                return _conflict(f"Batch is '{batch.state}'; nothing to retry.")

            failed = batch.courses.filter(state=BulkUnenrollCourseState.State.FAILED)
            if not failed.exists():
                return _conflict("Batch has no failed courses to retry.")

            # Bump the generation (not delete the ledger) so an old-attempt straggler
            # can't claim chunk 0; the snapshot lets the rollback restore diagnostics.
            before = {row.pop("pk"): row for row in failed.values("pk", *RESET_FIELDS)}
            failed.update(
                state=BulkUnenrollCourseState.State.PENDING,
                attempt=F("attempt") + 1,
                modified=now(),
                **{field: RESET_FIELDS[field] for field in RESET_FIELDS},
            )
            batch.state = BulkUnenrollBatch.State.PENDING
            batch.save(update_fields=["state", "modified"])

        # Restores courses (row by row — each carries its own counters back) and
        # the batch. The bumped attempt stays bumped: those identities are spent.
        def _undo_reset():
            for pk, fields in before.items():
                BulkUnenrollCourseState.objects.filter(
                    pk=pk, state=BulkUnenrollCourseState.State.PENDING,
                ).update(
                    state=BulkUnenrollCourseState.State.FAILED, modified=now(), **fields,
                )
            BulkUnenrollBatch.objects.filter(
                pk=batch.pk, state=BulkUnenrollBatch.State.PENDING,
            ).update(state=previous_state, modified=now())

        _dispatch_or_rollback(batch, "retry", _undo_reset)

        return _batch_response(batch, status.HTTP_202_ACCEPTED)
