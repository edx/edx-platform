"""
Route the LMS purchase links to the a la carte checkout.

While the commerce.redirect_to_alacarte_checkout flag is on, every purchase link the LMS builds points at the checkout
redirect view (see EcommerceService.payment_page_url and get_checkout_page_url). When the learner clicks, the view
calls route_checkout, which turns the parameters of the link into the a la carte checkout URL. That checkout needs
the Discovery course UUID, which the LMS doesn't store, so it is looked up here, once per click and cached.

Purchases the a la carte checkout doesn't take (enterprise, bulk, several products, modes other than
ALACARTE_CHECKOUT_COURSE_MODES, programs while ALACARTE_CHECKOUT_PROGRAMS_ENABLED is off, courses Discovery doesn't
know) go to ALACARTE_CHECKOUT_FALLBACK_URL, or to the ecommerce checkout while it isn't set.
"""

import logging
from collections import namedtuple
from urllib.parse import urlencode, urljoin

from django.conf import settings
from django.db.models import Q
from django.utils.timezone import now
from edx_django_utils.cache import TieredCache, get_cache_key
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from openedx.core.djangoapps.catalog.utils import (
    check_catalog_integration_and_get_user,
    get_catalog_api_base_url,
    get_catalog_api_client
)
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

from .toggles import should_redirect_to_alacarte_checkout
from .utils import EcommerceService

log = logging.getLogger(__name__)

# The destination of a checkout link, and the reason it was chosen (sent to monitoring)
Route = namedtuple('Route', ['url', 'reason'])

ALACARTE_CHECKOUT_PATH = '/aca-checkout'
DISCOVERY_TIMEOUT_SECONDS = 5
COURSE_UUID_CACHE_TIMEOUT = 24 * 60 * 60
COURSE_UUID_NOT_FOUND_CACHE_TIMEOUT = 15 * 60
MAX_DISCOUNT_CODE_LENGTH = 64


def route_checkout(query):
    """
    Return the Route of a checkout link, given its query parameters (a QueryDict).
    """
    if not should_redirect_to_alacarte_checkout():
        # links given out while the flag was on go back to the ecommerce checkout
        return Route(legacy_destination(query), 'flag_off')

    route = _route_to_checkout(query)
    if route.url:
        return route
    return Route(unsupported_url(query), route.reason)


def _route_to_checkout(query):
    """
    Return the Route to the a la carte checkout, or a Route without a URL when that checkout doesn't take the purchase.
    """
    checkout_url = get_checkout_base_url()
    if not checkout_url:
        log.error('The a la carte checkout URL is not configured (ALACARTE_CHECKOUT_URL or LEARNER_HOME_SITE_URL)')
        return Route(None, 'no_checkout_url')

    if query.get('catalog'):
        return Route(None, 'enterprise')

    discount = _get_discount_params(query)

    program_uuid = query.get('bundle')
    if program_uuid:
        if not settings.ALACARTE_CHECKOUT_PROGRAMS_ENABLED:
            return Route(None, 'program_disabled')
        return Route(_build_url(checkout_url, {'program_uuid': program_uuid, **discount}, query), 'program')

    skus = _distinct(query.getlist('sku'))
    run_keys = _distinct(normalize_run_key(run_key) for run_key in query.getlist('course_run_key'))
    if len(skus) > 1 or len(run_keys) > 1:
        return Route(None, 'multiple_products')
    sku = skus[0] if skus else None

    if run_keys:
        try:
            course_key = CourseKey.from_string(run_keys[0])
        except InvalidKeyError:
            return Route(None, 'invalid_key')
    else:
        course_key = _get_course_key_for_sku(sku)
        if course_key is None:
            return Route(None, 'unknown_product')

    reason = _check_course_modes(course_key, sku)
    if reason:
        return Route(None, reason)

    course_uuid = get_cached_course_uuid(course_key)
    if not course_uuid:
        return Route(None, 'no_course_uuid')

    params = {'course_uuid': course_uuid, 'course_run_key': str(course_key), **discount}
    return Route(_build_url(checkout_url, params, query), 'course')


def normalize_run_key(run_key):
    """
    Return the course run key with spaces turned back into "+".

    Course keys never contain spaces, and a link with a literal "+" in the key decodes it to a space.
    """
    return run_key.strip().replace(' ', '+')


def _distinct(values):
    """
    Return the non-empty values, without repeats, in order.

    A plugin that overrides the checkout link (OVERRIDE_GET_CHECKOUT_PAGE_URL) can append the course run key again, and
    one that overrides the offer (OVERRIDE_GENERATE_OFFER_DATA) the discount code, so a repeated value is one product.
    """
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _get_course_key_for_sku(sku):
    """
    Return the course run key of the course mode with the SKU, or None.
    """
    if not sku:
        return None
    course_mode = CourseMode.objects.filter(Q(sku=sku) | Q(bulk_sku=sku)).first()
    return course_mode.course_id if course_mode else None


