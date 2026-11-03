"""
Tests for Track Selection API in the course_modes REST API.
"""

from datetime import datetime
from unittest import mock

import ddt
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from openedx_filters import PipelineStep
from rest_framework.test import APITestCase

from cms.djangoapps.contentstore.outlines import update_outline_from_modulestore
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.course_modes.toggles import (
    COURSE_MODES_MFE_TRACK_SELECTION,
    COURSE_MODES_TRACK_SELECTION_OPTIONS,
)
from common.djangoapps.course_modes.track_selection_data import get_track_options
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.commerce.models import CommerceConfiguration
from lms.djangoapps.verify_student.models import VerificationDeadline
from openedx.core.djangoapps.content.course_overviews.tests.factories import (
    CourseOverviewFactory,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


TEST_LIST_PRICE = 149
TEST_DISCOUNTED_PRICE = 99
TEST_SUBSCRIBE_URL = "https://checkout.example.com/subscribe"
TEST_CATALOG_OFFER = {"in_catalog": True, "subscribe_url": TEST_SUBSCRIBE_URL}
TEST_PROGRAM_UUID = "6d1b9c2e-3f4a-4b5c-8d6e-7f8a9b0c1d2e"
TEST_OTHER_PROGRAM_UUID = "0a7f3e5d-9c1b-4e2a-b6d8-5c4f3e2d1b0a"


class TestCourseModePriceRequestedPipelineStep(PipelineStep):
    """
    Utility pipeline step that overrides a course mode price with a fixed discounted price.
    """

    def run_filter(self, user, course_mode_data, price):  # pylint: disable=arguments-differ
        """Pipeline step that returns a discounted price for the given course mode."""
        return {
            "user": user,
            "course_mode_data": course_mode_data,
            "price": TEST_DISCOUNTED_PRICE,
        }


# Built from the module and class names rather than hardcoded, so the dotted path stays
# correct if this test module is moved or the step class is renamed.
TEST_STEP_PATH = f"{__name__}.{TestCourseModePriceRequestedPipelineStep.__name__}"


class TestTrackSelectionAPIRenderStartedPipelineStep(PipelineStep):
    """
    Utility pipeline step that adds a field to the track selection data.
    """

    def run_filter(self, user, course_key, track_selection_data):  # pylint: disable=arguments-differ
        """Pipeline step that adds a field built from the learner and the course."""
        track_selection_data["plugin_field"] = {"course_id": str(course_key), "username": user.username}
        return {"track_selection_data": track_selection_data}


TEST_RENDER_STEP_PATH = f"{__name__}.{TestTrackSelectionAPIRenderStartedPipelineStep.__name__}"


class TestTrackSelectionOfferPipelineStep(PipelineStep):
    """
    Utility pipeline step that adds a subscription offer to the track selection data.
    """

    def run_filter(self, track_selection_data, **kwargs):  # pylint: disable=arguments-differ
        """Pipeline step that offers Subscribe for a course in the catalog."""
        track_selection_data["extra_offer"] = TEST_CATALOG_OFFER
        return {"track_selection_data": track_selection_data}


TEST_OFFER_STEP_PATH = f"{__name__}.{TestTrackSelectionOfferPipelineStep.__name__}"


@ddt.ddt
@skip_unless_lms
@override_waffle_flag(COURSE_MODES_MFE_TRACK_SELECTION, active=True)
@override_waffle_flag(COURSE_MODES_TRACK_SELECTION_OPTIONS, active=True)
class TrackSelectionTestViews(ModuleStoreTestCase, APITestCase):
    """
    Tests for the Track Selection BFF API.
    """

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(
            start=datetime(2020, 1, 1),
            end=datetime(2028, 1, 1),
            enrollment_start=datetime(2020, 1, 1),
            enrollment_end=datetime(2028, 1, 1),
            emit_signals=True,
            modulestore=self.store,
        )
        chapter = BlockFactory(parent=self.course, category="chapter")
        BlockFactory(parent=chapter, category="sequential")

        CourseModeFactory(course_id=self.course.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED,
            expiration_datetime=datetime(2028, 1, 1),
            min_price=TEST_LIST_PRICE,
            sku="ABCD1234",
        )
        VerificationDeadline.objects.create(
            course_key=self.course.id, deadline=datetime(2028, 1, 1)
        )

        CourseOverviewFactory.create(run="1T2020")
        update_outline_from_modulestore(self.course.id)

        self.user, password = self.create_non_staff_user()
        self.client.login(username=self.user.username, password=password)
        self.url = reverse("course_modes_api:v1:track-selection", args=[self.course.id])

    def test_verified_price_is_list_price_without_filter_step(self):
        """
        With no pipeline step configured, the verified mode price is the unmodified list price.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"]["min_price"] == str(TEST_LIST_PRICE)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.price.requested.v1": {
                "pipeline": [
                    TEST_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_verified_price_uses_course_mode_price_requested_filter(self):
        """
        A configured CourseModePriceRequested step overrides the verified mode price.

        This is the hook edx-enterprise uses to apply enterprise-negotiated pricing.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"]["min_price"] == str(TEST_DISCOUNTED_PRICE)

    def test_post_loads_track_selection_page_data(self):
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is not None
        assert response.data["audit_mode"] is not None
        assert response.data["course_name"] == self.course.display_name_with_default
        assert response.data["programs"] == []
        assert response.data["options"] == [{"slug": "purchase", "url": None}, {"slug": "audit", "url": None}]

    def test_get_is_not_supported(self):
        response = self.client.get(self.url)
        assert response.status_code == 405

    def test_post_authenticated_audit_enrolled_user(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is not None
        assert response.data["audit_mode"] is not None
        assert response.data["course_name"] == self.course.display_name_with_default
        # without the upgrade intent, an audit learner may have just enrolled, so Audit stays
        assert [option["slug"] for option in response.data["options"]] == ["purchase", "audit"]

    def test_post_authenticated_verified_enrolled_user_redirects(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert "redirect_url" in response.data
        assert response.data["redirect_url"].endswith(f"/course/{self.course.id}/home")

    def test_post_authenticated_unenrolled_user_with_open_enrollment(self):
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is not None

    def test_post_authenticated_previously_enrolled_user(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        CourseEnrollment.unenroll(self.user, self.course.id)
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is not None

    def test_post_unauthenticated_user(self):
        self.client.logout()
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 401

    def test_post_unknown_course_redirects_to_legacy_choose(self):
        url = reverse(
            "course_modes_api:v1:track-selection",
            args=["course-v1:unknown+course+2T2020"],
        )
        response = self.client.post(url, {}, format="json")
        assert response.status_code == 200
        assert "redirect_url" in response.data
        assert response.data["redirect_url"].endswith(
            "/course_modes/choose/course-v1:unknown+course+2T2020/"
        )

    @override_waffle_flag(COURSE_MODES_MFE_TRACK_SELECTION, active=False)
    def test_waffle_flag_disabled(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 404

    def test_post_audit_track_selection_enrolls_and_redirects(self):
        response = self.client.post(self.url, {"mode": CourseMode.AUDIT}, format="json")
        assert response.status_code == 200
        assert "redirect_url" in response.data

        mode, is_active = CourseEnrollment.enrollment_mode_for_user(
            self.user, self.course.id
        )
        assert mode == CourseMode.AUDIT
        assert is_active

    def test_post_verified_track_selection_redirects_to_verification(self):
        response = self.client.post(
            self.url,
            {"mode": CourseMode.VERIFIED, "contribution": "149"},
            format="json",
        )
        assert response.status_code == 200
        assert "redirect_url" in response.data

    def test_post_invalid_mode_returns_error(self):
        response = self.client.post(self.url, {"mode": "unsupported"}, format="json")
        assert response.status_code == 400
        assert response.data["error"] == "Enrollment mode not supported"

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_RENDER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_post_page_data_uses_track_selection_render_filter(self):
        """
        A configured TrackSelectionAPIRenderStarted step can add fields to the page data.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["plugin_field"] == {
            "course_id": str(self.course.id),
            "username": self.user.username,
        }
        assert response.data["verified_mode"] is not None

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_OFFER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_post_page_data_options_use_the_plugin_offer(self):
        """
        Core picks the options after the filter, from the offer a plugin adds.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["options"] == [
            {"slug": "subscribe", "url": TEST_SUBSCRIBE_URL},
            {"slug": "audit", "url": None},
        ]

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_OFFER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    @mock.patch(
        "common.djangoapps.course_modes.track_selection_data.get_track_options",
        return_value=[{"slug": "subscribe", "url": TEST_SUBSCRIBE_URL}],
    )
    def test_post_single_option_redirects_to_its_link(self, _mock_get_track_options):
        """
        With a single option that has a link, the learner skips the page and goes to its link.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data == {"redirect_url": TEST_SUBSCRIBE_URL}

    @staticmethod
    def _extended_program(uuid, title, eligible=True, **fields):
        """Return a program as ProgramDataExtender returns it."""
        return {
            "uuid": uuid,
            "title": title,
            "courses": [{}, {}, {}],
            "is_learner_eligible_for_one_click_purchase": eligible,
            "skus": ["SKU1", "SKU2"],
            "discount_data": {"total_incl_tax_excl_discounts": 300.0, "total_incl_tax": 270.0, "currency": "USD"},
            **fields,
        }

    @mock.patch("common.djangoapps.course_modes.track_selection_data.ProgramDataExtender")
    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_programs")
    def test_post_page_data_offers_the_programs(self, mock_get_programs, mock_extender):
        """
        The active programs of the course run that the learner can buy in one click are listed, in order.
        The first one is also an option.
        """
        mock_get_programs.return_value = [
            {"status": "retired"},
            {"status": "active", "name": "first"},
            {"status": "active", "name": "not eligible"},
            {"status": "active", "name": "second"},
        ]
        mock_extender.return_value.extend.side_effect = [
            self._extended_program(TEST_PROGRAM_UUID, "First Program"),
            self._extended_program(TEST_OTHER_PROGRAM_UUID, "Not Eligible Program", eligible=False),
            self._extended_program(TEST_OTHER_PROGRAM_UUID, "Second Program"),
        ]
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        programs = response.data["programs"]
        assert [program["title"] for program in programs] == ["First Program", "Second Program"]
        assert programs[0]["url"].endswith(f"?sku=SKU1&sku=SKU2&bundle={TEST_PROGRAM_UUID}")
        assert {key: programs[0][key] for key in ("number_of_courses", "price", "discounted_price", "currency")} == {
            "number_of_courses": 3,
            "price": 300.0,
            "discounted_price": 270.0,
            "currency": "USD",
        }
        assert [option["slug"] for option in response.data["options"]] == ["purchase", "program", "audit"]
        # the learner chooses among the programs on the page, so the option has no link
        assert response.data["options"][1]["url"] is None
        assert [call.args for call in mock_extender.call_args_list] == [
            ({"status": "active", "name": "first"}, self.user),
            ({"status": "active", "name": "not eligible"}, self.user),
            ({"status": "active", "name": "second"}, self.user),
        ]

    @mock.patch("common.djangoapps.course_modes.track_selection_data.ProgramDataExtender")
    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_programs")
    def test_post_page_data_lists_all_the_programs(self, mock_get_programs, mock_extender):
        """
        The page data has every program that the learner can buy, however many the course run is in.
        """
        mock_get_programs.return_value = [{"status": "active"}] * 5
        mock_extender.return_value.extend.side_effect = [
            self._extended_program(TEST_PROGRAM_UUID, f"Program {number}") for number in range(5)
        ]
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert [program["title"] for program in response.data["programs"]] == [f"Program {n}" for n in range(5)]
        assert mock_extender.call_count == 5

    @mock.patch("common.djangoapps.course_modes.track_selection_data.ProgramDataExtender")
    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_programs")
    def test_post_page_data_describes_each_program(self, mock_get_programs, mock_extender):
        """
        Each program has what the Program card shows: logo, duration, description and the courses.
        """
        mock_get_programs.return_value = [{"status": "active"}, {"status": "active"}]
        mock_extender.return_value.extend.side_effect = [
            self._extended_program(
                TEST_PROGRAM_UUID,
                "Full Program",
                organization_logo_override_url="https://logos.test/override.png",
                authoring_organizations=[{"logo_image_url": "https://logos.test/org.png"}],
                weeks_to_complete=22,
                marketing_hook="A hook",
                subtitle="A subtitle",
                courses=[
                    {"title": "Course A", "course_runs": [{"marketing_url": "https://www.edx.org/learn/a"}]},
                    {"title": "Course B", "course_runs": []},
                    {"course_runs": [{"marketing_url": "https://www.edx.org/learn/untitled"}]},
                ],
            ),
            self._extended_program(
                TEST_OTHER_PROGRAM_UUID,
                "Bare Program",
                authoring_organizations=[{"logo_image_url": "https://logos.test/org.png"}],
                subtitle="A subtitle",
                discount_data={"is_discounted": False},
            ),
        ]
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        full, bare = response.data["programs"]
        assert full["logo_url"] == "https://logos.test/override.png"
        assert full["weeks_to_complete"] == 22
        assert full["description"] == "A hook"
        assert full["number_of_courses"] == 3
        assert full["courses"] == [
            {"title": "Course A", "url": "https://www.edx.org/learn/a"},
            {"title": "Course B", "url": None},
        ]
        assert bare["logo_url"] == "https://logos.test/org.png"
        assert bare["weeks_to_complete"] is None
        assert bare["description"] == "A subtitle"
        # without a price from Ecommerce, the program has none
        assert (bare["price"], bare["discounted_price"], bare["currency"]) == (None, None, None)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_OFFER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_post_upgrading_audit_learner_chooses_subscribe_or_purchase(self):
        """
        An audit learner who came to upgrade a catalog course chooses between Subscribe and Purchase.
        """
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.post(self.url, {"intent": "upgrade"}, format="json")
        assert response.status_code == 200
        assert [option["slug"] for option in response.data["options"]] == ["subscribe", "purchase"]

    def test_post_upgrading_audit_learner_goes_to_the_checkout(self):
        """
        An audit learner who came to upgrade a course outside the catalog skips the page.
        """
        CommerceConfiguration.objects.create(checkout_on_ecommerce_service=True)
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.post(self.url, {"intent": "upgrade"}, format="json")
        assert response.status_code == 200
        assert "/basket/add/?sku=ABCD1234&course_run_key=" in response.data["redirect_url"]

    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_programs", side_effect=Exception("down"))
    def test_post_page_data_leaves_out_the_programs_that_cannot_be_read(self, _mock_get_programs):
        """
        An error while reading the programs leaves them out instead of failing the page.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["programs"] == []
        assert [option["slug"] for option in response.data["options"]] == ["purchase", "audit"]

    @mock.patch("common.djangoapps.course_modes.track_selection_data.ProgramDataExtender")
    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_programs")
    def test_post_page_data_leaves_out_only_the_programs_that_cannot_be_read(self, mock_get_programs, mock_extender):
        """
        A program that cannot be read is left out, and the programs after it are still listed.
        """
        mock_get_programs.return_value = [{"status": "active"}] * 3
        program_without_skus = self._extended_program(TEST_OTHER_PROGRAM_UUID, "Program Without SKUs")
        del program_without_skus["skus"]
        mock_extender.return_value.extend.side_effect = [
            Exception("down"),
            program_without_skus,
            self._extended_program(TEST_PROGRAM_UUID, "Last Program"),
        ]
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert [program["title"] for program in response.data["programs"]] == ["Last Program"]

    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_track_options", return_value=[])
    def test_post_without_options_redirects_to_the_course(self, _mock_get_track_options):
        """
        With nothing to choose, the learner skips the page and goes to the course.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["redirect_url"].endswith(f"/course/{self.course.id}/home")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_OFFER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    @ddt.data(
        ({"in_catalog": True, "is_subscriber": True}, True),
        ({"in_catalog": False, "is_subscriber": True}, False),
        # the subscription service is down: the status is unknown
        ({"in_catalog": True, "is_subscriber": None}, False),
        ({"in_catalog": True, "is_subscriber": False}, False),
    )
    @ddt.unpack
    def test_post_subscriber_redirects_to_the_course(self, offer, redirects):
        """
        A subscriber has nothing to choose, so the learner skips the page and goes to the course.
        """
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        with mock.patch(f"{__name__}.TEST_CATALOG_OFFER", offer):
            response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        if redirects:
            assert response.data["redirect_url"].endswith(f"/course/{self.course.id}/home")
        else:
            assert [option["slug"] for option in response.data["options"]] == ["purchase", "audit"]

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_OFFER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_post_subscriber_without_the_verified_mode_gets_the_page(self):
        """
        The subscription upgrades to the verified mode, so a subscriber of a course without it gets the page.
        """
        # a course that sells another track than verified, such as a Master's course run
        CourseMode.objects.filter(course_id=self.course.id, mode_slug=CourseMode.VERIFIED).update(
            mode_slug=CourseMode.MASTERS
        )
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        with mock.patch(f"{__name__}.TEST_CATALOG_OFFER", {"in_catalog": True, "is_subscriber": True}):
            response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is None
        assert response.data["options"] == [{"slug": "audit", "url": None}]

    @override_waffle_flag(COURSE_MODES_TRACK_SELECTION_OPTIONS, active=False)
    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_RENDER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_programs")
    def test_post_page_data_without_the_options_flag(self, mock_get_programs):
        """
        Without the options flag, the page data has no programs and no options, and the filter does not run.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is not None
        assert response.data["audit_mode"] is not None
        for key in ("plugin_field", "programs", "options"):
            assert key not in response.data
        mock_get_programs.assert_not_called()

    @override_waffle_flag(COURSE_MODES_TRACK_SELECTION_OPTIONS, active=False)
    def test_post_upgrade_intent_shows_the_page_without_the_options_flag(self):
        """
        Without the options flag, an upgrading audit learner gets the page data, not a redirect.
        """
        CommerceConfiguration.objects.create(checkout_on_ecommerce_service=True)
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.post(self.url, {"intent": "upgrade"}, format="json")
        assert response.status_code == 200
        assert "redirect_url" not in response.data
        assert response.data["verified_mode"] is not None


@ddt.ddt
@skip_unless_lms
class GetTrackOptionsTests(SimpleTestCase):
    """
    Tests for the options the track selection page shows.
    """

    @ddt.data(
        (None, True, False, False, ["purchase", "audit"]),
        (None, False, False, False, ["purchase"]),
        (None, True, True, False, ["purchase", "program", "audit"]),
        (TEST_CATALOG_OFFER, True, True, False, ["subscribe", "program", "audit"]),
        (TEST_CATALOG_OFFER, False, False, False, ["subscribe", "purchase"]),
        (None, True, True, True, ["purchase", "program"]),
        (TEST_CATALOG_OFFER, True, False, True, ["subscribe", "purchase"]),
    )
    @ddt.unpack
    def test_options(self, extra_offer, has_audit_mode, has_program, upgrading, expected):
        data = {
            "course_id": "course-v1:a+b+c",
            "verified_mode": {"use_ecommerce_payment_flow": False},
            "audit_mode": {"slug": CourseMode.AUDIT} if has_audit_mode else None,
            "programs": [{"url": "https://ecommerce.test/program"}] if has_program else [],
            "extra_offer": extra_offer,
        }
        assert [option["slug"] for option in get_track_options(data, upgrading)] == expected

    def test_program_link_only_for_a_single_program(self):
        data = {
            "course_id": "course-v1:a+b+c",
            "verified_mode": None,
            "audit_mode": None,
            "programs": [{"url": "https://ecommerce.test/program-a"}],
        }
        assert get_track_options(data) == [{"slug": "program", "url": "https://ecommerce.test/program-a"}]
        data["programs"].append({"url": "https://ecommerce.test/program-b"})
        assert get_track_options(data) == [{"slug": "program", "url": None}]

    def test_purchase_link(self):
        data = {
            "course_id": "course-v1:a+b+c",
            "verified_mode": {
                "sku": "ABCD1234",
                "use_ecommerce_payment_flow": True,
                "ecommerce_payment_page": "https://ecommerce.test/basket/add/",
            },
            "audit_mode": None,
        }
        purchase_url = "https://ecommerce.test/basket/add/?sku=ABCD1234&course_run_key=course-v1%3Aa%2Bb%2Bc"
        assert get_track_options(data) == [{"slug": "purchase", "url": purchase_url}]
