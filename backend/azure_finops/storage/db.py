"""Engine/session management and idempotent schema bootstrap.

`init_db()` creates the tables, then best-effort promotes the fact tables to
TimescaleDB hypertables and (re)creates the Grafana-facing SQL views. Every
optional/Timescale-specific step runs in its own transaction so a missing
extension degrades gracefully to plain Postgres instead of aborting the rest.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from .schema import Base

logger = logging.getLogger("azure_finops.storage")

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory() -> sessionmaker:
    get_engine()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


HYPERTABLES = [("cost_snapshots", "usage_date"), ("utilization_samples", "ts")]

_VIEWS_SQL = """
CREATE OR REPLACE VIEW v_cost_by_resource AS
SELECT resource_id, resource_type, location, resource_group,
       SUM(cost) AS cost, currency
FROM cost_snapshots
WHERE cost_type = 'Amortized' AND usage_date >= (CURRENT_DATE - INTERVAL '30 days')
GROUP BY resource_id, resource_type, location, resource_group, currency;

CREATE OR REPLACE VIEW v_cost_by_type AS
SELECT resource_type, SUM(cost) AS cost, currency
FROM cost_snapshots
WHERE cost_type = 'Amortized' AND usage_date >= (CURRENT_DATE - INTERVAL '30 days')
GROUP BY resource_type, currency;

CREATE OR REPLACE VIEW v_cost_by_region AS
SELECT location, SUM(cost) AS cost, currency
FROM cost_snapshots
WHERE cost_type = 'Amortized' AND usage_date >= (CURRENT_DATE - INTERVAL '30 days')
GROUP BY location, currency;

CREATE OR REPLACE VIEW v_latest_recommendations AS
SELECT * FROM recommendations
WHERE run_id = (
    SELECT run_id FROM runs WHERE status = 'succeeded'
    ORDER BY started_at DESC LIMIT 1
);

CREATE OR REPLACE VIEW v_savings_by_category AS
SELECT category, SUM(est_monthly_savings) AS est_monthly_savings, currency
FROM v_latest_recommendations
GROUP BY category, currency;
"""


def _split_sql(block: str) -> list[str]:
    return [stmt.strip() for stmt in block.split(";") if stmt.strip()]


def _try_exec(engine: Engine, sql: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    except Exception as exc:  # noqa: BLE001 - optional DDL, degrade gracefully
        logger.info("optional DDL skipped (%s...): %s", sql[:48].replace("\n", " "), exc)


def init_db() -> None:
    engine = get_engine()
    _try_exec(engine, "CREATE EXTENSION IF NOT EXISTS timescaledb")
    Base.metadata.create_all(engine)
    for table, column in HYPERTABLES:
        _try_exec(
            engine,
            f"SELECT create_hypertable('{table}', '{column}', "
            "if_not_exists => TRUE, migrate_data => TRUE)",
        )
    with engine.begin() as conn:
        for stmt in _split_sql(_VIEWS_SQL):
            conn.execute(text(stmt))
    logger.info("database schema ready")
