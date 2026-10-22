# ADR 0004: Single login domain instead of a subdomain per tenant

**Status:** Accepted

## Context

Multi-tenant SaaS products usually choose between:

- **A subdomain per tenant** (`acme.example.app`): the tenant comes from the `Host`
  header and usernames only need to be unique within a tenant.
- **A single login domain** (`app.example.app`): one login form, and the tenant comes
  from the user record.

## Decision

Use a single login domain, with globally unique usernames.

- The tenant is derived from the authenticated user and carried in the signed session
  cookie together with a random session token. It is never taken from the `Host` header,
  a path segment, a query parameter or the request body.
- The server stores only the SHA-256 of the session token, in a tenant-owned table.
  Logout, deactivation and password changes revoke sessions on the server.

## Consequences

- A request has only one input that decides the tenant: a value signed with the server
  secret. A subdomain design adds a second one (`Host`) that must agree with the session,
  which is a classic source of confused-deputy bugs.
- There is no wildcard DNS or TLS, and no per-tenant cookie scoping to manage.
- Usernames are a global namespace, so creating `jdoe` fails with `409` if any tenant
  already has it. That reveals that the name exists somewhere, but nothing about which
  tenant has it. Teams that need per-tenant namespaces can use email-style usernames
  (`jdoe@tenant-slug`) without changing this design.
- Even a correctly signed cookie with its tenant id swapped resolves to no session,
  because the session row is protected by RLS (`test_valid_token_with_swapped_tenant_finds_no_session`).
- Moving to subdomains later only adds a check (`Host` tenant == session tenant). It does
  not change the data model.
