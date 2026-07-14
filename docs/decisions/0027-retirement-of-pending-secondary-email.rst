Retirement of Pending Secondary Email (``new_secondary_email``)
################################################################

**Status**: Accepted

**Date**: 2026-06-10

-----

Context
*******

Open edX supports an account recovery email feature that allows users to
configure a secondary email address.

When a user updates their recovery email, the new address is temporarily stored
in the ``student_pendingsecondaryemailchange`` table until the user confirms
ownership of the email address.

This table contains:

* ``new_secondary_email``
* ``activation_key``

Once the confirmation link is used:

1. ``activate_secondary_email()`` is executed.
2. The confirmed email is stored in the account recovery model.
3. The pending record is redacted and deleted.

During a review of Personally Identifiable Information (PII) handling in the
user retirement workflow, it was discovered that ``new_secondary_email`` was
annotated as::

    .. pii: Contains new_secondary_email
    .. pii_types: email_address
    .. pii_retirement: retained

Although the field contains an email address, it was configured to be retained
after user retirement.

No documented architectural decision or business requirement explaining this
behavior could be found.

-----

Problem
*******

Retaining ``new_secondary_email`` after a user has requested retirement is
inconsistent with the retirement model, whose purpose is to remove or anonymize
user PII.

Because ``new_secondary_email`` is only temporary state used while confirming a
recovery email address, retaining it indefinitely provides no clear functional
benefit while unnecessarily preserving personal data.

During investigation, it was also questioned whether this field was required to
support Enterprise account recovery during the retirement cooling-off period.

-----

Decision
********

* ``new_secondary_email`` **SHALL** be treated as temporary PII.

* The retirement process **SHALL** redact and remove ``new_secondary_email`` in
  the same manner as other email-address PII.

* Enterprise account recoverability **SHALL NOT** depend on the retention of
  ``new_secondary_email``.

* The ability to cancel a retirement request during the cooling-off period is
  provided through existing account recovery mechanisms, including retained
  authentication state where appropriate (such as Social Auth links during the
  cooling-off period), and not through pending secondary email records.

-----

Consequences
************

Positive
========

* Eliminates unnecessary retention of personal data.
* Makes retirement behavior consistent across email-related PII.
* Simplifies the retirement model by treating temporary email state the same as
  other transient user data.
* Improves compliance with privacy requirements by ensuring temporary email
  addresses are not retained after retirement.

Negative
========

* None identified.

-----

References
**********

* Review of ``new_secondary_email`` PII annotations.
* User retirement implementation discussion.
* Enterprise account recovery investigation.
* Pull request implementing retirement handling for ``new_secondary_email``.
