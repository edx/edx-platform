"""
Serializers for all Course Enrollment related return objects.
"""

import logging

from django.db.models import Prefetch
from rest_framework import serializers

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAllowed

log = logging.getLogger(__name__)


def with_verified_mode_prefetch(queryset):
    """Prefetch verified modes so enrollment serialization avoids per-row lookups."""
    return queryset.select_related("user", "course").prefetch_related(
        Prefetch(
            "course__modes",
            queryset=CourseMode.objects.filter(mode_slug=CourseMode.VERIFIED),
            to_attr="prefetched_verified_modes",
        )
    )


class StringListField(serializers.CharField):
    """Custom Serializer for turning a comma delimited string into a list.

    This field is designed to take a string such as "1,2,3" and turn it into an actual list
    [1,2,3]

    """

    def field_to_native(self, obj, field_name):  # pylint: disable=unused-argument
        """
        Serialize the object's class name.
        """
        if not obj.suggested_prices:
            return []

        items = obj.suggested_prices.split(",")
        return [int(item) for item in items]


class CourseSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serialize a course block and related information.
    """

    course_id = serializers.CharField(source="id")
    course_name = serializers.CharField(source="display_name_with_default")
    enrollment_start = serializers.DateTimeField(format=None)
    enrollment_end = serializers.DateTimeField(format=None)
    course_start = serializers.DateTimeField(source="start", format=None)
    course_end = serializers.DateTimeField(source="end", format=None)
    invite_only = serializers.BooleanField(source="invitation_only")
    course_modes = serializers.SerializerMethodField()
    pacing_type = serializers.SerializerMethodField()

    class Meta:
        # For disambiguating within the drf-yasg swagger schema
        ref_name = "enrollment.Course"

    def __init__(self, *args, **kwargs):
        self.include_expired = kwargs.pop("include_expired", False)
        super().__init__(*args, **kwargs)

    def get_course_modes(self, obj):
        """
        Retrieve course modes associated with the course.
        """
        course_modes = CourseMode.modes_for_course(obj.id, include_expired=self.include_expired, only_selectable=False)
        return [ModeSerializer(mode).data for mode in course_modes]

    def get_pacing_type(self, obj):
        """
        Get a string representation of the course pacing.
        """
        return "Self Paced" if obj.self_paced else "Instructor Paced"


class CourseEnrollmentSerializer(serializers.ModelSerializer):
    """Serializes CourseEnrollment models.

    Aggregates all data from the Course Enrollment table, and pulls in the serialization for
    the Course block and course modes, to give a complete representation of course enrollment.

    """

    course_details = CourseSerializer(source="course_overview")
    user = serializers.SerializerMethodField("get_username")
    access_expiration_date = serializers.SerializerMethodField()
    is_audit_with_expiring_upgrade = serializers.SerializerMethodField()

    def get_username(self, model):
        """Retrieves the username from the associated model."""
        return model.username

    def _get_verified_mode(self, obj):
        """Retrieve the verified mode for this enrollment's course."""
        course_overview = obj.course_overview
        if course_overview is None:
            return None

        prefetched_verified_modes = getattr(course_overview, "prefetched_verified_modes", None)
        if prefetched_verified_modes is not None:
            return prefetched_verified_modes[0] if prefetched_verified_modes else None

        verified_mode_by_course = self.context.setdefault("verified_mode_by_course", {})
        if obj.course_id not in verified_mode_by_course:
            verified_mode_by_course[obj.course_id] = CourseMode.objects.filter(
                course_id=obj.course_id,
                mode_slug=CourseMode.VERIFIED,
            ).first()

        return verified_mode_by_course[obj.course_id]

    def get_access_expiration_date(self, obj):
        """Return expiration date for audit enrollments."""

        if obj.mode != CourseMode.AUDIT:
            return None

        verified_mode = self._get_verified_mode(obj)

        if verified_mode and verified_mode.expiration_datetime:
            return verified_mode.expiration_datetime.isoformat().replace('+00:00', 'Z')

        return None

    def get_is_audit_with_expiring_upgrade(self, obj):
        """Return whether an audit enrollment has a verified upgrade expiration."""
        if obj.mode != CourseMode.AUDIT:
            return False

        verified_mode = self._get_verified_mode(obj)
        return bool(verified_mode and verified_mode.expiration_datetime)

    class Meta:
        model = CourseEnrollment
        fields = (
            "created",
            "mode",
            "is_active",
            "course_details",
            "user",
            "access_expiration_date",
            "is_audit_with_expiring_upgrade",
        )
        lookup_field = "username"


class CourseEnrollmentsApiListSerializer(CourseEnrollmentSerializer):
    """
    Serializes CourseEnrollment model and returns a subset of fields returned
    by the CourseEnrollmentSerializer.
    """

    course_id = serializers.CharField(source="course_overview.id")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("course_details")

    class Meta(CourseEnrollmentSerializer.Meta):
        fields = CourseEnrollmentSerializer.Meta.fields + ("course_id",)


class ModeSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Serializes a course's 'Mode' tuples.

    Returns a serialized representation of the modes available for course enrollment. The course
    modes models are designed to return a tuple instead of the model object itself. This serializer
    does not handle the model object itself, but the tuple.

    """

    slug = serializers.CharField(max_length=100)
    name = serializers.CharField(max_length=255)
    min_price = serializers.IntegerField()
    suggested_prices = StringListField(max_length=255)
    currency = serializers.CharField(max_length=8)
    expiration_datetime = serializers.DateTimeField()
    description = serializers.CharField()
    sku = serializers.CharField()
    bulk_sku = serializers.CharField()


class CourseEnrollmentAllowedSerializer(serializers.ModelSerializer):
    """
    Serializes CourseEnrollmentAllowed model

    Aggregates all data from the CourseEnrollmentAllowed table, and pulls in the serialization
    to give a complete representation of course enrollment allowed.
    """

    class Meta:
        model = CourseEnrollmentAllowed
        exclude = ["id"]
        lookup_field = "user"
