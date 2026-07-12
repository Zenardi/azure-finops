# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/) and [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Phase 0 — Scaffold:** project layout, `pyproject.toml` (Ruff + pytest),
  `Makefile`, `.env.example`, Docker Compose (TimescaleDB + backend + Grafana,
  frontend behind a profile), Chainguard nonroot backend image.
- **Config / auth / resilience:** `config.py` (pydantic-settings), `auth.py`
  (read + write `DefaultAzureCredential`, ARM token), `resilience.py`
  (retry/backoff honoring `Retry-After`/`x-ms-ratelimit-*` + last-good cache).
- **Phase 1 — MVP cost pipeline:** Resource Graph inventory + Cost Management
  collectors (mock-backed via fixtures), storage layer (SQLAlchemy models +
  repository + Timescale/views bootstrap), orchestrator, Typer CLI
  (`initdb | run | run --mock | api | scheduler`), Grafana provisioning + Cost
  dashboard.
- **Phase 2 — Metrics + rules engine:** Azure Monitor metrics + Log Analytics
  memory + Advisor collectors (mock-backed), utilization rollups (avg/p95/max +
  data_completeness), FinOps rules (shutdown / downsize / idle-orphan) with
  Retail-Prices-based savings and Advisor confidence boosting, prioritized
  recommendations persisted, and a **Recommendations & Savings** Grafana dashboard.
