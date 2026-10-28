"""
Tests for the plugin override points in the course_modes Python API.
"""

from django.test import SimpleTestCase, override_settings
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.api import get_extra_offer
from common.djangoapps.course_modes.track_selection_data import (
    TrackSelectionRedirect,
    TrackSelectionSubmissionError,
    handle_unknown_track_choice,
)

COURSE_KEY = CourseKey.from_string("course-v1:edX+DemoX+Demo_Course")


def offer_override(prev_fn, request, course_key):
    """Returns an offer and the value the next implementation returns."""
    return {"course_id": str(course_key), "previous": prev_fn(request, course_key)}


def second_offer_override(prev_fn, request, course_key):
    """Adds to whatever the previous override returned."""
    return {**prev_fn(request, course_key), "second": True}


def failing_offer_override(prev_fn, request, course_key):
    """Fails like a plugin whose status service is down."""
    raise RuntimeError("Status service is down")


def list_offer_override(prev_fn, request, course_key):
    """Returns something that is not a dict."""
    return ["not", "a", "dict"]


def choice_override(prev_fn, request, course_key, mode):
    """Handles only "subscribe"."""
    if mode == "subscribe":
        return TrackSelectionRedirect(url="https://checkout.example.com/subscribe")
    return prev_fn(request, course_key, mode)


def failing_choice_override(prev_fn, request, course_key, mode):
    """Fails like a plugin whose checkout service is down."""
    raise RuntimeError("Checkout is down")


def url_choice_override(prev_fn, request, course_key, mode):
    """Returns a bare URL instead of a TrackSelectionRedirect."""
    return "https://checkout.example.com/subscribe"


class GetExtraOfferTests(SimpleTestCase):
    """
    Tests for course_modes.api.get_extra_offer.
    """

    def test_returns_none_by_default(self):
        assert get_extra_offer(request=object(), course_key=COURSE_KEY) is None

    @override_settings(OVERRIDE_COURSE_MODES_GET_EXTRA_OFFER=f"{__name__}.offer_override")
    def test_override_can_call_the_default(self):
        offer = get_extra_offer(request=object(), course_key=COURSE_KEY)
        assert offer == {"course_id": str(COURSE_KEY), "previous": None}

    @override_settings(
        OVERRIDE_COURSE_MODES_GET_EXTRA_OFFER=[f"{__name__}.offer_override", f"{__name__}.second_offer_override"]
    )
    def test_overrides_can_be_chained(self):
        offer = get_extra_offer(request=object(), course_key=COURSE_KEY)
        assert offer == {"course_id": str(COURSE_KEY), "previous": None, "second": True}

    @override_settings(OVERRIDE_COURSE_MODES_GET_EXTRA_OFFER=f"{__name__}.failing_offer_override")
    def test_failing_override_returns_none(self):
        with self.assertLogs("common.djangoapps.course_modes.api", level="ERROR"):
            assert get_extra_offer(request=object(), course_key=COURSE_KEY) is None

    @override_settings(OVERRIDE_COURSE_MODES_GET_EXTRA_OFFER=f"{__name__}.list_offer_override")
    def test_non_dict_override_returns_none(self):
        with self.assertLogs("common.djangoapps.course_modes.api", level="ERROR"):
            assert get_extra_offer(request=object(), course_key=COURSE_KEY) is None


class HandleUnknownTrackChoiceTests(SimpleTestCase):
    """
    Tests for track_selection_data.handle_unknown_track_choice.
    """

    def test_returns_none_by_default(self):
        assert handle_unknown_track_choice(request=object(), course_key=COURSE_KEY, mode="subscribe") is None

    @override_settings(OVERRIDE_COURSE_MODES_HANDLE_UNKNOWN_TRACK_CHOICE=f"{__name__}.choice_override")
    def test_override_handles_its_choice_and_passes_on_the_rest(self):
        handled = handle_unknown_track_choice(request=object(), course_key=COURSE_KEY, mode="subscribe")
        assert handled == TrackSelectionRedirect(url="https://checkout.example.com/subscribe")
        assert handle_unknown_track_choice(request=object(), course_key=COURSE_KEY, mode="other") is None

    @override_settings(OVERRIDE_COURSE_MODES_HANDLE_UNKNOWN_TRACK_CHOICE=f"{__name__}.failing_choice_override")
    def test_failing_override_returns_an_error(self):
        with self.assertLogs("common.djangoapps.course_modes.track_selection_data", level="ERROR"):
            handled = handle_unknown_track_choice(request=object(), course_key=COURSE_KEY, mode="subscribe")
        assert handled == TrackSelectionSubmissionError(error="Could not process this track choice")

    @override_settings(OVERRIDE_COURSE_MODES_HANDLE_UNKNOWN_TRACK_CHOICE=f"{__name__}.url_choice_override")
    def test_unexpected_result_returns_an_error(self):
        with self.assertLogs("common.djangoapps.course_modes.track_selection_data", level="ERROR"):
            handled = handle_unknown_track_choice(request=object(), course_key=COURSE_KEY, mode="subscribe")
        assert handled == TrackSelectionSubmissionError(error="Could not process this track choice")
