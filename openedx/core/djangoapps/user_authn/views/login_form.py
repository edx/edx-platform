""" Login related views """


import json
import logging
import urllib

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from common.djangoapps import third_party_auth
from common.djangoapps.edxmako.shortcuts import render_to_response
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api import accounts
from openedx.core.djangoapps.user_api.accounts.utils import (
    is_secondary_email_feature_enabled,
)
from openedx.core.djangoapps.user_api.helpers import FormDescription
from openedx.core.djangoapps.user_authn.config.waffle import (
    ENABLE_ENTERPRISE_REDIRECT_TO_AUTHN,
)
from openedx.core.djangoapps.user_authn.cookies import set_logged_in_cookies
from openedx.core.djangoapps.user_authn.toggles import (
    is_require_third_party_auth_enabled,
    should_redirect_to_authn_microfrontend,
)
from openedx.core.djangoapps.user_authn.views.password_reset import (
    get_password_reset_form,
)
from openedx.core.djangoapps.user_authn.views.registration_form import (
    RegistrationFormFactory,
)
from openedx.core.djangoapps.user_authn.views.utils import third_party_auth_context
from openedx.features.enterprise_support.api import (
    enterprise_customer_for_request,
    enterprise_enabled,
)
from openedx.features.enterprise_support.utils import (
    get_enterprise_slug_login_url,
    handle_enterprise_cookies_for_logistration,
    update_logistration_context_for_enterprise,
)
from common.djangoapps.student.helpers import get_next_url_for_login_page
from common.djangoapps.third_party_auth import pipeline
from common.djangoapps.third_party_auth.decorators import xframe_allow_whitelisted
from common.djangoapps.util.password_policy_validators import (
    DEFAULT_MAX_PASSWORD_LENGTH,
)

log = logging.getLogger(__name__)


def _apply_third_party_auth_overrides(request, form_desc):
    """
    Modify the login form if the user has authenticated with a third-party provider.

    If a user has successfully authenticated with a third-party provider,
    and an email is associated with it then we fill in the email field with
    readonly property.
    """
    if third_party_auth.is_enabled():
        running_pipeline = third_party_auth.pipeline.get(request)
        if running_pipeline:
            current_provider = third_party_auth.provider.Registry.get_from_pipeline(
                running_pipeline
            )
            if current_provider and enterprise_customer_for_request(request):
                pipeline_kwargs = running_pipeline.get("kwargs")

                # Details about the user sent back from the provider.
                details = pipeline_kwargs.get("details")
                email = details.get("email", "")

                # Override the email field.
                form_desc.override_field_properties(
                    "email",
                    default=email,
                    restrictions={"readonly": "readonly"}
                    if email
                    else {
                        "min_length": accounts.EMAIL_MIN_LENGTH,
                        "max_length": accounts.EMAIL_MAX_LENGTH,
                    },
                )


def get_login_session_form(request):
    """Return a description of the login form."""
    form_desc = FormDescription(
        "post", reverse("user_api_login_session", kwargs={"api_version": "v1"})
    )
    _apply_third_party_auth_overrides(request, form_desc)

    # Translators: This label appears above a field on the login form
    # meant to hold the user's email address.
    email_label = _("Email")

    # Translators: These instructions appear on the login form, immediately
    # below a field meant to hold the user's email address.
    email_instructions = _(
        "The email address you used to register with {platform_name}"
    ).format(
        platform_name=configuration_helpers.get_value(
            "PLATFORM_NAME", settings.PLATFORM_NAME
        )
    )

    form_desc.add_field(
        "email",
        field_type="email",
        label=email_label,
        instructions=email_instructions,
        restrictions={
            "min_length": accounts.EMAIL_MIN_LENGTH,
            "max_length": accounts.EMAIL_MAX_LENGTH,
        },
    )

    # Translators: This label appears above a field on the login form
    # meant to hold the user's password.
    password_label = _("Password")

    form_desc.add_field(
        "password",
        label=password_label,
        field_type="password",
        restrictions={"max_length": DEFAULT_MAX_PASSWORD_LENGTH},
    )

    return form_desc


