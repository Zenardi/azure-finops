"""Test configuration: force mock mode and a placeholder DB URL before imports."""

from __future__ import annotations

import os

os.environ.setdefault("FINOPS_MOCK", "1")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://finops:finops@localhost:5432/finops")
