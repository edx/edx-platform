Registration & Login Flows
###########################

Context
*******

Learner-facing account creation and session login are handled by
``openedx/core/djangoapps/user_authn/``. This spec formalizes the current
behavior of the registration and login surfaces so that:

* client teams (logistration MFE, mobile apps, other IDAs) have a single
  authoritative description of the contract, independent of reading view code;
* the machine-readable contract in
  `docs/openapi/authn-openapi.yaml <../../openapi/authn-openapi.yaml>`_ has a
  companion narrative spec covering flows, state, and invariants that don't
  fit cleanly into an OpenAPI document;
* future changes (e.g. removing v1, adding passwordless login) have a
  baseline to diff against.

This spec does not introduce new behavior; it documents the system as
implemented as of this writing.

Actors
======

* **Anonymous browser / SPA** - the logistration MFE or any first-party
  client, unauthenticated at the start of the flow.
* **edx-platform (LMS)** - owns ``user_authn``, issues session cookies and
  JWT cookies on success.
* **Third-party identity provider** - optional, see
  `docs/specs/third-party-auth/spec.rst <../third-party-auth/spec.rst>`_;
  registration/login can be entered mid-pipeline when TPA is in progress.

Decision
********

Endpoint inventory
===================

See `authn-openapi.yaml <../../openapi/authn-openapi.yaml>`_ for the full
request/response contract. Summary:

* ``GET|POST /api/user/v2/account/registration/`` - form description /
  create account (current version).
* ``GET|POST /api/user/v1/account/registration/`` - deprecated alias
  (requires ``confirm_email``).
* ``POST /api/user/v1/validation/registration`` - inline field validation,
  no account created.
* ``GET|POST /api/user/v2/account/login_session/`` - login form
  description / authenticate, establish session + JWT cookies (current
  version).
* ``GET|POST /api/user/v1/account/login_session/`` - deprecated alias
  (``email`` instead of ``email_or_username``).
* ``POST /login_ajax`` - legacy login endpoint, same semantics as v1.
* ``POST /login_refresh`` - re-issue JWT cookies for an existing session.
* ``GET|POST /logout`` - terminate session, fan out IDA logout.
* ``GET /api/user/v1/account/password_reset/`` - forgot-password form
  description.
* ``POST /account/password`` - request a password-reset email.
* ``GET|POST /password_reset_confirm/{uidb36}-{token}/`` - Django-style
  reset confirmation.
* ``POST /password/reset/{uidb36}-{token}/`` - JSON reset confirmation
  (MFE + account recovery).
* ``POST /api/user/v1/account/password_reset/token/validate/`` - validate a
  reset token.
* ``POST /api/send_account_activation_email`` - resend the activation
  email.

Registration state machine
===========================

A registration attempt resolves to exactly one of these terminal outcomes:

1. **Created** (``200``) - account row created, user logged in, session +
   JWT cookies set. If a third-party-auth pipeline was in progress
   (``partial_pipeline`` present in session), the pending social-auth
   association is completed as part of this request.
2. **Validation failed** (``400``) - one or more fields failed validation;
   response includes ``field_errors`` keyed by field name. No account is
   created. Safe to retry after fixing the reported field(s).
3. **Conflict** (``409``) - a non-retired account already exists with the
   given email and/or username. Response includes ``error_code`` in
   ``{duplicate-email, duplicate-username, duplicate-email-username}`` and,
   for username conflicts, ``username_suggestions``.
4. **Forbidden** (``403``) - rate limited (``settings.REGISTRATION_RATELIMIT``),
   or the deployment is in third-party-auth-only mode and no TPA session is
   present, or required consent (honor code / terms of service) was not
   given.

Invariant: registration is idempotent from the caller's perspective only in
the sense that retrying with corrected fields is safe; there is no
"upsert" semantics - a second successful ``POST`` for the same identity is
impossible (it will always terminate in outcome 3).

Login state machine
====================

``POST login_session`` (and its legacy aliases) resolve to:

1. **Authenticated** (``200``) - credentials valid, account active, no lockout
   in effect. Session cookie and JWT cookie pair are set. Optional
   ``redirect_url`` is returned when the caller arrived via a redirect chain
   (e.g. finishing a paused TPA pipeline or enrollment deep link).