def _handle_tpa_hint(request, redirect_to, initial_mode):
    """
    Handle TPA hint logic and return:
    (third_party_auth_hint, updated_initial_mode, optional_redirect_response).

    - Preserves existing behavior for hinted login dialog & skip_hinted_login_dialog.
    - Does NOT decide about MFE redirect; that is handled separately.
    """
    third_party_auth_hint = None

    # Early return if no query string in redirect_to
    if "?" not in redirect_to:
        return third_party_auth_hint, initial_mode, None

    try:
        next_args = urllib.parse.parse_qs(
            urllib.parse.urlparse(redirect_to).query
        )

        # Early return if no tpa_hint in query params
        if "tpa_hint" not in next_args:
            return third_party_auth_hint, initial_mode, None

        provider_id = next_args["tpa_hint"][0]
        tpa_hint_provider = third_party_auth.provider.Registry.get(
            provider_id=provider_id
        )

        # Early return if provider not found
        if not tpa_hint_provider:
            return third_party_auth_hint, initial_mode, None

        # Handle skip_hinted_login_dialog
        if tpa_hint_provider.skip_hinted_login_dialog:
            auth_entry = (
                pipeline.AUTH_ENTRY_REGISTER
                if initial_mode == "register"
                else pipeline.AUTH_ENTRY_LOGIN
            )
            redirect_response = redirect(
                pipeline.get_login_url(
                    provider_id,
                    auth_entry,
                    redirect_url=redirect_to,
                )
            )
            return None, initial_mode, redirect_response

        # Set hint and mode for hinted login
        third_party_auth_hint = provider_id
        initial_mode = "hinted_login"

    except (KeyError, ValueError, IndexError) as ex:
        log.exception("Unknown tpa_hint provider: %s", ex)

    return third_party_auth_hint, initial_mode, None


def _has_tpa_hint(request, redirect_to):
    """
    Return True if any TPA hint is present either in request.GET or nested inside
    the redirect_to URL (?next=...), used to block MFE redirect.
    """
    if "tpa_hint" in request.GET:
        return True

    if "?" in redirect_to:
        next_args = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_to).query)
        if "tpa_hint" in next_args:
            return True

    return False


def _maybe_redirect_to_authn_mfe(request, initial_mode, redirect_to):
    """
    Decide whether to redirect to the AuthN MFE.

    Returns:
        HttpResponse redirect, or None if we should render the legacy page.
    """
    # External providers (SAML / TPA hint) must NEVER redirect to MFE.
    # Check for any running pipeline first (this catches all third-party auth)
    running_pipeline = pipeline.get(request)
    # If there's ANY running pipeline, treat it as an external provider
    # This handles SAML, OAuth, and any other third-party auth flows
    has_running_pipeline = running_pipeline is not None
    # Also explicitly check for SAML if pipeline exists
    saml_provider = False
    if running_pipeline:
        backend_name = running_pipeline.get("backend")
        kwargs = running_pipeline.get("kwargs", {})
        # is_saml_provider returns a tuple (bool, provider_name)
        saml_provider, __ = third_party_auth.utils.is_saml_provider(
            backend=backend_name,
            kwargs=kwargs,
        )
    # Check for TPA hint in request or redirect URL
    has_tpa_hint = _has_tpa_hint(request, redirect_to)
    # Treat ANY of these as external provider (hard stop for MFE redirect)
    has_external_provider = bool(has_running_pipeline or saml_provider or has_tpa_hint)

    enterprise_customer = enterprise_customer_for_request(request)
    if enterprise_customer:
        # Enterprise / B2B: gated by the Enterprise waffle flag
        is_segment_eligible = ENABLE_ENTERPRISE_REDIRECT_TO_AUTHN.is_enabled()
    else:
        # B2C: eligible by default when global AuthN MFE is on
        is_segment_eligible = True

    if not (
        should_redirect_to_authn_microfrontend()
        and is_segment_eligible
        and not has_external_provider
    ):
        return None

    # Handle authenticated user with a specific redirect target (finish_auth, etc.)
    if request.user.is_authenticated:
        redirect_to_target = get_next_url_for_login_page(request)
        if redirect_to_target:
            return redirect(redirect_to_target)

    query_params = request.GET.urlencode()
    url_path = "/{}{}".format(
        initial_mode,
        "?" + query_params if query_params else "",
    )
    return redirect(settings.AUTHN_MICROFRONTEND_URL + url_path)


def _get_account_messages(request):
    """
    Return (account_activation_messages, account_recovery_messages) from Django messages.
    """
    account_activation_messages = [
        {
            "message": message.message,
            "tags": message.tags,
        }
        for message in messages.get_messages(request)
        if "account-activation" in message.tags
    ]

    account_recovery_messages = [
        {
            "message": message.message,
            "tags": message.tags,
        }
        for message in messages.get_messages(request)
        if "account-recovery" in message.tags
    ]

    return account_activation_messages, account_recovery_messages


