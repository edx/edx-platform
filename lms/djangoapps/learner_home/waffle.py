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

# .. toggle_name: rv_plugins.learner_dashboard_no_courses_view
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Enable the RV widget for the Learner Dashboard no-courses view.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2026-10-27
RV_LEARNER_DASHBOARD_NO_COURSES_VIEW = WaffleFlag(
    f"{RV_PLUGINS_NAMESPACE}.learner_dashboard_no_courses_view",
    __name__,
)


def get_rv_plugin_settings():
    """
    Return the enabled state of RV plugins.
    """
    return {
        "isEnabled": RV_LEARNER_DASHBOARD_NO_COURSES_VIEW.is_enabled()
    }
