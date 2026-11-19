"""Tests of the routing of LMS purchase links to the a la carte checkout."""

from datetime import timedelta
from unittest.mock import ANY, Mock, patch
from urllib.parse import urlencode

import ddt
from django.http import QueryDict
from django.test import TestCase
from django.test.utils import override_settings
from django.utils.timezone import now
from edx_django_utils.cache import TieredCache
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.commerce import alacarte_checkout
from lms.djangoapps.commerce.models import CommerceConfiguration
from lms.djangoapps.commerce.toggles import REDIRECT_TO_ALACARTE_CHECKOUT
from lms.djangoapps.commerce.alacarte_checkout import Route
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

COURSE_RUN_KEY = 'course-v1:edX+DemoX+1T2026'
COURSE_UUID = '11111111-2222-3333-4444-555555555555'
CHECKOUT_URL = 'https://learner-home.example.com/aca-checkout'
LEGACY_CHECKOUT_URL = 'http://ecommerce_url/basket/add/'
MAX_CODE_LENGTH = alacarte_checkout.MAX_DISCOUNT_CODE_LENGTH


def alacarte_url(**params):
    """The a la carte checkout URL with the parameters."""
    return f'{CHECKOUT_URL}?{urlencode(params)}'


def create_course_modes(course_run_key, *modes):
    """Create the course overview of the course run, and its course modes (dicts of CourseModeFactory arguments)."""
    course_key = CourseKey.from_string(course_run_key)
    CourseOverviewFactory(id=course_key)
    for mode in modes:
        CourseModeFactory(course_id=course_key, **mode)
    return course_key


