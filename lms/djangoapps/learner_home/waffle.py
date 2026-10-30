"""
Configuration for features of Learner Home
"""
from edx_toggles.toggles import WaffleFlag

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

# Namespace for Learner Home MFE waffle flags.
WAFFLE_FLAG_NAMESPACE = "learner_home_mfe"

# .. toggle_name: learner_home_mfe.enabled
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable to redirect user to learner home mfe
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-10-11
# .. toggle_tickets: AU-879
ENABLE_LEARNER_HOME_MFE = WaffleFlag(
    f"{WAFFLE_FLAG_NAMESPACE}.enabled",
    __name__,
)


def learner_home_mfe_enabled():
    """
    Determine if Learner Home MFE is enabled, replacing student_dashboard
    """

    return configuration_helpers.get_value(
        "ENABLE_LEARNER_HOME_MFE",
        ENABLE_LEARNER_HOME_MFE.is_enabled(),
    )

# Namespace for RV plugin feature flags.
RV_PLUGINS_NAMESPACE = "rv_plugins"

# .. toggle_name: rv_plugins.disable_rv
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Disable all RV plugin experiences.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-10-30
# .. toggle_target_removal_date: YYYY-MM-DD
# .. toggle_tickets: SUBS-1181
DISABLE_RV = WaffleFlag(
    f"{RV_PLUGINS_NAMESPACE}.disable_rv",
    __name__,
)
