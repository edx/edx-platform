"""
This module contains various configuration settings via
waffle switches for the Certificates app.
"""
from edx_toggles.toggles import SettingToggle, WaffleSwitch

# Namespace
WAFFLE_NAMESPACE = 'certificates'

# .. toggle_name: certificates.auto_certificate_generation
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: This toggle will enable certificates to be automatically generated
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-09-14
AUTO_CERTIFICATE_GENERATION = WaffleSwitch(f"{WAFFLE_NAMESPACE}.auto_certificate_generation", __name__)

# .. toggle_name: certificates.certificate_proctoring_review_block
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: This toggle blocks certificate access while required proctored exams are not in an allowed state.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2026-09-10
CERTIFICATE_PROCTORING_REVIEW_BLOCK = WaffleSwitch(
    f"{WAFFLE_NAMESPACE}.certificate_proctoring_review_block", __name__
)

# .. toggle_name: REDACT_CERTIFICATES_HISTORICAL_PII
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Clears the `name` field in the django-simple-history audit table for
#      retiring users' certificate records.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2026-05-29
REDACT_CERTIFICATES_HISTORICAL_PII = SettingToggle(
    "REDACT_CERTIFICATES_HISTORICAL_PII", default=False, module_name=__name__
)
