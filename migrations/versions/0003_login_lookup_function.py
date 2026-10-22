"""SECURITY DEFINER login lookup owned by the NOLOGIN role app_auth.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22

At login the tenant is not yet known, so RLS hides every user row. Instead of
giving the app role BYPASSRLS, a single function maps a username to
(user_id, tenant_id) and nothing else. It runs as app_auth, which:

* cannot log in,
* can read only the id, tenant_id and username columns of users,
* is matched by a SELECT-only policy that applies to app_auth alone.

The application then sets the tenant and reads the full user row (password
hash, lockout state) through the normal RLS-protected path. See ADR 0003.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA auth AUTHORIZATION app_auth")
    op.execute("GRANT USAGE ON SCHEMA public TO app_auth")
    op.execute("GRANT SELECT (id, tenant_id, username) ON users TO app_auth")
    op.execute("CREATE POLICY login_lookup ON users FOR SELECT TO app_auth USING (true)")

    # Create the function as app_auth so that it is the owner (and definer).
    op.execute("SET LOCAL ROLE app_auth")
    op.execute(
        """
        CREATE FUNCTION auth.lookup_login_user(p_username text)
        RETURNS TABLE (user_id integer, tenant_id integer)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT u.id, u.tenant_id
            FROM public.users AS u
            WHERE u.username = p_username
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth.lookup_login_user(text) FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA auth FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA auth TO app_user")
    op.execute("GRANT EXECUTE ON FUNCTION auth.lookup_login_user(text) TO app_user")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE app_auth")
    op.execute("DROP FUNCTION auth.lookup_login_user(text)")
    op.execute("DROP SCHEMA auth")
    op.execute("RESET ROLE")
    op.execute("DROP POLICY login_lookup ON users")
    op.execute("REVOKE ALL ON users FROM app_auth")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_auth")