@skip_unless_lms
@ddt.ddt
@override_settings(
    LEARNER_HOME_SITE_URL='https://learner-home.example.com',
    ECOMMERCE_PUBLIC_URL_ROOT='http://ecommerce_url',
)
@override_waffle_flag(REDIRECT_TO_ALACARTE_CHECKOUT, active=True)
class RouteCheckoutTests(TestCase):
    """Tests of route_checkout."""

    def setUp(self):
        super().setUp()
        TieredCache.dangerous_clear_all_tiers()
        CommerceConfiguration.objects.create(checkout_on_ecommerce_service=True, basket_checkout_page='/basket/add/')

        create_course_modes(
            COURSE_RUN_KEY,
            {'mode_slug': CourseMode.AUDIT},
            {'mode_slug': CourseMode.VERIFIED, 'sku': 'VERIFIEDSKU', 'bulk_sku': 'BULKSKU'},
        )

        patcher = patch.object(alacarte_checkout, '_fetch_course_uuid', return_value=COURSE_UUID)
        self.mock_fetch_course_uuid = patcher.start()
        self.addCleanup(patcher.stop)

    def route(self, query):
        """Route a checkout link with the query parameters (a dict, or a list of pairs for repeated names)."""
        return alacarte_checkout.route_checkout(QueryDict(urlencode(query)))

    def legacy_route(self, query, reason):
        """The Route to the ecommerce checkout with the query parameters."""
        return Route(f'{LEGACY_CHECKOUT_URL}?{QueryDict(urlencode(query)).urlencode()}', reason)

    @ddt.data(
        {'sku': 'VERIFIEDSKU', 'course_run_key': COURSE_RUN_KEY},
        # the a la carte checkout doesn't need the SKU, and the upgrade link has sku=None when the mode has none
        {'course_run_key': COURSE_RUN_KEY},
        {'sku': 'None', 'course_run_key': COURSE_RUN_KEY},
        # the legacy track selection page and the legacy dashboard send the SKU only
        {'sku': 'VERIFIEDSKU'},
    )
    def test_course(self, query):
        expected_url = alacarte_url(course_uuid=COURSE_UUID, course_run_key=COURSE_RUN_KEY)

        assert self.route(query) == Route(expected_url, 'course')

    def test_repeated_values(self):
        """A plugin override can append the course run key and the code again: still one product."""
        query = [
            ('sku', 'VERIFIEDSKU'),
            ('course_run_key', COURSE_RUN_KEY),
            ('course_run_key', COURSE_RUN_KEY),
            ('code', 'EDXWELCOME'),
            ('code', 'EDXWELCOME'),
        ]

        assert self.route(query) == Route(
            alacarte_url(course_uuid=COURSE_UUID, course_run_key=COURSE_RUN_KEY, discount_code='EDXWELCOME'), 'course'
        )

    def test_course_run_key_with_space(self):
        """A literal "+" in an old link decodes to a space."""
        route = alacarte_checkout.route_checkout(QueryDict('course_run_key=course-v1:edX+DemoX+1T2026'))

        assert route == Route(alacarte_url(course_uuid=COURSE_UUID, course_run_key=COURSE_RUN_KEY), 'course')

    @ddt.data(
        (True, {'discount_code': 'EDXWELCOME'}),
        (False, {}),
    )
    @ddt.unpack
    def test_discount_code(self, forward_codes, expected_params):
        with override_settings(ALACARTE_CHECKOUT_FORWARD_DISCOUNT_CODES=forward_codes):
            route = self.route({'sku': 'VERIFIEDSKU', 'course_run_key': COURSE_RUN_KEY, 'code': 'EDXWELCOME'})

        assert route.url == alacarte_url(course_uuid=COURSE_UUID, course_run_key=COURSE_RUN_KEY, **expected_params)

    @ddt.data(
        ('A' * MAX_CODE_LENGTH, {'discount_code': 'A' * MAX_CODE_LENGTH}),
        # a longer code is dropped, not cut: a cut code would be another code
        ('A' * (MAX_CODE_LENGTH + 1), {}),
    )
    @ddt.unpack
    def test_long_discount_code(self, code, expected_params):
        route = self.route({'course_run_key': COURSE_RUN_KEY, 'code': code})

        assert route.url == alacarte_url(course_uuid=COURSE_UUID, course_run_key=COURSE_RUN_KEY, **expected_params)

    def test_utm_parameters(self):
        route = self.route({'course_run_key': COURSE_RUN_KEY, 'utm_source': 'email', 'other': 'dropped'})

        assert route.url == alacarte_url(course_uuid=COURSE_UUID, course_run_key=COURSE_RUN_KEY, utm_source='email')

    @ddt.data(
        ({'sku': 'VERIFIEDSKU', 'course_run_key': COURSE_RUN_KEY, 'catalog': 'abc'}, 'enterprise'),
        ([('sku', 'SKU1'), ('sku', 'SKU2'), ('bundle', 'program-uuid')], 'program_disabled'),
        ([('sku', 'SKU1'), ('sku', 'SKU2')], 'multiple_products'),
        ([('course_run_key', COURSE_RUN_KEY), ('course_run_key', 'course-v1:edX+Other+1T2026')], 'multiple_products'),
        ({'course_run_key': 'not-a-course-key'}, 'invalid_key'),
        ({'sku': 'UNKNOWNSKU'}, 'unknown_product'),
        ({'sku': 'BULKSKU', 'course_run_key': COURSE_RUN_KEY}, 'bulk'),
        ({'sku': 'BULKSKU'}, 'bulk'),
        ({'course_run_key': 'course-v1:edX+NoModes+1T2026'}, 'unknown_product'),
    )
    @ddt.unpack
    def test_unsupported(self, query, reason):
        """Purchases the a la carte checkout doesn't take go to the ecommerce checkout while no fallback page is set."""
        assert self.route(query) == self.legacy_route(query, reason)

    def test_sku_of_another_course_run(self):
        """A link with the SKU of one course run and the key of another doesn't say which one the learner wants."""
        create_course_modes('course-v1:edX+Other+1T2026', {'mode_slug': CourseMode.VERIFIED, 'sku': 'OTHERSKU'})
        query = {'sku': 'OTHERSKU', 'course_run_key': COURSE_RUN_KEY}

        assert self.route(query) == self.legacy_route(query, 'sku_mismatch')

    def test_sku_lookup_is_cached(self):
        """The SKU columns have no index, so the course run of a SKU is looked up once."""
        course_key = CourseKey.from_string(COURSE_RUN_KEY)
        get_course_key_for_sku = alacarte_checkout._get_course_key_for_sku  # pylint: disable=protected-access

        assert get_course_key_for_sku('VERIFIEDSKU') == course_key
        assert get_course_key_for_sku('UNKNOWNSKU') is None
        with self.assertNumQueries(0):
            assert get_course_key_for_sku('VERIFIEDSKU') == course_key
            assert get_course_key_for_sku('UNKNOWNSKU') is None

    def test_professional(self):
        course_run_key = 'course-v1:edX+Professional+1T2026'
        create_course_modes(course_run_key, {'mode_slug': CourseMode.PROFESSIONAL, 'sku': 'PROSKU'})
        query = {'sku': 'PROSKU', 'course_run_key': course_run_key}

        assert self.route(query) == self.legacy_route(query, 'mode_not_supported')

    def test_expired_verified_mode(self):
        course_run_key = 'course-v1:edX+Expired+1T2026'
        create_course_modes(
            course_run_key,
            {'mode_slug': CourseMode.VERIFIED, 'sku': 'EXPIREDSKU', 'expiration_datetime': now() - timedelta(days=1)},
        )
        query = {'sku': 'EXPIREDSKU', 'course_run_key': course_run_key}

        assert self.route(query) == self.legacy_route(query, 'unknown_product')

    def test_no_course_uuid(self):
        self.mock_fetch_course_uuid.return_value = None
        query = {'course_run_key': COURSE_RUN_KEY}

        assert self.route(query) == self.legacy_route(query, 'no_course_uuid')

    @override_settings(ALACARTE_CHECKOUT_FALLBACK_URL='https://help.example.com/buy')
    def test_fallback_url(self):
        assert self.route({'sku': 'UNKNOWNSKU'}) == Route('https://help.example.com/buy', 'unknown_product')

    @override_settings(ALACARTE_CHECKOUT_PROGRAMS_ENABLED=True)
    def test_program(self):
        route = self.route([('sku', 'SKU1'), ('sku', 'SKU2'), ('bundle', 'program-uuid'), ('code', 'CODE')])

        assert route == Route(alacarte_url(program_uuid='program-uuid', discount_code='CODE'), 'program')

    @override_settings(ALACARTE_CHECKOUT_URL='https://checkout.example.com/buy')
    def test_checkout_url_setting(self):
        assert self.route({'course_run_key': COURSE_RUN_KEY}).url.startswith('https://checkout.example.com/buy?')

    @override_settings(LEARNER_HOME_SITE_URL=None)
    def test_no_checkout_url(self):
        query = {'course_run_key': COURSE_RUN_KEY}

        assert self.route(query) == self.legacy_route(query, 'no_checkout_url')

    def test_flag_off(self):
        """Links given out while the flag was on go back to the ecommerce checkout."""
        query = {'sku': 'VERIFIEDSKU', 'course_run_key': COURSE_RUN_KEY}

        with override_waffle_flag(REDIRECT_TO_ALACARTE_CHECKOUT, active=False):
            assert self.route(query) == self.legacy_route(query, 'flag_off')


