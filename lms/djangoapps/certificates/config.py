"""Configuration settings for the Certificates app."""

import logging
from datetime import date, datetime, time, timezone

from django.conf import settings
from django.utils.dateparse import parse_datetime
from edx_django_utils.monitoring.utils import increment
from edx_toggles.toggles import SettingToggle, WaffleSwitch

log = logging.getLogger(__name__)

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

CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT_SETTING = (
    'CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT'
)


def _invalid_certificate_proctoring_review_block_effective_at(value):
    """Log and record an invalid certificate proctoring cutoff setting."""
    log.error(
        'Invalid %s certificate proctoring policy setting: %r',
        CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT_SETTING,
        value,
    )
    increment('certificates.proctoring_block.invalid_effective_at')
    return None


def get_certificate_proctoring_review_block_effective_at():
    """Return the configured UTC timestamp when certificate blocking began."""
    configured_value = getattr(
        settings,
        CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT_SETTING,
        None,
    )
    if configured_value is None:
        return None

    if isinstance(configured_value, str):
        original_value = configured_value
        try:
            configured_value = parse_datetime(configured_value)
        except (TypeError, ValueError):
            return _invalid_certificate_proctoring_review_block_effective_at(original_value)
        if configured_value is None:
            try:
                configured_value = datetime.combine(
                    date.fromisoformat(original_value),
                    time.min,
                    tzinfo=timezone.utc,
                )
            except (TypeError, ValueError):
                return _invalid_certificate_proctoring_review_block_effective_at(original_value)
    elif isinstance(configured_value, date) and not isinstance(configured_value, datetime):
        configured_value = datetime.combine(configured_value, time.min, tzinfo=timezone.utc)

    elif not isinstance(configured_value, datetime):
        return _invalid_certificate_proctoring_review_block_effective_at(configured_value)

    if configured_value.tzinfo is None:
        configured_value = configured_value.replace(tzinfo=timezone.utc)

    return configured_value.astimezone(timezone.utc)


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