def _build_logistration_context(
    request,
    redirect_to,
    initial_mode,
    third_party_auth_hint,
    form_descriptions,
    account_activation_messages,
    account_recovery_messages,
    enterprise_customer,
):
    """
    Build the context dict for the legacy combined login/registration page.
    """
    return {
        "data": {
            "login_redirect_url": redirect_to,
            "initial_mode": initial_mode,
            "third_party_auth": third_party_auth_context(
                request, redirect_to, third_party_auth_hint
            ),
            "third_party_auth_hint": third_party_auth_hint or "",
            "platform_name": configuration_helpers.get_value(
                "PLATFORM_NAME", settings.PLATFORM_NAME
            ),
            "support_link": configuration_helpers.get_value(
                "SUPPORT_SITE_LINK", settings.SUPPORT_SITE_LINK
            ),
            "password_reset_support_link": configuration_helpers.get_value(
                "PASSWORD_RESET_SUPPORT_LINK", settings.PASSWORD_RESET_SUPPORT_LINK
            )
            or settings.SUPPORT_SITE_LINK,
            "account_activation_messages": account_activation_messages,
            "account_recovery_messages": account_recovery_messages,
            # Include form descriptions retrieved from the user API.
            # We include them in the initial page load to avoid an extra round-trip.
            "login_form_desc": json.loads(form_descriptions["login"]),
            "registration_form_desc": json.loads(form_descriptions["registration"]),
            "password_reset_form_desc": json.loads(form_descriptions["password_reset"]),
            "account_creation_allowed": configuration_helpers.get_value(
                "ALLOW_PUBLIC_ACCOUNT_CREATION",
                settings.FEATURES.get("ALLOW_PUBLIC_ACCOUNT_CREATION", True),
            ),
            "register_links_allowed": settings.FEATURES.get(
                "SHOW_REGISTRATION_LINKS", True
            ),
            "is_account_recovery_feature_enabled": is_secondary_email_feature_enabled(),
            "enterprise_slug_login_url": get_enterprise_slug_login_url(),
            "is_enterprise_enable": enterprise_enabled(),
            "is_require_third_party_auth_enabled": is_require_third_party_auth_enabled(),
            "enable_coppa_compliance": settings.ENABLE_COPPA_COMPLIANCE,
            "edx_user_info_cookie_name": settings.EDXMKTG_USER_INFO_COOKIE_NAME,
        },
        # Added to the query string of the "Sign In" button in header
        "login_redirect_url": redirect_to,
        "responsive": True,
        "allow_iframing": True,
        "disable_courseware_js": True,
        "combined_login_and_register": True,
        "disable_footer": not configuration_helpers.get_value(
            "ENABLE_COMBINED_LOGIN_REGISTRATION_FOOTER",
            settings.FEATURES["ENABLE_COMBINED_LOGIN_REGISTRATION_FOOTER"],
        ),
    }


@require_http_methods(["GET"])
@ratelimit(
    key="openedx.core.djangoapps.util.ratelimit.real_ip",
    rate=settings.LOGIN_AND_REGISTER_FORM_RATELIMIT,
    method="GET",
    block=True,
)
@ensure_csrf_cookie
@xframe_allow_whitelisted
def login_and_registration_form(request, initial_mode="login"):
    """
    Render the combined login/registration form, defaulting to login.

    This relies on the JS to asynchronously load the actual form from
    the user_api.
    """
    # Determine the URL to redirect to following login/registration/third_party_auth
    redirect_to = get_next_url_for_login_page(request)

    # If we're already logged in, redirect to the dashboard (or next target).
    if request.user.is_authenticated:
        response = redirect(redirect_to)
        response = set_logged_in_cookies(request, response, request.user)
        return response

    # Retrieve the form descriptions from the user API
    form_descriptions = _get_form_descriptions(request)

    # Handle hinted login behavior (including skip_hinted_login_dialog).
    third_party_auth_hint, initial_mode, redirect_response = _handle_tpa_hint(
        request, redirect_to, initial_mode
    )
    if redirect_response is not None:
        return redirect_response

    # Possibly redirect to the AuthN MFE, depending on global flag, segment, and providers.
    redirect_response = _maybe_redirect_to_authn_mfe(
        request, initial_mode, redirect_to
    )
    if redirect_response is not None:
        return redirect_response

    # Account activation / recovery messages
    (
        account_activation_messages,
        account_recovery_messages,
    ) = _get_account_messages(request)

    # Enterprise context (used for sidebar / branding)
    enterprise_customer = enterprise_customer_for_request(request)

    # Otherwise, render the combined legacy login/registration page
    context = _build_logistration_context(
        request,
        redirect_to,
        initial_mode,
        third_party_auth_hint,
        form_descriptions,
        account_activation_messages,
        account_recovery_messages,
        enterprise_customer,
    )

    update_logistration_context_for_enterprise(request, context, enterprise_customer)

    response = render_to_response("student_account/login_and_register.html", context)
    handle_enterprise_cookies_for_logistration(request, response, context)

    return response


def _get_form_descriptions(request):
    """
    Retrieve form descriptions from the user API.

    Returns:
        dict: Keys are 'login', 'registration', and 'password_reset';
              values are the JSON-serialized form descriptions.
    """
    return {
        "password_reset": get_password_reset_form().to_json(),
        "login": get_login_session_form(request).to_json(),
        "registration": RegistrationFormFactory()
        .get_registration_form(request)
        .to_json(),
    }
