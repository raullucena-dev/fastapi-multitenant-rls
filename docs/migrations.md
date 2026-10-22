# Migrations

Migrations run as `app_owner` via `make migrate` (`alembic upgrade head`). The API's
`app_user` role never runs DDL.

| Revision | Purpose |
| --- | --- |
| `0001` | Core schema, composite tenant foreign keys |
| `0002` | Grants for `app_user`, `ENABLE` + `FORCE` RLS and `tenant_isolation` policies |
| `0003` | `auth` schema and the `SECURITY DEFINER` login lookup |
| `0004` | Adds `tenant_id` to `sale_items` (the pattern below), then RLS |

## Pattern: adding `tenant_id` to an existing table

`sale_items` started out isolated only through its parent `sales` row. Revision `0004`
denormalises `tenant_id` onto it so that RLS can protect it directly.

### Small table: one migration

```python
op.add_column("sale_items", sa.Column("tenant_id", sa.Integer(), nullable=True))   # 1. nullable

for parent in ("tenants", "sales", "products"):                                   # owner is subject
    op.execute(f"ALTER TABLE {parent} NO FORCE ROW LEVEL SECURITY")              # to FORCE RLS
op.execute("""UPDATE sale_items si SET tenant_id = s.tenant_id
              FROM sales s WHERE s.id = si.sale_id AND si.tenant_id IS NULL""")  # 2. backfill

op.execute("ALTER TABLE sale_items ADD CONSTRAINT ck_tmp CHECK (tenant_id IS NOT NULL) NOT VALID")
op.execute("ALTER TABLE sale_items VALIDATE CONSTRAINT ck_tmp")
op.alter_column("sale_items", "tenant_id", nullable=False)                        # 3. NOT NULL
op.execute("ALTER TABLE sale_items DROP CONSTRAINT ck_tmp")

# composite FKs, then restore FORCE on the parents, then ENABLE/FORCE RLS + policy
```

### Large or hot table: several deploys

| Step | Ships as | Lock / cost |
| --- | --- | --- |
| 1. Add nullable `tenant_id` | migration | Instant (metadata only) |
| 2. Write `tenant_id` on every insert/update | **code deploy** | none |
| 3. Backfill in batches (`WHERE id BETWEEN ...`), outside a single long transaction | script or migration | Row locks per batch |
| 4. `ADD CONSTRAINT ... CHECK (tenant_id IS NOT NULL) NOT VALID` | migration | Brief |
| 5. `VALIDATE CONSTRAINT` | migration | `SHARE UPDATE EXCLUSIVE`; reads and writes continue |
| 6. `SET NOT NULL` (skips the scan thanks to step 5), drop the check | migration | Brief |
| 7. Add composite FK `NOT VALID`, then `VALIDATE` | migration | As above |
| 8. Deploy code that sets `app.current_tenant_id` for this table's queries | **code deploy** | none |
| 9. `ENABLE` + `FORCE` RLS and create the policy | migration | Brief |

Steps 8 and 9 must stay in that order: **code before policy**. See the
[postmortem](postmortem-example.md) for what happens otherwise.

## Gotchas found while building this

- **`FORCE` RLS applies to the owner.** A backfill that joins a parent table sees zero
  rows when no tenant is set. It does not fail; it quietly updates nothing. Lift `FORCE`
  inside the migration's own transaction, or loop over tenants and set the context for
  each.
- **New foreign keys are validated under RLS too.** `ADD FOREIGN KEY` by the owner checks
  existing rows against the referenced table with `FORCE` honoured, so it fails with
  "Key (tenant_id)=(1) is not present in table tenants". `tests/test_migrations.py` found
  this.
- **`SET LOCAL` cannot take bind parameters.** Use
  `SELECT set_config('app.current_tenant_id', :id, true)`.
- **`TRUNCATE` ignores RLS.** Tests use it to reset all tenants at once, and production
  grants never include `TRUNCATE` for `app_user`.

## Checklist for a migration that touches isolation

- [ ] Code that depends on the new shape (or sets the tenant for a newly protected table)
      is already deployed.
- [ ] New tenant-owned table: `tenant_id NOT NULL`, composite FKs, `ENABLE` + `FORCE`, a
      `tenant_isolation` policy with the `NULLIF` form for both `USING` and `WITH CHECK`,
      and least-privilege grants to `app_user`.
- [ ] `tests/test_rls.py::TENANT_TABLES` updated (the catalogue test fails until it is).
- [ ] `alembic check` / `test_models_match_migrations` is clean.
- [ ] Downgrade written and exercised by the test session.
