"""
Serializers for use in the support app.
"""

from datetime import datetime

import pytz
from django.conf import settings
from rest_framework import serializers

from lms.djangoapps.support.models import BulkUnenrollBatch, BulkUnenrollCourseState
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


class CourseTeamManageSerializer(serializers.ModelSerializer):
    """Serializer for course team management context data"""

    role = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    course_url = serializers.SerializerMethodField()

    class Meta:
        model = CourseOverview
        fields = ("id", "display_name", "role", "status", "course_url")

    def get_role(self, obj):
        course_role_map = self.context.get("course_role_map", {})
        return course_role_map.get(str(obj.id))

    def get_status(self, obj):
        """
        Determine if the course is active or archived based on end date.
        Returns 'active' if course end is null or in the future, 'archived' otherwise.
        """
        if obj.end is None or obj.end >= datetime.now().replace(tzinfo=pytz.UTC):
            return "active"
        return "archived"

    def get_course_url(self, obj):
        """
        Construct the course URL for CMS with proper scheme and host.
        """
        scheme = "https" if settings.HTTPS == "on" else "http"
        course_url = f"{scheme}://{settings.CMS_BASE}/course/{str(obj.id)}"
        return course_url

    def to_representation(self, instance):
        data = super().to_representation(instance)
        course_key = instance.id
        return {
            "course_id": str(course_key),
            "course_name": data["display_name"],
            "course_url": data["course_url"],
            "role": data["role"],
            "status": data["status"],
            "org": course_key.org,
            "run": course_key.run,
            "number": course_key.course,
        }


class BulkUnenrollCourseStateSerializer(serializers.ModelSerializer):
    """
    Per-course row within a bulk-unenroll batch.

    Carries both the dry-run preview (``active_count``, set at upload) and the
    worker-populated mutation counters, so the polling UI can show real progress
    for a run that may last hours rather than just a state label.
    """

    course_id = serializers.CharField()

    class Meta:
        model = BulkUnenrollCourseState
        fields = (
            "course_id", "state", "active_count", "error",
            "unenrolled", "already_inactive", "failed_count",
            "chunks_total", "chunks_finished",
        )


class BulkUnenrollBatchSerializer(serializers.ModelSerializer):
    """Aggregate view of a bulk-unenroll batch (the public ``batch_id`` is the uuid)."""

    batch_id = serializers.UUIDField(source="uuid", read_only=True)
    # A uuid is not recognizable: who/which-file is how an operator spots their
    # own run in the list (or a colleague's, for an in-flight batch).
    requester = serializers.CharField(source="requester.username", read_only=True)

    class Meta:
        model = BulkUnenrollBatch
        fields = (
            "batch_id", "state", "reason", "total_courses",
            "requester", "csv_filename", "created", "modified",
        )
