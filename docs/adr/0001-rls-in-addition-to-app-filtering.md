# ADR 0001: Row-Level Security in addition to application-level filtering

**Status:** Accepted

## Context

All tenants share one database and one schema. The usual way to isolate them is a
`WHERE tenant_id = :current` clause on every query. That works until someone forgets it:
a new endpoint, a report query written in a hurry, an ORM relationship that lazy-loads
without the filter, or a background job. In a shared-schema design, one forgotten filter
is a cross-tenant data leak, and code review is the only control.

## Decision

Enforce isolation at two independent layers:

1. **Application.** Every query on a tenant-owned table is built with
   `scoped(Model, tenant_id)` (`app/services/scoping.py`). The tenant id comes from the
   authenticated session, never from the request payload. Ids received in a payload are
   looked up inside the tenant and return 404 when not found.
2. **Database.** Every tenant-owned table has `tenant_id NOT NULL`, `ENABLE` and `FORCE
   ROW LEVEL SECURITY`, and a `tenant_isolation` policy with both `USING` and
   `WITH CHECK`. The API connects as `app_user`, a role that is not superuser, does not
   own the tables and has `NOBYPASSRLS`. The tenant is set for each transaction with
   `set_config('app.current_tenant_id', :id, true)`.

Composite foreign keys such as `(tenant_id, product_id) -> products (tenant_id, id)` are a
third safety net. They make a cross-tenant reference impossible even for a role that
bypasses RLS.

## Consequences

- One missing filter is no longer a breach. The tests prove each layer on its own:
  raw SQL as `app_user` shows RLS working without the app (`tests/test_rls.py`), and the
  service layer filters correctly even when run as a superuser that bypasses RLS.
- `FORCE` applies RLS to the table owner as well. This closes the "the migration role can
  read everything" hole, but migrations that read across tenants have to opt out
  explicitly (ADR 0005).
- The setting is transaction-scoped (`is_local = true`), so it cannot leak to the next
  request that reuses a pooled connection. Work that runs after a commit gets its tenant
  again automatically: a SQLAlchemy `after_begin` hook (`app/db/tenant.py`) re-applies it
  at the start of every transaction.
- There is a small cost per transaction (one `set_config` round-trip) and the planner
  sees one extra predicate. Both are negligible next to the leak they prevent, and the
  `tenant_id` indexes keep the extra predicate cheap.
- `SET LOCAL app.current_tenant_id = $1` is not used because `SET` does not accept bind
  parameters. Building the statement with string formatting would invite SQL injection.
  `set_config()` is an ordinary function and takes bind parameters.
