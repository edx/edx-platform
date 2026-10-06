"""
Toggles for course modes experience.
"""

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

WAFFLE_FLAG_NAMESPACE = "course_modes"

# .. toggle_name: course_modes.mfe_track_selection
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, GET /course_modes/choose/ redirects to the Learning MFE track
#   selection page (plugin-owned UI). POST enrollment remains on edx-platform.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-07-27
# .. toggle_target_removal_date: 2027-01-27
# .. toggle_tickets: LP-837
COURSE_MODES_MFE_TRACK_SELECTION = CourseWaffleFlag(
    f"{WAFFLE_FLAG_NAMESPACE}.mfe_track_selection", __name__
)


def course_modes_mfe_track_selection_is_active(course_key):
    return not course_key.deprecated and COURSE_MODES_MFE_TRACK_SELECTION.is_enabled(
        course_key
    )
