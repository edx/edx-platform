"""
Video Configuration Service for XBlock runtime.

This service provides video-related configuration and feature flags
that are specific to the edx-platform implementation
for the extracted video block in xblocks-contrib repository.
"""

import logging
from typing import Any, Dict, Optional

from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.video_config.models import (
    CourseYoutubeBlockedFlag,
    HLSPlaybackEnabledFlag,
)
from openedx.core.djangoapps.video_config.toggles import TRANSCRIPT_FEEDBACK
from openedx.core.djangoapps.video_pipeline.config.waffle import DEPRECATE_YOUTUBE
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel
from openedx.core.lib.xblock_services.video_config_utils import (
    get_public_video_url,
    is_public_sharing_enabled,
)
from organizations.api import get_course_organization


log = logging.getLogger(__name__)


class VideoConfigService:
    """
    Service for providing video-related configuration and feature flags.
    
    This service abstracts away edx-platform specific functionality
    that the Video XBlock needs, allowing the Video XBlock to be
    extracted to a separate repository.
    """

    def __init__(self, course_id: Optional[CourseKey] = None):
        """
        Initialize the VideoConfigService.
        
        Args:
            course_id: The course key for course-specific configurations
        """
        self.course_id = course_id

    def is_hls_playback_enabled(self, course_id) -> bool:
        """
        Check if HLS playback is enabled for the course.
        
        Arguments:
            course_id (CourseKey): course id for whom feature will be checked.

        Returns:
            bool: True if HLS playback is enabled, False otherwise
        """
        return HLSPlaybackEnabledFlag.feature_enabled(course_id)

    def is_youtube_deprecated(self, course_id: CourseKey) -> bool:
        """
        Check if YouTube is deprecated for the course.
        
        Args:
            course_id: The course key
            
        Returns:
            bool: True if YouTube is deprecated, False otherwise
        """
        return DEPRECATE_YOUTUBE.is_enabled(course_id)

    def is_youtube_blocked_for_course(self, course_id: CourseKey) -> bool:
        """
        Check if YouTube is blocked for the course.
        
        Args:
            course_id: The course key
            
        Returns:
            bool: True if YouTube is blocked, False otherwise
        """
        return CourseYoutubeBlockedFlag.feature_enabled(course_id)

    def is_transcript_feedback_enabled(self, course_id: CourseKey) -> bool:
        """
        Check if transcript feedback is enabled for the course.
        
        Args:
            course_id: The course key
            
        Returns:
            bool: True if transcript feedback is enabled, False otherwise
        """
        return TRANSCRIPT_FEEDBACK.is_enabled(course_id)

    def get_public_sharing_context(self, video_block, course_id):
        """
        Get the complete public sharing context for a video.
        
        Args:
            video_block: The video XBlock instance
            course_id: The course identifier
            
        Returns:
            dict: Context dictionary with sharing information, empty if sharing is disabled
        """
        context = {}

        if not is_public_sharing_enabled(video_block):
            return context

        try:
            # Get public video URL
            public_video_url = get_public_video_url(video_block)
            context['public_sharing_enabled'] = True
            context['public_video_url'] = public_video_url

            # Get course organization for branding
            organization = get_course_organization(self.course_id)

            # Get sharing sites information
            from xmodule.video_block.sharing_sites import sharing_sites_info_for_video
            sharing_sites_info = sharing_sites_info_for_video(
                public_video_url,
                organization=organization
            )
            context['sharing_sites_info'] = sharing_sites_info

        except Exception as err:
            log.exception(f"Error getting public sharing context for course ID: {course_id}")
            # Return empty context on error
            context = {}

        return context
