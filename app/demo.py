"""Public demo data: two fictitious stores with products, stock history and two weeks of sales.

`reset_demo` wipes every tenant and reseeds the demo through the same services the API
uses, so the generated data obeys every business rule (stock never negative, ledger
entries for each sale, audit log). It runs with the owner connection and refuses to run
unless DEMO_MODE is enabled.
"""

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.security import hash_password
from app.models import AuditLog, Product, Role, Sale, StockMovement, User
from app.schemas.products import ProductCreate
from app.schemas.sales import SaleCreate, SaleItemIn
from app.schemas.stock import StockMovementCreate
from app.services import products as product_service
from app.services import sales as sale_service
from app.services import stock as stock_service
from app.services.tenants import provision_tenant

DEMO_PASSWORD = "demo1234"  # noqa: S105  (public, documented demo credential)
PARAGUAY = timezone(timedelta(hours=-3))
OPENING_HOUR, CLOSING_HOUR = 8, 20

# (sku, name, price in PYG, initial stock)
NORTH_PRODUCTS = [
    ("ALM-001", "Yerba mate 500 g", 18500, 80),
    ("ALM-002", "Yerba mate 1 kg", 34000, 50),
    ("ALM-003", "Chipa (docena)", 15000, 60),
    ("ALM-004", "Mandioca 1 kg", 5000, 120),
    ("ALM-005", "Harina de trigo 1 kg", 6500, 90),
    ("ALM-006", "Azúcar 1 kg", 7500, 100),
    ("ALM-007", "Arroz 1 kg", 8900, 90),
    ("ALM-008", "Fideos tallarín 500 g", 6900, 100),
    ("ALM-009", "Aceite de girasol 900 ml", 14500, 60),
    ("ALM-010", "Leche entera 1 L", 6800, 120),
    ("ALM-011", "Queso Paraguay 1 kg", 42000, 25),
    ("ALM-012", "Huevos (maple de 30)", 32000, 40),
    ("ALM-013", "Galletitas de agua 200 g", 4500, 100),
    ("ALM-014", "Dulce de guayaba 500 g", 12000, 40),
    ("ALM-015", "Café molido 250 g", 19500, 40),
    ("ALM-016", "Gaseosa cola 2 L", 12500, 80),
    ("ALM-017", "Agua mineral 2 L", 6500, 100),
    ("ALM-018", "Jugo de naranja 1 L", 9500, 60),
    ("ALM-019", "Sal fina 1 kg", 3500, 80),
    ("ALM-020", "Poroto rojo 1 kg", 13000, 50),
    ("ALM-021", "Detergente 500 ml", 9800, 60),
    ("ALM-022", "Jabón en polvo 800 g", 21000, 40),
    ("ALM-023", "Papel higiénico x4", 13500, 60),
    ("ALM-024", "Pilas AA x2", 9000, 50),
    ("ALM-025", "Carbón vegetal 3 kg", 16000, 40),
]

SOUTH_PRODUCTS = [
    ("FER-001", "Termo de acero 1 L", 120000, 15),
    ("FER-002", "Guampa de palo santo", 45000, 25),
    ("FER-003", "Bombilla de alpaca", 35000, 30),
    ("FER-004", "Martillo de carpintero 16 oz", 58000, 20),
    ("FER-005", "Destornillador plano", 18000, 40),
    ("FER-006", "Juego de llaves combinadas (8)", 135000, 12),
    ("FER-007", "Cinta métrica 5 m", 32000, 30),
    ("FER-008", "Taladro percutor 650 W", 420000, 8),
    ("FER-009", "Brocas para madera (5)", 38000, 25),
    ("FER-010", 'Tornillos 1" (100 u.)', 15000, 60),
    ("FER-011", 'Clavos 2" 1 kg', 17000, 50),
    ("FER-012", "Pintura látex blanca 4 L", 165000, 15),
    ("FER-013", "Rodillo 23 cm", 29000, 25),
    ("FER-014", 'Pincel 2"', 11000, 40),
    ("FER-015", "Lija al agua (hoja)", 3500, 150),
    ("FER-016", "Cinta aisladora", 12000, 60),
    ("FER-017", "Foco LED 9 W", 14000, 80),
    ("FER-018", "Alargue eléctrico 5 m", 47000, 20),
    ("FER-019", "Candado 40 mm", 39000, 25),
    ("FER-020", "Manguera de jardín 15 m", 98000, 12),
    ("FER-021", "Pala de punta", 76000, 15),
    ("FER-022", 'Machete 18"', 52000, 20),
    ("FER-023", "Escalera de aluminio 5 peldaños", 380000, 6),
    ("FER-024", "Silicona transparente", 26000, 30),
    ("FER-025", "Guantes de trabajo", 22000, 40),
]


