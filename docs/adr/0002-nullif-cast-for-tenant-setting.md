# ADR 0002: `NULLIF(...)::int` cast so an unset tenant fails closed

**Status:** Accepted

## Context

Policies compare `tenant_id` with a custom setting. The obvious way to write the policy is

```sql
USING (tenant_id = current_setting('app.current_tenant_id')::int)
```

This has two failure modes, and both produce errors instead of an empty result:

1. If the setting was never defined in the session, `current_setting(name)` raises
   `unrecognized configuration parameter`. Passing `missing_ok = true` returns `NULL`
   instead.
2. Once a transaction on a connection has called `set_config(..., true)`, the setting
   stays defined for the rest of that session. After the transaction ends it resets to
   the **empty string**, not to `NULL`. On a pooled connection, any later query without a
   tenant then evaluates `''::int` and fails with
   `invalid input syntax for type integer: ""`.

The second case is easy to miss in development, where connections are short-lived. It
appears in production, where pooled connections live for hours (see
[the postmortem](../postmortem-example.md)).

## Decision

Every policy reads the tenant as

```sql
NULLIF(current_setting('app.current_tenant_id', true), '')::int
```

Unset, never-defined and empty all become `NULL`. `tenant_id = NULL` is never true, so the
query returns **zero rows**. It does not raise an error, and it never returns all rows.

## Consequences

- A code path that forgets to set the tenant fails closed and quietly: it sees nothing
  and can write nothing, because `WITH CHECK` uses the same expression.
- "Zero rows" can hide bugs that an error would have surfaced. The application layer
  therefore always binds a tenant before it queries tenant data (`get_auth_session`), and
  the tests assert both behaviours: zero rows with no tenant, and an error for the bare
  cast (`test_guc_reverts_to_empty_string_and_nullif_keeps_it_safe`).
- `tests/test_rls.py::test_every_tenant_table_has_forced_rls_and_not_null_tenant` reads
  `pg_policies` and fails if any policy is missing the `NULLIF` form.