def _check_course_modes(course_key, sku):
    """
    Return the reason the a la carte checkout can't sell the course run, or None if it can.

    The SKU only tells which mode the link is for. The a la carte checkout doesn't need it, so a missing or unknown SKU
    is fine for a course run with a current mode of ALACARTE_CHECKOUT_COURSE_MODES.
    """
    modes = CourseMode.modes_for_course(course_id=course_key, include_expired=True, only_selectable=False)
    supported_modes = settings.ALACARTE_CHECKOUT_COURSE_MODES

    if sku and any(mode.bulk_sku == sku for mode in modes):
        return 'bulk'
    if sku and any(mode.sku == sku and mode.slug not in supported_modes for mode in modes):
        return 'mode_not_supported'

    current_time = now()
    if not any(
        mode.slug in supported_modes and (mode.expiration_datetime is None or mode.expiration_datetime >= current_time)
        for mode in modes
    ):
        return 'unknown_product'
    return None


def get_checkout_base_url():
    """
    Return the URL of the a la carte checkout page, or None.
    """
    if settings.ALACARTE_CHECKOUT_URL:
        return settings.ALACARTE_CHECKOUT_URL

    learner_home_site_url = configuration_helpers.get_value(
        'LEARNER_HOME_SITE_URL',
        getattr(settings, 'LEARNER_HOME_SITE_URL', None),
    )
    if not learner_home_site_url:
        return None
    return f"{learner_home_site_url.rstrip('/')}{ALACARTE_CHECKOUT_PATH}"


def get_cached_course_uuid(course_key):
    """
    Return the Discovery UUID of the course of the course run, or None.

    A found UUID is cached for a day, and a course run Discovery doesn't know for 15 minutes. Errors aren't cached, so
    the next click tries again.
    """
    cache_key = get_cache_key(resource='alacarte_checkout.course_uuid', course_run_key=str(course_key))
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        return cached_response.value or None

    try:
        course_uuid = _fetch_course_uuid(course_key)
    except Exception:  # pylint: disable=broad-except
        log.exception('Failed to get the course UUID of course run %s from Discovery', course_key)
        return None

    if course_uuid:
        TieredCache.set_all_tiers(cache_key, course_uuid, COURSE_UUID_CACHE_TIMEOUT)
    else:
        TieredCache.set_all_tiers(cache_key, '', COURSE_UUID_NOT_FOUND_CACHE_TIMEOUT)
    return course_uuid


def _fetch_course_uuid(course_key):
    """
    Return the course UUID from the Discovery course run, or None if Discovery doesn't know the course run.

    The course run already carries the UUID of its course, so this is one call (get_course_uuid_for_course makes two).
    """
    user, _catalog_integration = check_catalog_integration_and_get_user(error_message_field='Course UUID')
    if not user:
        raise ValueError('The catalog integration is not available')

    url = urljoin(f'{get_catalog_api_base_url()}/', f'course_runs/{course_key}/')
    response = get_catalog_api_client(user).get(url, timeout=DISCOVERY_TIMEOUT_SECONDS)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json().get('course_uuid') or None


def _get_discount_params(query):
    """
    Return the discount code of the link as the a la carte checkout parameter, if discount codes are forwarded.
    """
    if not settings.ALACARTE_CHECKOUT_FORWARD_DISCOUNT_CODES:
        return {}
    codes = _distinct(query.getlist('code'))
    if not codes:
        return {}
    return {'discount_code': codes[0][:MAX_DISCOUNT_CODE_LENGTH]}


def _build_url(checkout_url, params, query):
    """
    Return the a la carte checkout URL with the parameters, and the utm_* parameters of the link.
    """
    params = {
        **params,
        **{name: value for name, value in query.items() if name.startswith('utm_')},
    }
    return f'{checkout_url}?{urlencode(params)}'


def legacy_destination(query):
    """
    Return the ecommerce checkout link of the query, as the LMS built it before the a la carte checkout.

    get_absolute_ecommerce_url isn't changed by the a la carte checkout flag, so this never points back at the redirect
    view. A plugin that overrides it (OVERRIDE_GET_ABSOLUTE_ECOMMERCE_URL) can send this link to its own checkout.
    """
    ecommerce_service = EcommerceService()
    checkout_page_url = ecommerce_service.get_absolute_ecommerce_url(ecommerce_service.config.basket_checkout_page)
    if not query:
        return checkout_page_url
    return f'{checkout_page_url}?{query.urlencode()}'


def unsupported_url(query):
    """
    Return where a purchase the a la carte checkout doesn't take goes.
    """
    return settings.ALACARTE_CHECKOUT_FALLBACK_URL or legacy_destination(query)
