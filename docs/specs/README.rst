Authentication SDD Specs
########################

This directory contains narrative specifications ("SDD" - specification
documents, following this repository's ADR format from
`docs/decisions/ <../decisions/>`_) for the edx-platform authentication
domain. Each spec documents *current, implemented* behavior - flows, state
machines, and security invariants - as a companion to the machine-readable
contracts in `docs/openapi/ <../openapi/>`_.

* `registration-login/spec.rst <./registration-login/spec.rst>`_ - Registration & login flows. Companion schema:
  `authn-openapi.yaml <../openapi/authn-openapi.yaml>`_.
* `third-party-auth/spec.rst <./third-party-auth/spec.rst>`_ - SAML/OAuth2/LTI federated login pipeline. Companion schema:
  `third-party-auth-openapi.yaml <../openapi/third-party-auth-openapi.yaml>`_.
* `oauth2-bridge/spec.rst <./oauth2-bridge/spec.rst>`_ - edX OAuth2 authorization server. Companion schema:
  `oauth2-openapi.yaml <../openapi/oauth2-openapi.yaml>`_.

Conventions
***********

* Each spec follows the same section structure as
  `docs/decisions/ <../decisions/>`__: **Status/Date**, **Context**,
  **Decision** (the actual specification: endpoints, state machines,
  security requirements), **Consequences**, **References**.
* Specs are descriptive, not aspirational: they document what the code
  does today. Proposed *changes* to behavior belong in a new
  `docs/decisions/ <../decisions/>`__ ADR; once accepted and implemented,
  the relevant spec here (and the matching OpenAPI file) should be updated
  to match.
* Keep a spec and its companion OpenAPI file in sync: endpoint-level
  request/response detail belongs in the OpenAPI file; flow-level
  narrative (sequencing, state machines, invariants, security rationale)
  belongs in the spec.
