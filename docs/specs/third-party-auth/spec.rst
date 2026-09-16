Third-Party Authentication (TPA) Pipeline
##########################################

Context
*******

``common/djangoapps/third_party_auth/`` integrates SAML, OAuth2, and LTI
identity providers via ``python-social-auth`` (``social_django``), with
edX-specific pipeline steps for account linking, registration hand-off, and
provider configuration. This spec formalizes the pipeline's contract so
that:

* client teams (logistration MFE, mobile apps) can implement the
  login/registration UI without reading ``pipeline.py`` line by line;
* the machine-readable contract in
  `docs/openapi/third-party-auth-openapi.yaml <../../openapi/third-party-auth-openapi.yaml>`_
  has a companion narrative spec covering the pipeline state machine and
  security invariants that don't fit cleanly into an OpenAPI document;
* integrators adding a new identity provider know which steps and
  contracts they must satisfy.

This spec does not introduce new behavior; it documents the system as
implemented as of this writing.

Actors
======

* **Anonymous browser** - starts the flow at ``/auth/login/{backend}/``.
* **Identity provider (IdP)** - external SAML IdP, OAuth2 provider (Google,
  Facebook, Microsoft, ...), or LTI tool consumer.
* **edx-platform (LMS)** - the OAuth2/SAML *service provider* / *relying
  party*; hosts the pipeline and owns the resulting edX account.
* **Downstream registration/login** - see
  `docs/specs/registration-login/spec.rst <../registration-login/spec.rst>`_;
  the TPA pipeline can hand off into registration or login when it needs
  additional information or final confirmation.

Decision
********

Endpoint inventory
===================

See `third-party-auth-openapi.yaml <../../openapi/third-party-auth-openapi.yaml>`_
for the full request/response contract. Summary:

* ``GET /auth/login/{backend}/`` - begin federated login (redirect to IdP).
* ``GET|POST /auth/complete/{backend}/`` - provider callback; resumes the
  pipeline.
* ``POST /auth/login/lti/`` - LTI 1.x signed launch login.
* ``GET /auth/idp_redirect/{provider_slug}/`` - resolve a stable slug to
  ``/auth/login/{backend}/``.
* ``GET /auth/inactive`` - landing page for a linked-but-inactive account.
* ``POST /auth/disconnect_json/{backend}/[{association_id}/]`` - unlink a
  provider (JSON, CORS-friendly).
* ``POST /auth/disconnect/{backend}/`` - unlink a provider (legacy,
  redirect-based).
* ``GET|POST /auth/exception/`` - pipeline error handler.
* ``GET /auth/saml/metadata.xml`` - this instance's SAML SP metadata.
* ``GET /auth/saml/v0/saml_configuration/`` - public SAML configuration
  records.
* ``GET|DELETE /api/third_party_auth/v0/users/`` - username/email <->
  provider association lookup.
* ``GET /api/third_party_auth/v0/providers/{id}/users`` - server-to-server
  username <-> remote-id mapping.
* ``GET /api/third_party_auth/v0/providers/user_status`` - per-provider
  connection status for account settings.

Pipeline state machine
=======================

The pipeline is a fixed, ordered sequence of steps
(``SOCIAL_AUTH_PIPELINE`` setting). Conceptually it has three phases:

1. **Identification** - exchange the provider's response (OAuth code, SAML
   assertion, LTI launch) for a stable remote identity
   (``provider_id`` + ``remote_id``).
2. **Association** - resolve the remote identity to an edX account:

   * If a ``UserSocialAuth`` row already links this remote identity to an
     edX account, the pipeline short-circuits straight to **Login**.
   * Otherwise the pipeline *pauses* (``partial_pipeline`` persisted to the
     session) and control returns to the client as a ``302``/``200`` to
     ``/api/mfe_context`` with ``pipelineUserDetails`` populated from the
     provider's claims (email, name, username hint). The client must
     collect any additional required registration fields and submit them
     to `registration <../registration-login/spec.rst>`_ or
     `login <../registration-login/spec.rst>`_, which resumes and
     completes the pipeline.

3. **Finalization** - once an edX account is determined (new or existing),
   remaining pipeline steps run (e.g. syncing profile data if
   ``syncLearnerProfileData`` is enabled) and the user is logged in exactly
   as in the core login flow (session + JWT cookies set).

