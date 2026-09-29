edX OAuth2 Bridge
##################

Context
*******

``openedx/core/djangoapps/oauth_dispatch/`` (with
``openedx/core/djangoapps/auth_exchange/`` for third-party token exchange)
implements edX's OAuth 2.0 authorization server on top of
`django-oauth-toolkit <https://github.com/jazzband/django-oauth-toolkit>`_
(DOT), with edX-specific overrides for JWT issuance, restricted
applications, and organization-scoped access. This spec formalizes the
bridge's contract so that:

* other IDAs and external integrators have a single authoritative
  description of how to obtain and validate edX access tokens;
* the machine-readable contract in
  `docs/openapi/oauth2-openapi.yaml <../../openapi/oauth2-openapi.yaml>`_
  has a companion narrative spec covering the JWT claim contract and
  security invariants that don't fit cleanly into an OpenAPI document;
* the relationship between this bridge and the
  `third-party-auth pipeline <../third-party-auth/spec.rst>`_ /
  `core login <../registration-login/spec.rst>`_ is explicit.

This spec does not introduce new behavior; it documents the system as
implemented as of this writing.

Actors
======

* **OAuth2 client application** - a registered ``Application`` (DOT model),
  either confidential (server-to-server, has a ``client_secret``) or public
  (native/mobile app, PKCE-style).
* **Resource owner** - the edX learner/staff user, for grants that involve a
  user (``authorization_code``, ``password``).
* **edx-platform (LMS)** - the OAuth2 authorization server and, via JWT
  validation, a resource server for its own and other IDAs' APIs.

Decision
********

Endpoint inventory
===================

See `oauth2-openapi.yaml <../../openapi/oauth2-openapi.yaml>`_ for the full
request/response contract. Summary:

* ``GET|POST /oauth2/authorize/`` - RFC 6749 authorization endpoint
  (consent screen).
* ``POST /oauth2/access_token/`` - RFC 6749 token endpoint (all grant
  types).
* ``POST /oauth2/revoke_token/`` - RFC 7009 token revocation.
* ``POST /oauth2/exchange_access_token/{backend}/`` - exchange a
  third-party OAuth2 token for an edX token.
* ``POST /oauth2/login/`` - exchange an edX access token for a session
  cookie.

Supported grant types
=======================

* ``authorization_code`` - browser-based apps (consent screen, redirect
  flow). User present: yes.
* ``password`` - first-party trusted clients (mobile apps, legacy). User
  present: yes (credentials passed directly).
* ``client_credentials`` - service-to-service, no end user. User present:
  no.
* ``refresh_token`` - renew an access token without re-authenticating.
  User present: implicit (from the prior grant).

Token formats
==============

Every grant can mint either:

* **Opaque bearer token** (default) - a DOT-native, database-backed token.
  Revocable at any time via ``/oauth2/revoke_token/``; validating it
  requires a call back to the LMS (introspection) or DOT's own DB lookup.
* **JWT access token** (``token_type=jwt``) - a self-contained, signed
  token carrying edX-specific claims (below). Validated locally by any
  relying party that has the signing key (see
  `docs/specs/registration-login/spec.rst <../registration-login/spec.rst>`_'s
  reference to ``/auth/jwks.json``), without a round-trip to the LMS.
  **Not revocable** mid-lifetime; only the associated refresh token can be
  revoked, which prevents *future* JWTs from being minted.

Signing: symmetric (HS256, shared secret) by default; asymmetric (RS512)
when the client is a *restricted application* or ``asymmetric_jwt=true`` is
passed. Restricted-application tokens are additionally issued with
``expires_in <= 0`` (immediately expired) so they carry an audit trail
without granting live API access.

JWT claim contract
====================

.. code-block::

    {
      "aud": "<audience>",           standard
      "iss": "<issuer>",             standard
      "iat": 1700000000,             standard
      "exp": 1700003600,             standard
      "sub": "<anonymized user id>", standard (hashed, not the raw pk)
      "preferred_username": "janedoe",
      "grant_type": "password",
      "scopes": ["read", "write", "profile"],
      "version": "1.3.0",
      "is_restricted": false,
      "email_verified": true,
      "filters": ["content_org:OrgX"],

      // present only if "email" scope was granted:
      "email": "jane@example.com",

      // present only if "profile" scope was granted:
      "name": "Jane Doe", "given_name": "Jane", "family_name": "Doe",
      "administrator": false, "superuser": false,

      // present only if "user_id" scope was granted (password grant only):
      "user_id": 42,

      // present only if the user has edx-rbac role assignments:
      "roles": ["enterprise_admin:OrgX"]
    }

