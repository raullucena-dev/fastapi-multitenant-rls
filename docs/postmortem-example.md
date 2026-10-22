# Postmortem: tenant-scoped API returned 500s after enabling Row-Level Security

*An example of a blameless postmortem, written as a generic engineering case study. The
scenario is realistic and is the reason behind several decisions in this repository.*

| | |
| --- | --- |
| **Impact** | About 40 minutes of elevated 5xx on all authenticated read endpoints; about 30% of requests failed at peak. No data was exposed or corrupted. |
| **Detection** | 5xx rate alert, 6 minutes after the migration finished. |
| **Severity** | High (customer-facing outage, no data loss). |

## Summary

A migration that enabled Row-Level Security on five tenant-owned tables was applied by
the pipeline **before** the application release that sets the tenant context on each
transaction. The policies also cast the tenant setting with a bare `::int`. On
long-lived pooled connections that cast hit an empty string and raised an error, so
requests failed with 500 instead of returning empty results.

## Timeline (relative)

| Time | Event |
| --- | --- |
| T+0 | Release pipeline starts. The migration stage and the application stage run in parallel, as for every release. |
| T+2m | Migration `enable_rls_core_tables` completes. |
| T+3m | Old application pods (no tenant context) begin returning empty lists for some requests and 500s for others. |
| T+8m | 5xx alert fires. The on-call engineer sees `invalid input syntax for type integer: ""` in the database logs. |
| T+15m | The application stage fails its health gate on unrelated canary noise and is held. |
| T+24m | The decision is made to roll back the migration rather than force the application deploy forward. |
| T+31m | Downgrade migration applied: policies dropped, RLS disabled. |
| T+40m | Error rate back to baseline. |

## What happened

1. The policies were written as
   `USING (tenant_id = current_setting('app.current_tenant_id', true)::int)`.
2. **Old pods never set the tenant.** On fresh connections the setting was undefined,
   `current_setting(..., true)` returned `NULL`, and queries quietly returned zero rows.
   Some users saw empty dashboards.
3. **Some connections had set the tenant before.** A batch worker that was already
   tenant-aware shares the pool configuration, and it sets the value with
   `set_config(..., true)`. When a transaction ends, such a setting reverts to `''`, not
   to `NULL`. Any later query on that connection without a tenant evaluated `''::int`
   and raised. Those requests failed with 500.
4. The deploy order was not enforced. Migrations and code shipped in parallel because, up
   to then, every migration had been additive.

## Root causes

- **Ordering:** a migration that *restricts* what the running code can see was shipped
  together with, rather than after, the code that satisfies the restriction.
- **Unsafe cast:** `::int` on a value that can legitimately be `''` turns "no tenant"
  into a hard error rather than "no rows".
- **Test gap:** integration tests used a fresh connection per test, so the "setting
  reverts to empty string on a reused connection" path never ran.

## What went well

- RLS failed closed. No request ever saw another tenant's data.
- The downgrade migration existed and had been tested, so rollback was a single command.

## Fixes

| Fix | Status |
| --- | --- |
| Policies use `NULLIF(current_setting('app.current_tenant_id', true), '')::int`: unset or empty means zero rows, never an error. | Done |
| Tenant context is set in one place for every transaction (`after_begin` hook) rather than per endpoint. | Done |
| Pipeline: migrations tagged `restrictive` run only after the application stage is healthy. | Done |
| Test that reuses one pooled connection across transactions and asserts zero rows with `NULLIF` and an error with the bare cast. | Done (`test_guc_reverts_to_empty_string_and_nullif_keeps_it_safe`) |
| Catalogue test that fails if any policy lacks the `NULLIF` form or any table lacks `FORCE`. | Done |

## Lessons

1. **Deploy code before the migration that depends on it.** A policy is a restriction on
   running code. Ship the code that satisfies the restriction first, then the
   restriction. This is expand/contract applied to security.
2. **Fail closed, but fail quietly.** Isolation should return zero rows, not raise. An
   error in a hot path turns a guard into an outage. `NULLIF` is what turns the first
   into the second.
3. **Test with the connection lifecycle of production.** Pooled, reused connections carry
   session state that fresh test connections do not.