.. code-block::

    Browser                 LMS (TPA)                     IdP
      |-- GET /auth/login/{backend}/ ------------------------>|
      |                        |-- redirect to IdP ---------->|
      |                                                        |-- user authenticates
      |<----------------------------- redirect/POST -----------|
      |-- GET|POST /auth/complete/{backend}/ ----------------->|
      |                        |-- resolve remote identity
      |                        |-- known association? --yes--> log in, set cookies, 302 to `next`
      |                        |
      |                        `--no--> pause pipeline, 200/302 to MFE
      |                                 with pipelineUserDetails
      |-- (MFE renders reg/login form pre-filled) -->|
      |-- POST /api/user/v2/account/registration/ ----------->|
      |                        |-- resumes + completes pipeline
      |<--- 200, cookies set --|

Account linking / unlinking
============================

* Linking additional providers to an *already logged-in* account uses the
  same ``/auth/login/{backend}/?auth_entry=account_settings`` entry point;
  the pipeline detects an authenticated session and links rather than logs
  in.
* Unlinking removes the ``UserSocialAuth`` row for that
  ``(backend, association_id)``. Prefer ``/auth/disconnect_json/`` (JSON,
  same-origin-friendly) over the legacy ``/auth/disconnect/`` redirect
  flow for new integrations.
* Unlinking a SAML association raises a ``SAMLAccountDisconnected`` signal
  (see `0026-enterprise-decoupled-from-third-party-auth
  <../../decisions/0026-enterprise-decoupled-from-third-party-auth.rst>`_)
  so that enterprise-specific cleanup can react without ``third_party_auth``
  importing enterprise code directly.

Error handling
==============

All pipeline exceptions (``AuthException`` and subclasses) are funneled
through the ``ExceptionMiddleware`` to ``/auth/exception/``, which:

* reads ``auth_entry`` from the session to decide where to redirect
  (``/login``, ``/register``, or ``/account/settings``);
* surfaces a user-facing message via the Django messages framework;
* never exposes raw provider error payloads to the browser.

Security requirements
======================

* SAML responses are verified against the configured IdP's signing
  certificate; assertions outside their validity window or with an
  unrecognized signer are rejected before any pipeline step runs.
* LTI launches are validated via OAuth 1.0a signature verification against
  the configured consumer key/secret before ``lti_login_and_complete_view``
  invokes the pipeline.
* ``/auth/disconnect_json/`` and ``/auth/disconnect/`` require an
  authenticated session and only allow a user to unlink their own
  associations (never another user's).
* The server-to-server mapping endpoint
  (``/api/third_party_auth/v0/providers/{id}/users``) requires either a JWT
  with ``tpa:read`` scope or an OAuth2 client-credentials token; it MUST
  NOT be reachable with only a browser session, since it is designed for
  trusted backend services.
* `/auth/saml/v0/saml_configuration/` only returns configuration rows
  flagged ``is_public=true``; private SAML configuration (keys, secrets)
  is never exposed via this endpoint.

Consequences
************

* Any new identity provider backend must terminate in the same
  Identification -> Association -> Finalization phases described above so
  that the MFE context contract (``pipelineUserDetails``,
  ``finishAuthUrl``) remains stable for all providers.
* Because pipeline pause/resume state lives in the Django session, the TPA
  flow requires session-cookie continuity across the redirect to the IdP
  and back; clients cannot complete a paused pipeline via a stateless
  (e.g. mobile-native, cookie-less) HTTP client without a session cookie
  jar.
* The enterprise plugin owns any enterprise-specific pipeline steps (see
  `0026 <../../decisions/0026-enterprise-decoupled-from-third-party-auth.rst>`_);
  this spec's pipeline phases apply regardless of whether enterprise is
  installed.

References
**********

* `docs/openapi/third-party-auth-openapi.yaml <../../openapi/third-party-auth-openapi.yaml>`__ -
  machine-readable request/response contract for every endpoint listed
  above.
* `docs/decisions/0025-saml-admin-views-in-enterprise-plugin.rst <../../decisions/0025-saml-admin-views-in-enterprise-plugin.rst>`_
* `docs/decisions/0026-enterprise-decoupled-from-third-party-auth.rst <../../decisions/0026-enterprise-decoupled-from-third-party-auth.rst>`_
* ``common/djangoapps/third_party_auth/views.py``, ``pipeline.py``,
  ``lti.py``, ``saml.py``, ``api/views.py``.
