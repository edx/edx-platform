"""
Togglable settings for Third Party Auth
"""

from edx_toggles.toggles import WaffleFlag, SettingToggle

THIRD_PARTY_AUTH_NAMESPACE = 'thirdpartyauth'

# .. toggle_name: third_party_auth.apple_user_migration
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Enable User ID matching while apple migration is in process
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2023-02-27
# .. toggle_target_removal_date: 2023-05-01
# .. toggle_tickets: LEARNER-8790
# .. toggle_warning: None.
APPLE_USER_MIGRATION_FLAG = WaffleFlag(f'{THIRD_PARTY_AUTH_NAMESPACE}.apple_user_migration', __name__)


# .. toggle_name: ENABLE_SAML_CONFIG_SIGNAL_HANDLERS
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Controls whether SAML configuration signal handlers are active.
#    When enabled (True), signal handlers will automatically update SAMLProviderConfig
#    references when the associated SAMLConfiguration is updated.
#    When disabled (False), SAMLProviderConfigs point to outdated SAMLConfiguration.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-07-03
# .. toggle_target_removal_date: 2026-01-01
# .. toggle_warning: Disabling this toggle may result in SAMLProviderConfig instances
#    pointing to outdated SAMLConfiguration records. Use the management command
#    'saml --fix-references' to fix outdated references.
ENABLE_SAML_CONFIG_SIGNAL_HANDLERS = SettingToggle(
    "ENABLE_SAML_CONFIG_SIGNAL_HANDLERS",
    default=False,
    module_name=__name__
)


def is_apple_user_migration_enabled():
    """
    Returns a boolean if Apple users migration is in process.
    """
    return APPLE_USER_MIGRATION_FLAG.is_enabled()


# .. toggle_name: third_party_auth.tpa_next_url_on_dispatch
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, the third-party auth pipeline will forward
#    session['next'] as a ?next= query parameter when redirecting to the login or
#    registration page. This ensures the post-auth destination is preserved for new
#    users who must complete registration before being redirected.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-02-13
# .. toggle_target_removal_date: 2026-06-01
# .. toggle_warning: None.
TPA_NEXT_URL_ON_DISPATCH_FLAG = WaffleFlag(f'{THIRD_PARTY_AUTH_NAMESPACE}.tpa_next_url_on_dispatch', __name__)


def is_tpa_next_url_on_dispatch_enabled():
    """
    Returns True if the pipeline should forward session['next'] as a query parameter
    when dispatching to login/register pages.
    """
    return TPA_NEXT_URL_ON_DISPATCH_FLAG.is_enabled()


# .. toggle_name: third_party_auth.saml_provider_site_fallback
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, Registry.get_from_pipeline() will fall back to a
#    site-independent SAMLProviderConfig lookup when the site-filtered registry returns no
#    match for a running SAML pipeline. This handles cases where the SAMLProviderConfig or
#    SAMLConfiguration is associated with a different Django site than the one currently
#    serving the request, while SAML auth itself already completed (SAMLAuthBackend.get_idp()
#    has no site check). Without this flag, pipeline steps such as should_force_account_creation()
#    cannot read provider flags (e.g. send_to_registration_first), causing new users to land on
#    the login page instead of registration.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-02-19
# .. toggle_target_removal_date: 2026-06-01
# .. toggle_warning: The underlying site configuration mismatch should still be fixed in Django
#    admin (SAMLConfiguration and SAMLProviderConfig must reference the correct site). This flag
#    is a temporary workaround until that is resolved.
SAML_PROVIDER_SITE_FALLBACK_FLAG = WaffleFlag(
    f'{THIRD_PARTY_AUTH_NAMESPACE}.saml_provider_site_fallback', __name__
)


def is_saml_provider_site_fallback_enabled():
    """
    Returns True if get_from_pipeline() should fall back to a site-independent
    SAMLProviderConfig lookup when the site-filtered registry finds no match.
    """
    return SAML_PROVIDER_SITE_FALLBACK_FLAG.is_enabled()
