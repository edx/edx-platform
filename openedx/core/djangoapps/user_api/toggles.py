"""
Toggles for the user_api app.
"""

from edx_toggles.toggles import WaffleFlag

# .. toggle_name: user_api.free_retired_learner_email_on_completion
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, a learner's retired email address is automatically freed
#    (see free_retired_learner_email) as soon as their retirement reaches the COMPLETE state via
#    PATCH /api/user/v1/accounts/update_retirement_status/. This lets the behavior be turned off
#    without pausing the retirement pipeline itself; the free_retired_user_email management command
#    is unaffected by this toggle.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2026-09-04
FREE_RETIRED_LEARNER_EMAIL_ON_COMPLETION = WaffleFlag(
    'user_api.free_retired_learner_email_on_completion', __name__
)


def should_free_retired_learner_email_on_completion():
    """
    Returns True if a learner's retired email should be automatically freed
    when their retirement reaches the COMPLETE state.
    """
    return FREE_RETIRED_LEARNER_EMAIL_ON_COMPLETION.is_enabled()
