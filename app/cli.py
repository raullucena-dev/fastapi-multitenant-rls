"""Operational commands. They use the owner connection (MIGRATIONS_DATABASE_URL).

python -m app.cli create-tenant --name "Demo Store" --slug demo --admin-username demo-admin
python -m app.cli seed-demo
"""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.tenant import bind_tenant
from app.models import MovementReason, Product, Role, StockMovement, User
from app.services.tenants import provision_tenant

DEMO_PASSWORD = "demo-password-123"  # noqa: S105  (documented demo credential)

DEMO_TENANTS = {
    "alpha": (
        "Demo Tenant Alpha",
        [
            ("HAM-16", "Claw hammer 16oz", "18.90", 25),
            ("NAIL-50", "Box of nails (50)", "3.40", 200),
        ],
    ),
    "beta": (
        "Demo Tenant Beta",
        [("TEA-GRN", "Green tea 100g", "6.50", 40), ("MUG-CER", "Ceramic mug", "9.00", 15)],
    ),
}


def _owner_session() -> Session:
    return Session(create_engine(get_settings().migrations_database_url), expire_on_commit=False)


def create_tenant(name: str, slug: str, admin_username: str, admin_password: str) -> int:
    with _owner_session() as db:
        tenant, _ = provision_tenant(
            db,
            name=name,
            slug=slug,
            admin_username=admin_username,
            admin_password=admin_password,
        )
    return tenant.id


def seed_demo() -> list[int]:
    """Create two demo tenants with users and stocked products (idempotent)."""
    created: list[int] = []
    for slug, (name, products) in DEMO_TENANTS.items():
        with _owner_session() as db:
            try:
                tenant, admin = provision_tenant(
                    db,
                    name=name,
                    slug=slug,
                    admin_username=f"{slug}-admin",
                    admin_password=DEMO_PASSWORD,
                )
            except IntegrityError:  # slug or username already present
                db.rollback()
                continue
            bind_tenant(db, tenant.id)
            for role in (Role.MANAGER, Role.CASHIER):
                db.add(
                    User(
                        tenant_id=tenant.id,
                        username=f"{slug}-{role.value}",
                        full_name=f"{name} {role.value.title()}",
                        password_hash=hash_password(DEMO_PASSWORD),
                        role=role.value,
                    )
                )
            for sku, product_name, price, stock in products:
                product = Product(
                    tenant_id=tenant.id,
                    sku=sku,
                    name=product_name,
                    price=Decimal(price),
                    stock_quantity=stock,
                )
                db.add(product)
                db.flush()
                db.add(
                    StockMovement(
                        tenant_id=tenant.id,
                        product_id=product.id,
                        quantity=stock,
                        reason=MovementReason.PURCHASE,
                        note="initial stock",
                        created_by_id=admin.id,
                    )
                )
            db.commit()
            created.append(tenant.id)
    return created


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    ct = sub.add_parser("create-tenant", help="provision a tenant and its first admin")
    ct.add_argument("--name", required=True)
    ct.add_argument("--slug", required=True)
    ct.add_argument("--admin-username", required=True)
    sub.add_parser("seed-demo", help=f"create demo tenants (password: {DEMO_PASSWORD})")
    args = parser.parse_args(argv)

    if args.command == "create-tenant":
        password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
        if len(password) < 8:
            print("password must be at least 8 characters", file=sys.stderr)
            return 2
        tenant_id = create_tenant(args.name, args.slug, args.admin_username, password)
        print(f"created tenant {tenant_id}")
    else:
        created = seed_demo()
        print(f"seeded tenants: {created or 'none (already present)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
