"""Load recorded API fixtures for FINOPS_MOCK=1 (offline development / tests)."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any


def load_fixture(name: str) -> Any:
    ref = resources.files("azure_finops.fixtures").joinpath(f"{name}.json")
    with ref.open("r", encoding="utf-8") as fh:
        return json.load(fh)