Invariant: claims gated by scope MUST NOT be included when the
corresponding scope was not granted, even if the requesting client would
otherwise be entitled to that data by role - scope is the sole gate for
claim inclusion.

Token lifecycle
=================

.. code-block::

    Client                                    LMS
      |-- POST /oauth2/access_token/ --------->|  grant_type=client_credentials
      |                                         |-- validate client_id/secret
      |                                         |-- resolve ApplicationAccess
      |                                         |   (scopes, org filters)
      |                                         |-- mint token (opaque or JWT)
      |<-- 200 {access_token, expires_in, ...} -|
      |
      |-- Authorization: Bearer <token> ------->|  (subsequent API calls)
      |                                         |-- opaque: DB lookup + scope check
      |                                         |-- JWT: signature + exp check,
      |                                         |   scope/claim check, no DB hit
      |
      |-- POST /oauth2/revoke_token/ ---------->|  token=<refresh_token>
      |<-- 200 (empty) ------------------------|
      |                                         |-- opaque access token: invalidated
      |                                         |-- JWT access token already issued:
      |                                         |   remains valid until natural expiry
      |                                         |   (revocation only blocks re-issuance)

Relationship to other auth surfaces
=====================================

* ``/oauth2/authorize/`` requires an existing session; a client without one
  must first complete `core login
  <../registration-login/spec.rst>`__ or the
  `third-party-auth pipeline <../third-party-auth/spec.rst>`__.
* ``/oauth2/exchange_access_token/{backend}/`` lets a client that already
  holds a *third-party* provider token skip the browser-redirect TPA
  pipeline entirely, provided the backend's `social_django` pipeline can
  resolve that token to a linked edX account.
* ``/oauth2/login/`` is the inverse of the login flow: it converts a
  previously-issued OAuth2 token back into a session cookie, restricted to
  tokens from the ``password`` grant or ``skip_authorization`` apps, and
  only for asymmetric JWTs (symmetric JWTs are rejected, since they cannot
  be cheaply distinguished from tokens minted for a different, less
  trusted, audience).

Security requirements
======================

* ``/oauth2/access_token/`` and ``/oauth2/revoke_token/`` are CSRF-exempt
  (they are called by non-browser clients using client credentials, not
  cookies) but MUST authenticate the client via ``client_id`` (+
  ``client_secret`` for confidential clients) on every call.
* Confidential clients MUST supply ``client_secret``; public clients are
  restricted to grant types and scopes that don't require it
  (``authorization_code`` with PKCE, or restricted ``password`` usage for
  first-party apps only).
* Scope and content-organization filtering is enforced via the
  ``ApplicationAccess`` model at token-mint time, not at resource-access
  time; a token's ``filters``/``scopes`` claims are authoritative for its
  entire lifetime.
* Restricted applications always receive immediately-expired tokens
  (``expires_in <= 0``); relying parties MUST treat any restricted-app
  token as unusable for live API access regardless of the nominal
  ``token_type``.
* JWT signature verification MUST use the key identified by the token's
  ``kid`` header looked up via ``/auth/jwks.json``; relying parties MUST
  reject tokens signed with an unknown ``kid`` rather than falling back to
  a default key.

Consequences
************

* Because JWT access tokens are not revocable, any client-facing feature
  that requires immediate access termination (e.g. "sign out everywhere")
  must operate on refresh tokens and rely on the (short) JWT TTL for
  worst-case exposure window, not on ``/oauth2/revoke_token/`` alone.
* Adding a new scope-gated claim to the JWT payload is a backward-compatible
  addition for existing tokens (old tokens simply lack the claim) but is a
  breaking change for `oauth2-openapi.yaml <../../openapi/oauth2-openapi.yaml>`__
  and must be reflected there first.
* Relying parties that only validate opaque tokens via DB/introspection
  will not automatically support JWT tokens minted with
  ``token_type=jwt``; those code paths must be updated together.

References
**********

* `docs/openapi/oauth2-openapi.yaml <../../openapi/oauth2-openapi.yaml>`__ -
  machine-readable request/response contract for every endpoint listed
  above.
* ``openedx/core/djangoapps/oauth_dispatch/views.py``,
  ``dot_overrides/views.py``, ``jwt.py``, ``models.py``, ``scopes.py``.
* ``openedx/core/djangoapps/auth_exchange/views.py``.
