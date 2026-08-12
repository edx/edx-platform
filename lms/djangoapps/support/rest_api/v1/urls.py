"""
URL definitions for the support v1 API.
"""

from django.urls import re_path

from .views import (
    BulkUnenrollAPIView,
    BulkUnenrollCancelAPIView,
    BulkUnenrollConfirmAPIView,
    BulkUnenrollRetryAPIView,
    BulkUnenrollStatusAPIView,
    CourseTeamManageAPIView,
)

app_name = "v1"

# A ``batch_id`` is the batch's uuid: 32 hex digits, optionally hyphen-grouped.
BATCH_ID = r"(?P<batch_id>[0-9a-fA-F-]+)"

urlpatterns = [
    re_path(
        r"manage_course_team/?$",
        CourseTeamManageAPIView.as_view(),
        name="manage_course_team",
    ),
    re_path(
        rf"bulk_unenroll/{BATCH_ID}/confirm/?$",
        BulkUnenrollConfirmAPIView.as_view(),
        name="bulk_unenroll_confirm",
    ),
    re_path(
        rf"bulk_unenroll/{BATCH_ID}/cancel/?$",
        BulkUnenrollCancelAPIView.as_view(),
        name="bulk_unenroll_cancel",
    ),
    re_path(
        rf"bulk_unenroll/{BATCH_ID}/retry/?$",
        BulkUnenrollRetryAPIView.as_view(),
        name="bulk_unenroll_retry",
    ),
    re_path(
        rf"bulk_unenroll/{BATCH_ID}/?$",
        BulkUnenrollStatusAPIView.as_view(),
        name="bulk_unenroll_status",
    ),
    re_path(
        r"bulk_unenroll/?$",
        BulkUnenrollAPIView.as_view(),
        name="bulk_unenroll",
    ),
]
