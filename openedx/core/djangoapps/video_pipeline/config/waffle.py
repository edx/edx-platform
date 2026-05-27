"""
This module contains configuration settings via waffle flags
for the Video Pipeline app.
"""

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

# Videos Namespace
WAFFLE_NAMESPACE = 'videos'
LOG_PREFIX = 'Videos: '

# .. toggle_name: videos.deprecate_youtube
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag telling whether youtube is deprecated. When enabled, videos are no longer uploaded
#   to YouTube as part of the video pipeline.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-08-03
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/18765
DEPRECATE_YOUTUBE = CourseWaffleFlag(f'{WAFFLE_NAMESPACE}.deprecate_youtube', __name__, LOG_PREFIX)

ENABLE_VEM_PIPELINE = CourseWaffleFlag(  # lint-amnesty, pylint: disable=toggle-missing-annotation
    f'{WAFFLE_NAMESPACE}.enable_vem_pipeline', __name__, LOG_PREFIX
)