@skip_unless_lms
class CourseUuidTests(TestCase):
    """Tests of the course UUID lookup."""

    def setUp(self):
        super().setUp()
        TieredCache.dangerous_clear_all_tiers()
        self.course_key = CourseKey.from_string(COURSE_RUN_KEY)

    @patch.object(alacarte_checkout, '_fetch_course_uuid', return_value=COURSE_UUID)
    def test_found_is_cached(self, mock_fetch):
        assert alacarte_checkout.get_cached_course_uuid(self.course_key) == COURSE_UUID
        assert alacarte_checkout.get_cached_course_uuid(self.course_key) == COURSE_UUID
        mock_fetch.assert_called_once_with(self.course_key)

    @patch.object(alacarte_checkout, '_fetch_course_uuid', return_value=None)
    def test_not_found_is_cached(self, mock_fetch):
        assert alacarte_checkout.get_cached_course_uuid(self.course_key) is None
        assert alacarte_checkout.get_cached_course_uuid(self.course_key) is None
        mock_fetch.assert_called_once_with(self.course_key)

    @patch.object(alacarte_checkout, '_fetch_course_uuid', side_effect=Exception('Discovery is down'))
    def test_error_is_cached_briefly(self, mock_fetch):
        """During a Discovery outage, clicks go to the fallback without waiting for the timeout each time."""
        with patch.object(TieredCache, 'set_all_tiers', wraps=TieredCache.set_all_tiers) as mock_set_all_tiers:
            assert alacarte_checkout.get_cached_course_uuid(self.course_key) is None
            assert alacarte_checkout.get_cached_course_uuid(self.course_key) is None

        mock_fetch.assert_called_once_with(self.course_key)
        mock_set_all_tiers.assert_called_once_with(ANY, '', alacarte_checkout.COURSE_UUID_ERROR_CACHE_TIMEOUT)

    def _mock_discovery(self, status_code, data=None):
        """Patch the catalog client to answer the course run request."""
        response = Mock(status_code=status_code)
        response.json.return_value = data or {}
        if status_code >= 400:
            response.raise_for_status.side_effect = Exception(status_code)
        client = Mock()
        client.get.return_value = response
        patches = [
            patch.object(
                alacarte_checkout, 'check_catalog_integration_and_get_user', return_value=(UserFactory(), Mock())
            ),
            patch.object(
                alacarte_checkout, 'get_catalog_api_base_url', return_value='https://discovery.example.com/api/v1'
            ),
            patch.object(alacarte_checkout, 'get_catalog_api_client', return_value=client),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return client

    def test_fetch_course_uuid(self):
        """One request to the Discovery course run, which carries the UUID of its course."""
        client = self._mock_discovery(200, {'key': COURSE_RUN_KEY, 'course_uuid': COURSE_UUID})

        assert alacarte_checkout.get_cached_course_uuid(self.course_key) == COURSE_UUID
        client.get.assert_called_once_with(
            f'https://discovery.example.com/api/v1/course_runs/{COURSE_RUN_KEY}/',
            timeout=alacarte_checkout.DISCOVERY_TIMEOUT_SECONDS,
        )

    def test_fetch_course_uuid_not_found(self):
        self._mock_discovery(404)

        assert alacarte_checkout.get_cached_course_uuid(self.course_key) is None

    def test_fetch_course_uuid_error(self):
        self._mock_discovery(500)

        assert alacarte_checkout.get_cached_course_uuid(self.course_key) is None

    @patch.object(alacarte_checkout, 'check_catalog_integration_and_get_user', return_value=(None, Mock()))
    def test_catalog_integration_unavailable(self, _mock):
        assert alacarte_checkout.get_cached_course_uuid(self.course_key) is None
