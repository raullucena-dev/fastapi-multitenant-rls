# ADR 0005: Migration strategy for tenant isolation changes

**Status:** Accepted

## Context

Isolation lives partly in the schema (policies, grants, the definer function). A
migration can therefore break isolation, or break the application, as easily as code can.
The worst case is enabling RLS before the code that sets the tenant is deployed: every
query from the old code then returns zero rows or fails (see the
[postmortem](../postmortem-example.md)).

## Decision

1. **Alembic owns everything inside the database**: tables, indexes, RLS policies,
   grants, the `auth` schema and its function. A one-off bootstrap script
   (`db/bootstrap.sql`) owns only cluster-level objects: roles and databases.
2. **Migrations run as `app_owner`**, a separate credential the API never receives.
   `transaction_per_migration = True`, so each revision is atomic.
3. **Expand, migrate, contract.** A change that the running code depends on ships in
   steps: deploy code that tolerates both shapes; migrate; deploy code that requires the
   new shape; then tighten constraints and policies. For RLS this means the code that
   sets `app.current_tenant_id` goes out **before** the migration that enables the
   policy.
4. **Adding `tenant_id` to an existing table** follows nullable, then backfill, then
   `NOT NULL`, then RLS. `0004_sale_items_tenant_id` is the reference implementation, and
   [docs/migrations.md](../migrations.md) describes the multi-deploy version for large
   tables.
5. **Cross-tenant data migrations opt out of `FORCE` explicitly and locally.** They wrap
   the work in `ALTER TABLE ... NO FORCE ROW LEVEL SECURITY` / `FORCE` inside the same
   transaction. This is needed for backfills and also for adding foreign keys: the
   initial validation of a new foreign key runs as the owner and respects `FORCE` on
   the referenced table, so with no tenant set it sees no parent rows and fails. The
   migration test suite found this.
6. **Tests guard the migrations.** The suite downgrades to base and upgrades to head
   once per run. It asserts that the models and migrations do not drift (Alembic
   `compare_metadata`), seeds data at `0003` to check the `0004` backfill, and checks the
   catalogue (`pg_class`, `pg_policies`) to confirm every table is forced and every
   policy uses the `NULLIF` form.

## Consequences

- Deploys involving RLS need two steps and some care in ordering. The checklist lives in
  `docs/migrations.md`.
- Downgrades are written and tested, but they are a development tool. In production,
  roll forward.