2. **Rejected - retryable** (``400``) with ``error_code`` in
   ``{incorrect-email-or-password, inactive-user, require-password-change,
   nudge-password-change}``. The caller may retry immediately (subject to
   rate limits).
3. **Rejected - locked out** (``403``) with ``error_code=account-locked-out``
   after repeated failures within the configured window
   (``settings.MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED`` /
   ``...LOCKOUT_PERIOD_SECS``). Retrying before the lockout window elapses
   will continue to fail even with correct credentials.
4. **Rejected - TPA required** (``403``) with
   ``error_code=third-party-auth-with-no-linked-account`` when the account
   has no usable password (SSO-only account) and no third-party-auth
   session is present.

Rate limiting is applied on two independent axes -
``LOGISTRATION_PER_EMAIL_RATELIMIT_RATE`` (per submitted identifier) and
``LOGISTRATION_RATELIMIT_RATE`` (per client IP) - both must pass for the
request to be evaluated.

Password reset flow
====================

.. code-block::

    Client                          LMS
      |--- POST /account/password -->|   (email)
      |                              |-- looks up account by email
      |                              |-- always returns 200 (never reveals
      |                              |   whether the address is registered)
      |<--- 200 {success:true} ------|
      |                              |-- (async) sends email containing
      |                              |   /password/reset/{uidb36}-{token}/
      |
      |--- POST /password/reset/{uidb36}-{token}/ --->|  (new_password1/2)
      |                              |-- validates token (single use, TTL
      |                              |   governed by Django's
      |                              |   PASSWORD_RESET_TIMEOUT)
      |                              |-- enforces password policy
      |                              |   (complexity + reuse history +
      |                              |   Have I Been Pwned compromise check)
      |                              |-- activates account if still inactive
      |                              |-- on ?is_account_recovery=true,
      |                              |   promotes the recovery email to
      |                              |   primary email
      |<--- 200 {reset_status:true} -|

Invariants:

* A reset token is single-use: a second confirmation attempt with the same
  token returns ``token_invalid``.
* The "request reset" endpoint response is intentionally
  identity-independent (always ``200``) to avoid user-enumeration; only the
  rate-limit response differs observably.
* Successful confirmation always leaves the account ``is_active=True``,
  even if it was inactive before (this doubles as an implicit activation
  path).

Security requirements
======================

* All state-changing endpoints (``POST``) require a valid CSRF token when
  called with session/cookie auth (``X-CSRFToken`` header, matching the
  ``csrftoken`` cookie).
* Registration, login, and password-reset-request endpoints are rate
  limited per-IP and, where applicable, per-identifier; limits are
  configuration-driven (``settings.py``) and MUST fail closed (reject) when
  exceeded rather than fail open.
* Passwords are validated against the platform's configured complexity
  policy and, on reset, checked against reuse history and the
  Have I Been Pwned breached-password corpus.
* Error responses for authentication failures MUST NOT distinguish between
  "no such account" and "wrong password" (both surface as
  ``incorrect-email-or-password``) to avoid user enumeration.
* JWT cookies issued on login/registration follow the claim structure
  defined in `docs/specs/oauth2-bridge/spec.rst <../oauth2-bridge/spec.rst>`_.

Consequences
************

* Clients should treat ``v1`` registration/login endpoints as deprecated:
  new integrations MUST use the ``v2`` paths; ``v1`` is retained only for
  backward compatibility and may be removed in a future major version once
  usage telemetry shows no remaining traffic.
* Because the password-reset-request endpoint never reveals account
  existence, clients cannot use it to validate whether an email is
  registered; they should use ``/api/user/v1/validation/registration``
  (which does report ``duplicate-email``) for that purpose during
  registration flows only.
* Any change to the rate-limit error codes or lockout thresholds is a
  breaking change for clients that branch on ``error_code`` and must be
  reflected in `authn-openapi.yaml <../../openapi/authn-openapi.yaml>`__
  first.

References
**********

* `docs/openapi/authn-openapi.yaml <../../openapi/authn-openapi.yaml>`__ -
  machine-readable request/response contract for every endpoint listed
  above.
* ``openedx/core/djangoapps/user_authn/views/register.py``,
  ``login.py``, ``password_reset.py``, ``logout.py``.