@dataclass(frozen=True)
class DemoStore:
    name: str
    slug: str
    products: list[tuple[str, str, int, int]]
    sales_per_day: tuple[int, int]

    def username(self, role: Role) -> str:
        return f"{self.slug}.{role.value}"


DEMO_STORES = [
    DemoStore("Demo Store North", "north", NORTH_PRODUCTS, (8, 14)),
    DemoStore("Demo Store South", "south", SOUTH_PRODUCTS, (4, 9)),
]

ROLE_NAMES = {Role.ADMIN: "Administrador", Role.MANAGER: "Encargado", Role.CASHIER: "Cajero"}


def demo_info() -> dict[str, Any]:
    """Credentials and context shown on the frontend's login page."""
    return {
        "password": DEMO_PASSWORD,
        "stores": [
            {
                "name": store.name,
                "slug": store.slug,
                "users": [{"username": store.username(role), "role": role.value} for role in Role],
            }
            for store in DEMO_STORES
        ],
        "notes": [
            "Each store is a separate tenant: logging into one never shows the other's data.",
            "All data is fictitious and is reset every night.",
        ],
    }


@dataclass
class StoreSummary:
    tenant_id: int
    products: int = 0
    sales: int = 0
    movements: int = 0
    users: list[str] = field(default_factory=list)


class DemoModeRequiredError(RuntimeError):
    pass


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=PARAGUAY).astimezone(UTC)


def _backdate_sale(db: Session, sale_id: int, when: datetime) -> None:
    db.execute(update(Sale).where(Sale.id == sale_id).values(created_at=when))
    db.execute(
        update(StockMovement).where(StockMovement.sale_id == sale_id).values(created_at=when)
    )
    db.execute(
        update(AuditLog)
        .where(AuditLog.entity_type == "sale", AuditLog.entity_id == sale_id)
        .values(created_at=when)
    )


def _backdate_movement(db: Session, movement_id: int, when: datetime) -> None:
    db.execute(update(StockMovement).where(StockMovement.id == movement_id).values(created_at=when))
    db.execute(
        update(AuditLog)
        .where(AuditLog.entity_type == "stock_movement", AuditLog.entity_id == movement_id)
        .values(created_at=when)
    )


