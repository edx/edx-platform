"""
Views of the commerce app.
"""

import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit
from edx_django_utils.monitoring import set_custom_attribute

from .alacarte_checkout import Route, route_checkout, unsupported_url

log = logging.getLogger(__name__)


def _get_rate_limit(group, request):  # pylint: disable=unused-argument
    return settings.ALACARTE_CHECKOUT_RATE_LIMIT


def _describe_link(query):
    """
    Return the parameters of a checkout link that are safe to log: the product, not the discount code or the rest.
    """
    return {name: query.getlist(name) for name in ('sku', 'course_run_key', 'bundle') if name in query}


@method_decorator(
    ratelimit(
        key='openedx.core.djangoapps.util.ratelimit.real_ip',
        rate=_get_rate_limit,
        method=['GET', 'HEAD'],
        block=True,
    ),
    name='dispatch',
)
@method_decorator(login_required, name='dispatch')
class CheckoutRedirectView(View):
    """
    Send a purchase link of the LMS to the a la carte checkout.

    While the commerce.redirect_to_alacarte_checkout flag is on, every purchase link the LMS builds points here, with
    the parameters it used to send to the ecommerce checkout (sku, course_run_key, bundle, catalog, code). A learner
    who isn't signed in signs in first and comes back here.

    The view always redirects: whatever goes wrong, the learner goes to the fallback page, or to the ecommerce checkout
    while no fallback page is configured. The one exception is a client over the per-IP rate limit
    (ALACARTE_CHECKOUT_RATE_LIMIT), which gets the LMS rate limit page (429).
    """

    http_method_names = ['get', 'head']

    def get(self, request):
        """
        Redirect the learner to the checkout that takes this purchase.
        """
        try:
            route = route_checkout(request.GET)
        except Exception:  # pylint: disable=broad-except
            log.exception('Failed to route the checkout link %s', _describe_link(request.GET))
            route = Route(self._get_error_url(request), 'error')

        set_custom_attribute('alacarte_checkout_route', route.reason)
        return HttpResponseRedirect(route.url)

    @staticmethod
    def _get_error_url(request):
        """
        Return where the learner goes when routing failed.
        """
        try:
            return unsupported_url(request.GET)
        except Exception:  # pylint: disable=broad-except
            log.exception('Failed to get the fallback of the checkout link %s', _describe_link(request.GET))
            return reverse('dashboard')
