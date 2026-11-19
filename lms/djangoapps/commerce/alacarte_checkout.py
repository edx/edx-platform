"""
Route the LMS purchase links to the a la carte checkout.

While the commerce.redirect_to_alacarte_checkout flag is on, every purchase link the LMS builds points at the checkout
redirect view (see EcommerceService.payment_page_url and get_checkout_page_url). When the learner clicks, the view
calls route_checkout, which turns the parameters of the link into the a la carte checkout URL. That checkout needs
the Discovery course UUID, which the LMS doesn't store, so it is looked up here, once per click and cached.

Purchases the a la carte checkout doesn't take (enterprise, bulk, several products, a SKU of another course run, modes
other than ALACARTE_CHECKOUT_COURSE_MODES, programs while ALACARTE_CHECKOUT_PROGRAMS_ENABLED is off, courses Discovery
doesn't know) go to ALACARTE_CHECKOUT_FALLBACK_URL, or to the ecommerce checkout while it isn't set.
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

from .toggles import should_redirect_to_alacarte_checkout
from .utils import EcommerceService

log = logging.getLogger(__name__)

# The destination of a checkout link, and the reason it was chosen (sent to monitoring)
Route = namedtuple('Route', ['url', 'reason'])

DISCOVERY_TIMEOUT_SECONDS = 5
COURSE_UUID_CACHE_TIMEOUT = 24 * 60 * 60
COURSE_UUID_NOT_FOUND_CACHE_TIMEOUT = 15 * 60
# A failed Discovery call is remembered briefly, so an outage doesn't hold every click for the whole timeout
COURSE_UUID_ERROR_CACHE_TIMEOUT = 60
SKU_COURSE_RUN_CACHE_TIMEOUT = 24 * 60 * 60
SKU_COURSE_RUN_NOT_FOUND_CACHE_TIMEOUT = 15 * 60
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
        log.error('The a la carte checkout URL is not configured (ALACARTE_CHECKOUT_URL)')
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

    The SKU columns have no index, so the answer is cached: a found course run for a day, an unknown SKU for 15 minutes.
    The lowest id wins when several modes have the SKU, as ecommerce gives each mode its own.
    """
    if not sku:
        return None

    cache_key = get_cache_key(resource='alacarte_checkout.sku_course_run', sku=sku)
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        return CourseKey.from_string(cached_response.value) if cached_response.value else None

    course_mode = CourseMode.objects.filter(Q(sku=sku) | Q(bulk_sku=sku)).order_by('id').first()
    if course_mode:
        TieredCache.set_all_tiers(cache_key, str(course_mode.course_id), SKU_COURSE_RUN_CACHE_TIMEOUT)
        return course_mode.course_id
    TieredCache.set_all_tiers(cache_key, '', SKU_COURSE_RUN_NOT_FOUND_CACHE_TIMEOUT)
    return None


def _check_course_modes(course_key, sku):
    """
    Return the reason the a la carte checkout can't sell the course run, or None if it can.

    The SKU only tells which mode the link is for. The a la carte checkout doesn't need it, so a missing or unknown SKU
    is fine for a course run with a current mode of ALACARTE_CHECKOUT_COURSE_MODES. A SKU of another course run is not:
    the link doesn't say which course run the learner wants.
    """
    modes = CourseMode.modes_for_course(course_id=course_key, include_expired=True, only_selectable=False)
    supported_modes = settings.ALACARTE_CHECKOUT_COURSE_MODES

    if sku and any(mode.bulk_sku == sku for mode in modes):
        return 'bulk'
    if sku and any(mode.sku == sku and mode.slug not in supported_modes for mode in modes):
        return 'mode_not_supported'
    if sku and not any(mode.sku == sku for mode in modes):
        sku_course_key = _get_course_key_for_sku(sku)
        if sku_course_key is not None and sku_course_key != course_key:
            return 'sku_mismatch'

    current_time = now()
    if not any(
        mode.slug in supported_modes and (mode.expiration_datetime is None or mode.expiration_datetime >= current_time)
        for mode in modes
    ):
        return 'unknown_product'
    return None


def get_checkout_base_url():
    """
    Return the URL of the a la carte checkout page (the ALACARTE_CHECKOUT_URL setting), or None when it isn't set.
    """
    return settings.ALACARTE_CHECKOUT_URL or None


def get_cached_course_uuid(course_key):
    """
    Return the Discovery UUID of the course of the course run, or None.

    A found UUID is cached for a day, a course run Discovery doesn't know for 15 minutes, and an error for a minute, so
    clicks during a Discovery outage go to the fallback without waiting for the timeout.
    """
    cache_key = get_cache_key(resource='alacarte_checkout.course_uuid', course_run_key=str(course_key))
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        return cached_response.value or None

    try:
        course_uuid = _fetch_course_uuid(course_key)
    except Exception:  # pylint: disable=broad-except
        log.exception('Failed to get the course UUID of course run %s from Discovery', course_key)
        TieredCache.set_all_tiers(cache_key, '', COURSE_UUID_ERROR_CACHE_TIMEOUT)
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

    A code longer than MAX_DISCOUNT_CODE_LENGTH is dropped, not cut: a shorter code would be another code.
    """
    if not settings.ALACARTE_CHECKOUT_FORWARD_DISCOUNT_CODES:
        return {}
    codes = _distinct(query.getlist('code'))
    if not codes:
        return {}
    if len(codes[0]) > MAX_DISCOUNT_CODE_LENGTH:
        log.warning('Dropped a discount code of %d characters from a checkout link', len(codes[0]))
        return {}
    return {'discount_code': codes[0]}


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

    get_checkout_page_url and payment_page_url can't be used: while the flag is on they return the redirect view, which
    would send the learner back here. get_ecommerce_checkout_page_url isn't changed by the flag. A plugin that
    overrides get_absolute_ecommerce_url (OVERRIDE_GET_ABSOLUTE_ECOMMERCE_URL) can send this link to its own checkout.
    """
    checkout_page_url = EcommerceService().get_ecommerce_checkout_page_url()
    if not query:
        return checkout_page_url
    return f'{checkout_page_url}?{query.urlencode()}'


def unsupported_url(query):
    """
    Return where a purchase the a la carte checkout doesn't take goes.
    """
    return settings.ALACARTE_CHECKOUT_FALLBACK_URL or legacy_destination(query)
