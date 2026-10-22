# Architecture Decision Records

Short records of decisions that shape tenant isolation. Format: context, decision, consequences.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-rls-in-addition-to-app-filtering.md) | Row-Level Security in addition to application-level filtering | Accepted |
| [0002](0002-nullif-cast-for-tenant-setting.md) | `NULLIF(...)::int` cast so an unset tenant fails closed | Accepted |
| [0003](0003-security-definer-login-bootstrap.md) | SECURITY DEFINER function for the login bootstrap | Accepted |
| [0004](0004-single-login-domain.md) | Single login domain instead of a subdomain per tenant | Accepted |
| [0005](0005-migration-strategy.md) | Migration strategy for tenant isolation changes | Accepted |
