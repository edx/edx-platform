# edX Platform Authentication - OpenAPI Specs

OpenAPI 3.0 contracts for the HTTP endpoints owned by the edX Platform
authentication domain. These are hand-written specs describing the current
behavior of the code (not generated), intended as the source of truth for
client integrations and as a base for future drift detection.

| File | Covers |
|---|---|
| [`authn-openapi.yaml`](./authn-openapi.yaml) | Core registration, login (`login_session`), logout, password reset, account activation, logistration MFE context, and JWKS - `openedx/core/djangoapps/user_authn/`. |
| [`third-party-auth-openapi.yaml`](./third-party-auth-openapi.yaml) | SAML/OAuth2/LTI federated login pipeline, account linking, and the `/api/third_party_auth/v0/` REST API - `common/djangoapps/third_party_auth/`. |
| [`oauth2-openapi.yaml`](./oauth2-openapi.yaml) | edX's OAuth2 bridge (django-oauth-toolkit): `/oauth2/authorize/`, `/oauth2/access_token/`, `/oauth2/revoke_token/`, and third-party token exchange - `openedx/core/djangoapps/oauth_dispatch/` and `openedx/core/djangoapps/auth_exchange/`. |
| [`components.yaml`](./components.yaml) | Shared schemas and security scheme definitions reused across the three specs above. Not a standalone document. |

## Viewing

Each top-level spec can be loaded independently in any OpenAPI 3.0 viewer
(Redoc, Swagger UI, Stoplight) that supports resolving relative `$ref`s to
sibling files, e.g.:

```bash
npx @redocly/cli preview-docs docs/openapi/authn-openapi.yaml
```

## Validating

```bash
pip install openapi-spec-validator prance
python3 -c "
from prance import ResolvingParser
ResolvingParser('docs/openapi/authn-openapi.yaml', backend='openapi-spec-validator')
ResolvingParser('docs/openapi/third-party-auth-openapi.yaml', backend='openapi-spec-validator')
ResolvingParser('docs/openapi/oauth2-openapi.yaml', backend='openapi-spec-validator')
print('all specs valid')
"
```

## Scope and known gaps

* Endpoints are documented from the current implementation
  (see file/line references embedded in each `description`); keep these in
  sync with `openedx/core/djangoapps/user_authn/`,
  `common/djangoapps/third_party_auth/`, `openedx/core/djangoapps/oauth_dispatch/`,
  and `openedx/core/djangoapps/auth_exchange/` as they evolve.
* HTML-rendering and redirect-only views (e.g. `/logout`, `/auth/complete/{backend}/`)
  are documented for their status codes and side effects rather than as JSON
  contracts, since they are not primarily consumed as APIs.
* `enterprise`-plugin-registered SAML provider-config endpoints
  (`/auth/saml/v0/provider_config/`, `/auth/saml/v0/provider_data/`) are out
  of scope; they live in the `edx-enterprise` package, not this repository.
