"""Tests of the checkout redirect view."""

from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import TEST_PASSWORD, UserFactory
from lms.djangoapps.commerce.alacarte_checkout import Route
from openedx.core.djangolib.testing.utils import skip_unless_lms

CHECKOUT_URL = 'https://learner-home.example.com/aca-checkout?course_uuid=abc&course_run_key=def'


@skip_unless_lms
class CheckoutRedirectViewTests(TestCase):
    """Tests of the checkout redirect view."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.url = reverse('commerce_checkout_redirect')

    @patch('lms.djangoapps.commerce.views.set_custom_attribute')
    @patch('lms.djangoapps.commerce.views.route_checkout', return_value=Route(CHECKOUT_URL, 'course'))
    def test_redirect(self, mock_route_checkout, mock_set_custom_attribute):
        response = self.client.get(self.url, {'sku': 'SKU1', 'course_run_key': 'course-v1:edX+DemoX+1T2026'})

        assert response.status_code == 302
        assert response['Location'] == CHECKOUT_URL
        assert mock_route_checkout.call_args[0][0].dict() == {
            'sku': 'SKU1', 'course_run_key': 'course-v1:edX+DemoX+1T2026',
        }
        mock_set_custom_attribute.assert_called_once_with('alacarte_checkout_route', 'course')

    @patch('lms.djangoapps.commerce.views.route_checkout', return_value=Route(CHECKOUT_URL, 'course'))
    def test_head(self, _mock_route_checkout):
        response = self.client.head(self.url, {'sku': 'SKU1'})

        assert response.status_code == 302
        assert response['Location'] == CHECKOUT_URL

    def test_post_not_allowed(self):
        assert self.client.post(self.url).status_code == 405

    @patch('lms.djangoapps.commerce.views.route_checkout')
    def test_anonymous(self, mock_route_checkout):
        """A learner who isn't signed in signs in first, and comes back to the same link."""
        self.client.logout()

        response = self.client.get(self.url, {'sku': 'SKU1'})

        assert response.status_code == 302
        assert response['Location'].startswith(settings.LOGIN_URL)
        assert 'next=' in response['Location']
        assert 'sku%3DSKU1' in response['Location']
        mock_route_checkout.assert_not_called()

    @override_settings(
        ALACARTE_CHECKOUT_RATE_LIMIT='2/m',
        # the rate limit counts in the default cache, which is a DummyCache in tests
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'checkout_redirect',
            }
        },
    )
    @patch('lms.djangoapps.commerce.views.route_checkout', return_value=Route(CHECKOUT_URL, 'course'))
    def test_rate_limit(self, _mock_route_checkout):
        """A client over the per-IP rate limit gets the rate limit page."""
        cache.clear()

        for _ in range(2):
            assert self.client.get(self.url, {'sku': 'SKU1'}).status_code == 302
        assert self.client.get(self.url, {'sku': 'SKU1'}).status_code == 429

    @patch('lms.djangoapps.commerce.views.log')
    @patch('lms.djangoapps.commerce.views.set_custom_attribute')
    @patch('lms.djangoapps.commerce.views.unsupported_url', return_value='https://help.example.com/buy')
    @patch('lms.djangoapps.commerce.views.route_checkout', side_effect=Exception('unexpected'))
    def test_error(self, _mock_route_checkout, _mock_unsupported_url, mock_set_custom_attribute, mock_log):
        """The view shows no error page: an unexpected error sends the learner to the fallback."""
        response = self.client.get(
            self.url, {'sku': 'SKU1', 'course_run_key': 'course-v1:edX+DemoX+1T2026', 'code': 'SECRETCODE'}
        )

        assert response.status_code == 302
        assert response['Location'] == 'https://help.example.com/buy'
        mock_set_custom_attribute.assert_called_once_with('alacarte_checkout_route', 'error')
        # the log names the product, but not the discount code
        mock_log.exception.assert_called_once_with(
            'Failed to route the checkout link %s', {'sku': ['SKU1'], 'course_run_key': ['course-v1:edX+DemoX+1T2026']}
        )

    @patch('lms.djangoapps.commerce.views.unsupported_url', side_effect=Exception('unexpected'))
    @patch('lms.djangoapps.commerce.views.route_checkout', side_effect=Exception('unexpected'))
    def test_error_without_fallback(self, _mock_route_checkout, _mock_unsupported_url):
        response = self.client.get(self.url, {'sku': 'SKU1'})

        assert response.status_code == 302
        assert response['Location'] == reverse('dashboard')