class _StoreSeeder:
    """Seeds one store, step by step, through the regular service layer."""

    def __init__(self, db: Session, store: DemoStore, rng: random.Random) -> None:
        self.db = db
        self.store = store
        self.rng = rng
        self.staff: dict[Role, User] = {}
        self.catalog: list[Product] = []
        self.summary = StoreSummary(tenant_id=0)

    def _move(
        self, product: Product, quantity: int, reason: Literal["purchase", "adjustment"], note: str
    ) -> StockMovement:
        movement = stock_service.create_movement(
            self.db,
            self.staff[Role.MANAGER],
            StockMovementCreate(
                product_id=product.id,
                quantity=quantity,
                reason=reason,
                note=note,
            ),
        )
        self.summary.movements += 1
        return movement

    def create_staff(self) -> None:
        store = self.store
        tenant, admin = provision_tenant(
            self.db,
            name=store.name,
            slug=store.slug,
            admin_username=store.username(Role.ADMIN),
            admin_password=DEMO_PASSWORD,
            admin_full_name=f"{ROLE_NAMES[Role.ADMIN]} {store.slug.title()}",
        )
        self.summary = StoreSummary(tenant_id=tenant.id, users=[admin.username])
        self.staff[Role.ADMIN] = admin
        for role in (Role.MANAGER, Role.CASHIER):
            user = User(
                tenant_id=tenant.id,
                username=store.username(role),
                full_name=f"{ROLE_NAMES[role]} {store.slug.title()}",
                password_hash=hash_password(DEMO_PASSWORD),
                role=role.value,
            )
            self.db.add(user)
            self.staff[role] = user
            self.summary.users.append(user.username)
        self.db.commit()

    def create_catalog(self, opening: datetime) -> None:
        for sku, name, price, initial in self.store.products:
            product = product_service.create_product(
                self.db,
                self.staff[Role.ADMIN],
                ProductCreate(sku=sku, name=name, price=Decimal(price)),
            )
            self.catalog.append(product)
            _backdate_movement(
                self.db, self._move(product, initial, "purchase", "Stock inicial").id, opening
            )
        self.db.execute(update(Product).values(created_at=opening, updated_at=opening))
        self.db.execute(
            update(AuditLog)
            .where(AuditLog.entity_type.in_(("product", "user")))
            .values(created_at=opening)
        )
        self.db.commit()
        self.summary.products = len(self.catalog)

    def restock_and_correct(self, when: datetime) -> None:
        """Restocks the five lowest-stock products and records a wrong entry plus its reversal."""
        for product in sorted(self.catalog, key=lambda p: p.stock_quantity)[:5]:
            _backdate_movement(self.db, self._move(product, 30, "purchase", "Reposición").id, when)
        mistake = self._move(self.catalog[0], 100, "adjustment", "Carga errónea")
        reversal = stock_service.reverse_movement(self.db, self.staff[Role.MANAGER], mistake.id)
        self.summary.movements += 1
        _backdate_movement(self.db, mistake.id, when)
        _backdate_movement(self.db, reversal.id, when + timedelta(minutes=5))
        self.db.commit()

    def sell_day(self, day: date, now: datetime) -> None:
        low, high = self.store.sales_per_day
        minutes = sorted(
            self.rng.randrange(0, (CLOSING_HOUR - OPENING_HOUR) * 60)
            for _ in range(self.rng.randint(low, high))
        )
        for minute in minutes:
            when = _at(day, OPENING_HOUR + minute // 60, minute % 60)
            if when > now:
                break
            items = [
                SaleItemIn(product_id=p.id, quantity=self.rng.randint(1, 3))
                for p in self.rng.sample(self.catalog, k=self.rng.randint(1, 4))
                if p.stock_quantity >= 3
            ]
            if not items:
                continue
            seller = self.staff[Role.CASHIER if self.rng.random() < 0.8 else Role.MANAGER]
            sale = sale_service.create_sale(self.db, seller, SaleCreate(items=items))
            _backdate_sale(self.db, sale.id, when)
            self.db.commit()
            self.summary.sales += 1
            self.summary.movements += len(items)

    def maybe_shrinkage(self, day: date, now: datetime) -> None:
        """Occasional one-unit shrinkage (merma) at closing time."""
        if self.rng.random() >= 0.25:
            return
        candidates = [p for p in self.catalog if p.stock_quantity > 2]
        if not candidates:
            return
        loss = self._move(self.rng.choice(candidates), -1, "adjustment", "Merma")
        _backdate_movement(self.db, loss.id, min(_at(day, CLOSING_HOUR, 30), now))
        self.db.commit()


def _seed_store(
    db: Session, store: DemoStore, rng: random.Random, today: date, days: int
) -> StoreSummary:
    seeder = _StoreSeeder(db, store, rng)
    seeder.create_staff()
    seeder.create_catalog(_at(today - timedelta(days=days), OPENING_HOUR - 1, 0))
    now = clock.utcnow()
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        if offset == days // 2:
            seeder.restock_and_correct(_at(day, OPENING_HOUR, 15))
        seeder.sell_day(day, now)
        seeder.maybe_shrinkage(day, now)
    return seeder.summary


def reset_demo(*, days: int = 14, seed: int | None = None) -> dict[str, StoreSummary]:
    """Wipes all tenants and reseeds the demo stores. Requires DEMO_MODE=true."""
    settings = get_settings()
    if not settings.demo_mode:
        raise DemoModeRequiredError(
            "reset-demo wipes every tenant; it only runs with DEMO_MODE=true"
        )
    rng = random.Random(seed)  # noqa: S311  (demo data, not security-related)
    today = clock.utcnow().astimezone(PARAGUAY).date()
    engine = create_engine(settings.migrations_database_url)
    try:
        with engine.begin() as conn:
            # TRUNCATE is not subject to RLS; the owner can wipe all tenants at once.
            conn.execute(text("TRUNCATE tenants RESTART IDENTITY CASCADE"))
        summaries: dict[str, StoreSummary] = {}
        for store in DEMO_STORES:
            with Session(engine, expire_on_commit=False) as db:
                summaries[store.slug] = _seed_store(db, store, rng, today, days)
        return summaries
    finally:
        engine.dispose()
