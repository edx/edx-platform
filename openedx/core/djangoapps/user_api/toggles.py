"""
Toggles for the user_api app.
"""

from edx_toggles.toggles import WaffleSwitch

_WAFFLE_NAMESPACE = 'user_api'

# .. toggle_name: user_api.enable_default_email_optin_on_enrollment
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: When enabled, a default ``email-optin = False`` UserOrgTag is recorded for the course's org
#   whenever a learner is enrolled (COURSE_ENROLLMENT_CREATED) and has no existing email opt-in preference for that
#   org. This covers enrollment paths that never ask the learner (Enterprise auto-enroll, bulk CSV, enrollment API
#   calls without ``email_opt_in``, management commands), so the email_opt_in_list report shows an explicit
#   ``False`` rather than a blank. An existing ``True``/``False`` choice is never overwritten.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-10-01
# .. toggle_target_removal_date: 2027-01-01
# .. toggle_tickets: AUT-316
ENABLE_DEFAULT_EMAIL_OPTIN_ON_ENROLLMENT = WaffleSwitch(
    f'{_WAFFLE_NAMESPACE}.enable_default_email_optin_on_enrollment', __name__
)
