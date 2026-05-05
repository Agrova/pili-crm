"""Architectural test: all ORM relationships must stay within the same module.

Rule (ADR-001 v2, ADR-003): FK between modules are allowed at the DB level,
but SQLAlchemy relationship() must only connect models that belong to the same
module (identified by the shared table-name prefix, e.g. "catalog_", "orders_").
"""


# Side-effect imports: register all models with Base.metadata
import app.catalog.models  # noqa: F401
import app.communications.models  # noqa: F401
import app.finance.models  # noqa: F401
import app.orders.models  # noqa: F401
import app.pricing.models  # noqa: F401
import app.procurement.models  # noqa: F401
import app.warehouse.models  # noqa: F401
from app.shared.base_model import Base


def _module_prefix(cls: type) -> str:
    """Return the module prefix from a mapped class's __tablename__."""
    return cls.__tablename__.split("_")[0]


def _collect_cross_module_relationships() -> list[str]:
    """Return a list of human-readable descriptions for every cross-module relationship."""
    violations: list[str] = []
    for mapper in Base.registry.mappers:
        src_cls = mapper.class_
        src_prefix = _module_prefix(src_cls)
        for rel in mapper.relationships:
            dst_cls = rel.mapper.class_
            dst_prefix = _module_prefix(dst_cls)
            if src_prefix != dst_prefix:
                violations.append(
                    f"{src_cls.__name__}.{rel.key} → {dst_cls.__name__}"
                    f" ({src_prefix!r} → {dst_prefix!r})"
                )
    return violations


def test_no_cross_module_relationships() -> None:
    """No relationship() may cross module boundaries."""
    violations = _collect_cross_module_relationships()
    assert violations == [], (
        "Cross-module ORM relationships found (use FK without relationship instead):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_all_models_have_timestamps() -> None:
    """Every mapped table must have created_at and updated_at (TimestampMixin).

    Models marked with ImmutableMixin (__immutable__ = True) are exempt from
    updated_at but still require created_at.
    """
    missing: list[str] = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        col_names = {col.key for col in mapper.columns}
        if getattr(cls, "__immutable__", False):
            if "created_at" not in col_names:
                missing.append(f"{cls.__name__} (missing created_at)")
        elif "created_at" not in col_names or "updated_at" not in col_names:
            missing.append(cls.__name__)
    assert missing == [], (
        "Models missing created_at / updated_at (TimestampMixin not applied):\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


def test_api_module_has_no_models() -> None:
    """The api module must not own any tables (it has no models.py)."""
    table_names = {t for t in Base.metadata.tables}
    api_tables = [t for t in table_names if t.startswith("api_")]
    assert api_tables == [], (
        f"Unexpected api_* tables found: {api_tables}"
    )


def test_all_seven_module_prefixes_present() -> None:
    """All 7 table-owning modules must have at least one table registered."""
    expected_prefixes = {
        "catalog",
        "orders",
        "procurement",
        "warehouse",
        "pricing",
        "communications",
        "finance",
    }
    table_names = list(Base.metadata.tables)
    found_prefixes = {t.split("_")[0] for t in table_names}
    missing = expected_prefixes - found_prefixes
    assert missing == set(), (
        f"No tables found for module(s): {missing}"
    )
