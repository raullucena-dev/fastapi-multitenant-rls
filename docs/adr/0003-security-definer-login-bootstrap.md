# ADR 0003: SECURITY DEFINER function for the login bootstrap

**Status:** Accepted

## Context

At login the only input is a username and a password. The tenant is not known yet, so RLS
correctly hides every row in `users`. Something has to map a username to its tenant
before any tenant context exists. The options considered:

| Option | Problem |
| --- | --- |
| App role with `BYPASSRLS` | Removes the database layer for **every** query, not just login. |
| A second "auth" connection pool as a privileged role | Two credentials, two pools, and a privileged connection inside the API process. |
| Tenant slug in the login form or URL | Moves the problem to the user and still needs a lookup (see ADR 0004). |
| Narrow `SECURITY DEFINER` function | Chosen. |

## Decision

Migration `0003` creates:

- schema `auth`, owned by the `NOLOGIN` role `app_auth`;
- `auth.lookup_login_user(username) RETURNS TABLE (user_id, tenant_id)`, which is
  `SECURITY DEFINER`, `STABLE`, owned by `app_auth`, and has a pinned
  `search_path = pg_catalog, pg_temp`;
- a column-level grant of `SELECT (id, tenant_id, username) ON users` to `app_auth`
  (the password hash is not readable by it);
- a policy `login_lookup ON users FOR SELECT TO app_auth USING (true)` that applies only
  to that role.

`EXECUTE` is revoked from `PUBLIC` and granted only to `app_user`.

The login flow (`app/services/auth.py`) is:

1. Call the function to get `(user_id, tenant_id)`. Nothing else crosses tenants.
2. Bind the tenant on the transaction.
3. Read the full user row through the normal RLS path, `SELECT ... FOR UPDATE`, to check
   the lockout, verify the bcrypt hash and update the failure counter.

## Consequences

- The only cross-tenant capability in the system is "username to (id, tenant)". It cannot
  log in, cannot read hashes, and cannot write.
- Usernames must be globally unique (ADR 0004). An attacker can already learn whether a
  username exists through lockout behaviour, so the function leaks nothing new.
- Unknown usernames still pay for a bcrypt check, so response time does not reveal
  whether an account exists.
- `app_owner` is a member of `app_auth` with `INHERIT FALSE, SET TRUE`. It can create the
  function as that role during migrations but gains none of its privileges. Tests check
  that the function owner is a `NOLOGIN` role and that `app_auth` cannot select
  `password_hash`.
