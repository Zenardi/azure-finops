"""M14.4 — cost forecasting. Tests written FIRST (TDD).

Three layers, each asserting one behaviour:

* **Pure logic** (no DB): the OLS linear trend, multiplicative weekday seasonal
  factors, the period-end projection (point + interval), and the rolling-origin
  backtest MAPE. Deterministic synthetic series — a known linear/seasonal series
  projects within tolerance, the interval brackets the point, a flat series
  forecasts flat, and thin history is labelled low-confidence (never hidden).
* **Repository** (``db`` fixture): the daily total/by-scope series reads and the
  idempotent ``upsert_cost_forecast`` (unique on scope+horizon+as_of), list
  filters, and the "latest forecast" lookup budgets consume.
* **Detection + budget wiring + API** (``db`` fixture): ``compute_cost_forecasts``
  persists a forecast per scope×horizon from seeded ``cost_snapshots``; a budget
  with a ``forecast`` basis consumes the stored forecast (forecasted-to-exceed);
  the read endpoint is RBAC-guarded.
"""

from __future__ import annotations

import datetime as dt

# Mid-May: month-end (May 31) and quarter-end (Q2 → Jun 30) differ, so the two
# horizons are distinguishable, and both period starts sit inside the fit window.
_ASOF = dt.date(2026, 5, 15)


def _daterange(start: dt.date, end: dt.date) -> list[dt.date]:
    """Inclusive list of dates from ``start`` to ``end``."""
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def _window(days: int, end: dt.date = _ASOF) -> list[dt.date]:
    """``days`` consecutive dates ending (inclusive) at ``end``."""
    return [end - dt.timedelta(days=days - 1 - i) for i in range(days)]


def _line(d: dt.date, start: dt.date, *, base: float = 100.0, slope: float = 2.0) -> float:
    return base + slope * (d - start).days


def _weekend_seasonal(d: dt.date) -> float:
    """A clean weekly-seasonal daily cost: weekends run 3× weekdays, no trend."""
    return 300.0 if d.weekday() >= 5 else 100.0


# --------------------------------------------------------------------------- #
# Pure logic — no database
# --------------------------------------------------------------------------- #
def test_linear_trend_recovers_line() -> None:
    from cloudwarden.analysis.forecast import linear_trend

    intercept, slope = linear_trend([(0.0, 100.0), (1.0, 102.0), (2.0, 104.0)])
    assert round(slope, 6) == 2.0
    assert round(intercept, 6) == 100.0


def test_linear_trend_degenerate_inputs() -> None:
    from cloudwarden.analysis.forecast import linear_trend

    assert linear_trend([]) == (0.0, 0.0)  # empty → no level, no slope
    assert linear_trend([(5.0, 50.0)]) == (50.0, 0.0)  # single point → flat at its level


def test_seasonal_factors_recover_weekly_pattern() -> None:
    from cloudwarden.analysis.forecast import linear_trend, seasonal_factors

    start = _ASOF - dt.timedelta(days=27)  # 4 clean weeks
    series = [(d, _weekend_seasonal(d)) for d in _daterange(start, _ASOF)]
    base = series[0][0].toordinal()
    intercept, slope = linear_trend([(float(d.toordinal() - base), c) for d, c in series])
    factors = seasonal_factors(series, intercept=intercept, slope=slope, base_ordinal=base)

    assert factors[5] > factors[0]  # Saturday runs above the weekday level
    assert factors[6] > factors[0]  # Sunday too
    # ~3× the weekday factor. Not exactly 3.0: a periodic window ending mid-week induces
    # a small OLS slope (weekends sit late in each week), nudging the multiplicative
    # factor slightly under the raw 300/100 ratio — the model is trend-aware, not naive.
    assert 2.5 <= factors[5] / factors[0] <= 3.5


def test_period_bounds_for_month_and_quarter() -> None:
    from cloudwarden.analysis.forecast import period_bounds_for

    assert period_bounds_for("month_end", _ASOF) == (dt.date(2026, 5, 1), dt.date(2026, 5, 31))
    assert period_bounds_for("quarter_end", _ASOF) == (dt.date(2026, 4, 1), dt.date(2026, 6, 30))


def test_linear_series_projected_within_tolerance() -> None:
    from cloudwarden.analysis.forecast import forecast_scope

    start = _ASOF - dt.timedelta(days=89)  # 90 days of a clean upward line
    series = [(d, _line(d, start)) for d in _window(90)]
    fc = forecast_scope(series, as_of=_ASOF, horizon="month_end", min_history=14)

    period_start, period_end = dt.date(2026, 5, 1), dt.date(2026, 5, 31)
    true_total = sum(_line(d, start) for d in _daterange(period_start, period_end))
    assert fc is not None
    assert fc.confidence == "high"
    assert abs(fc.point - true_total) / true_total < 0.05  # within 5% of the true line sum


def test_seasonal_series_projected_within_tolerance() -> None:
    from cloudwarden.analysis.forecast import forecast_scope

    series = [(d, _weekend_seasonal(d)) for d in _window(90)]
    fc = forecast_scope(series, as_of=_ASOF, horizon="month_end", min_history=14, seasonal=True)

    period_start, period_end = dt.date(2026, 5, 1), dt.date(2026, 5, 31)
    true_total = sum(_weekend_seasonal(d) for d in _daterange(period_start, period_end))
    assert fc is not None
    assert abs(fc.point - true_total) / true_total < 0.10  # weekday seasonality reconstructed


def test_interval_brackets_point() -> None:
    from cloudwarden.analysis.forecast import forecast_scope

    start = _ASOF - dt.timedelta(days=89)
    # A line plus bounded, deterministic noise so residual sigma > 0 (a real band).
    series = [(d, _line(d, start) + (i % 7 - 3) * 4.0) for i, d in enumerate(_window(90))]
    fc = forecast_scope(series, as_of=_ASOF, horizon="month_end", min_history=14)

    assert fc is not None
    assert fc.lower <= fc.point <= fc.upper  # the point sits inside its interval
    assert fc.upper > fc.lower  # noise makes the band non-degenerate
    assert fc.lower >= fc.actual_to_date  # can't project below what's already spent


def test_backtest_mape_recorded() -> None:
    from cloudwarden.analysis.forecast import forecast_scope

    start = _ASOF - dt.timedelta(days=89)
    series = [(d, _line(d, start)) for d in _window(90)]
    fc = forecast_scope(series, as_of=_ASOF, horizon="month_end", min_history=14)

    assert fc is not None
    assert fc.mape is not None  # every confident forecast carries a backtest MAPE
    assert 0.0 <= fc.mape < 5.0  # a clean line is predicted near-perfectly one step ahead


def test_flat_series_forecasts_flat() -> None:
    from cloudwarden.analysis.forecast import forecast_scope

    series = [(d, 100.0) for d in _window(90)]  # perfectly flat
    fc = forecast_scope(series, as_of=_ASOF, horizon="month_end", min_history=14)

    assert fc is not None
    assert abs(fc.point - 31 * 100.0) < 1.0  # 31 days of May at 100/day
    assert fc.mape is not None and fc.mape < 1.0  # flat is trivially predictable


def test_short_history_low_confidence_label() -> None:
    from cloudwarden.analysis.forecast import forecast_scope

    series = [(d, 100.0) for d in _window(5)]  # only 5 days — below the min-history gate
    fc = forecast_scope(series, as_of=_ASOF, horizon="month_end", min_history=14)

    assert fc is not None  # never hidden — a labelled estimate is still produced
    assert fc.confidence == "low"
    assert fc.model == "linear_low_confidence"
    assert fc.point > 0
    assert fc.mape is None  # too little history to backtest honestly


def test_month_and_quarter_horizons() -> None:
    from cloudwarden.analysis.forecast import forecast_scope

    series = [(d, 100.0) for d in _window(90)]
    month = forecast_scope(series, as_of=_ASOF, horizon="month_end", min_history=14)
    quarter = forecast_scope(series, as_of=_ASOF, horizon="quarter_end", min_history=14)

    assert month is not None and quarter is not None
    assert month.period_end == dt.date(2026, 5, 31)
    assert quarter.period_end == dt.date(2026, 6, 30)
    assert quarter.point >= month.point  # the quarter covers a strictly larger window


def test_forecast_scope_empty_series_returns_none() -> None:
    from cloudwarden.analysis.forecast import forecast_scope

    assert forecast_scope([], as_of=_ASOF, horizon="month_end") is None


def test_backtest_insufficient_history_returns_none() -> None:
    from cloudwarden.analysis.forecast import backtest_mape

    series = [(d, 100.0) for d in _window(5)]
    assert backtest_mape(series, folds=14, min_train=14, seasonal=False) is None


def test_default_scopes_and_horizons() -> None:
    from cloudwarden.analysis.forecast import DEFAULT_FORECAST_SCOPES, DEFAULT_HORIZONS

    assert set(DEFAULT_FORECAST_SCOPES) == {"total", "subscription", "service"}
    assert set(DEFAULT_HORIZONS) == {"month_end", "quarter_end"}


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
def _seed_series(s, *, on, days, daily=100.0, subscription_id="sub-fc", service="Compute"):
    """Seed ``cost_snapshots`` with ``days`` days (ending at ``on``) of ``daily`` spend."""
    from cloudwarden import models as m
    from cloudwarden.storage import repository as repo

    rows = [
        m.CostRow(
            usage_date=d,
            # A per-subscription resource id — cost_snapshots' PK includes resource_id,
            # so a shared id would make one subscription's rows overwrite the other's.
            resource_id=f"/{subscription_id}/vm",
            subscription_id=subscription_id,
            service_name=service,
            resource_type="vm",
            cost=daily,
        )
        for d in _window(days, end=on)
    ]
    repo.upsert_cost_snapshots(s, rows)


def _forecast_args(**over):
    args = dict(
        scope_type="subscription",
        scope_value="sub-fc",
        horizon="month_end",
        as_of=_ASOF,
        period_end=dt.date(2026, 5, 31),
        point=3100.0,
        lower=2900.0,
        upper=3300.0,
        actual_to_date=1500.0,
        projected=1600.0,
        mape=2.5,
        model="seasonal_trend",
        confidence="high",
    )
    args.update(over)
    return args


def test_cost_daily_total_sums_all_scopes(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    on = dt.date(2026, 5, 15)
    with session_scope() as s:
        _seed_series(s, on=on, days=3, daily=60.0, subscription_id="sub-a")
        _seed_series(s, on=on, days=3, daily=40.0, subscription_id="sub-b")
        rows = repo.cost_daily_total(s, start=on - dt.timedelta(days=10), end=on)

    totals = {r["usage_date"]: r["cost"] for r in rows}
    assert len(totals) == 3  # one row per day
    assert totals[on] == 100.0  # 60 + 40 summed across both subscriptions


def test_upsert_cost_forecast_idempotent_on_scope_horizon_asof(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        row1, inserted1 = repo.upsert_cost_forecast(s, **_forecast_args())
    with session_scope() as s:
        row2, inserted2 = repo.upsert_cost_forecast(s, **_forecast_args(point=4000.0))
    with session_scope() as s:
        listed = repo.list_cost_forecasts(s)

    assert inserted1 is True
    assert inserted2 is False  # same scope+horizon+as_of → update, not a duplicate
    assert row1["id"] == row2["id"]
    assert len(listed) == 1
    assert listed[0]["point"] == 4000.0  # reflects the latest recompute


def test_list_cost_forecasts_filters(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        repo.upsert_cost_forecast(
            s, **_forecast_args(scope_type="subscription", horizon="month_end")
        )
        repo.upsert_cost_forecast(
            s, **_forecast_args(scope_type="total", scope_value="", horizon="quarter_end")
        )

    with session_scope() as s:
        assert len(repo.list_cost_forecasts(s)) == 2
        assert len(repo.list_cost_forecasts(s, scope_type="total")) == 1
        assert len(repo.list_cost_forecasts(s, horizon="month_end")) == 1
        assert repo.list_cost_forecasts(s, scope_type="total")[0]["horizon"] == "quarter_end"


def test_get_cost_forecast_returns_latest(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    older, newer = dt.date(2026, 5, 10), dt.date(2026, 5, 15)
    with session_scope() as s:
        repo.upsert_cost_forecast(s, **_forecast_args(as_of=older, point=1000.0))
        repo.upsert_cost_forecast(s, **_forecast_args(as_of=newer, point=2000.0))

    with session_scope() as s:
        got = repo.get_cost_forecast(
            s, scope_type="subscription", scope_value="sub-fc", horizon="month_end", as_of=newer
        )
        # As of the older day, the newer forecast is excluded (no time travel).
        past = repo.get_cost_forecast(
            s, scope_type="subscription", scope_value="sub-fc", horizon="month_end", as_of=older
        )

    assert got["point"] == 2000.0  # most-recent forecast at/under the as_of
    assert past["point"] == 1000.0


def test_get_cost_forecast_missing_returns_none(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        assert (
            repo.get_cost_forecast(
                s, scope_type="subscription", scope_value="nope", horizon="month_end"
            )
            is None
        )


# --------------------------------------------------------------------------- #
# Detection orchestration
# --------------------------------------------------------------------------- #
def test_compute_cost_forecasts_persists_per_scope_and_horizon(db) -> None:
    from cloudwarden.analysis.forecast import compute_cost_forecasts
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    on = dt.date(2026, 5, 15)
    with session_scope() as s:
        _seed_series(s, on=on, days=60, daily=100.0)
        summary = compute_cost_forecasts(
            s,
            on=on,
            scopes=["total", "subscription", "service"],
            horizons=["month_end", "quarter_end"],
        )
    with session_scope() as s:
        forecasts = repo.list_cost_forecasts(s)

    # 3 scopes × 2 horizons, each with a single scope value → 6 forecasts.
    assert summary["forecasts_written"] == 6
    assert len(forecasts) == 6
    scopes = {f["scope_type"] for f in forecasts}
    assert scopes == {"total", "subscription", "service"}
    assert all(f["point"] > 0 for f in forecasts)
    assert all(f["mape"] is not None for f in forecasts)  # 60 days ≥ min-history → backtested


def test_compute_cost_forecasts_low_confidence_on_thin_history(db) -> None:
    from cloudwarden.analysis.forecast import compute_cost_forecasts
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    on = dt.date(2026, 5, 15)
    with session_scope() as s:
        _seed_series(s, on=on, days=4, daily=100.0)  # below the 14-day gate
        summary = compute_cost_forecasts(s, on=on, scopes=["total"], horizons=["month_end"])
    with session_scope() as s:
        forecasts = repo.list_cost_forecasts(s)

    assert summary["low_confidence"] == 1
    assert forecasts[0]["confidence"] == "low"  # labelled, not hidden


def test_forecast_for_budget_maps_scope_and_period(db) -> None:
    from cloudwarden.analysis.forecast import forecast_for_budget
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    on = dt.date(2026, 5, 15)
    with session_scope() as s:
        repo.upsert_cost_forecast(
            s, **_forecast_args(scope_value="sub-x", horizon="month_end", point=1500.0)
        )
        budget = repo.create_budget(
            s, name="b", amount=1000.0, scope_type="subscription", scope_value="sub-x"
        )
        point = forecast_for_budget(s, budget, on=on)
        # A quarterly budget maps to the quarter_end horizon (none stored → None).
        q_budget = repo.create_budget(
            s,
            name="bq",
            amount=1000.0,
            scope_type="subscription",
            scope_value="sub-x",
            period="quarterly",
        )
        q_point = forecast_for_budget(s, q_budget, on=on)

    assert point == 1500.0  # monthly budget consumes the month_end forecast
    assert q_point is None  # no quarter_end forecast stored for this scope


def test_budget_consumes_forecast(db) -> None:
    from cloudwarden.analysis.budgets import evaluate_budgets
    from cloudwarden.analysis.forecast import forecast_for_budget
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    on = dt.date(2026, 5, 15)
    with session_scope() as s:
        # A forecast that lands at 150% of the budget, with no actual spend seeded.
        repo.upsert_cost_forecast(
            s, **_forecast_args(scope_value="sub-x", horizon="month_end", point=1500.0)
        )
        repo.create_budget(
            s,
            name="fc-budget",
            amount=1000.0,
            scope_type="subscription",
            scope_value="sub-x",
            thresholds=[{"pct": 100.0, "basis": "forecast"}],
        )
        summary = evaluate_budgets(s, on=on, forecast_fn=forecast_for_budget)
    with session_scope() as s:
        budget = repo.list_budgets(s)[0]
        events = repo.budget_events_for_period(s, budget["id"], "2026-05")

    # Actual spend is 0 (nothing seeded), so only the forecast basis can cross 100%.
    assert summary["events_recorded"] == 1
    assert events[0]["basis"] == "forecast"
    assert events[0]["actual_pct"] == 150.0  # forecast is 150% of the limit


# --------------------------------------------------------------------------- #
# API — read + RBAC
# --------------------------------------------------------------------------- #
def test_forecast_endpoint_lists_and_filters(db) -> None:
    from fastapi.testclient import TestClient

    from cloudwarden.api.main import app
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        repo.upsert_cost_forecast(
            s, **_forecast_args(scope_type="subscription", horizon="month_end")
        )
        repo.upsert_cost_forecast(
            s, **_forecast_args(scope_type="total", scope_value="", horizon="quarter_end")
        )
    client = TestClient(app)  # RBAC off by default

    resp = client.get("/api/costs/forecast")
    assert resp.status_code == 200
    assert len(resp.json()["forecasts"]) == 2
    assert len(client.get("/api/costs/forecast?scope_type=total").json()["forecasts"]) == 1
    filtered = client.get("/api/costs/forecast?horizon=month_end").json()["forecasts"]
    assert len(filtered) == 1
    assert filtered[0]["scope_type"] == "subscription"


def test_forecast_read_requires_permission(db, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from cloudwarden.api.main import app
    from cloudwarden.authz import rbac
    from cloudwarden.config import get_settings
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    monkeypatch.setenv("RBAC_ENABLED", "1")
    get_settings.cache_clear()
    with session_scope() as s:
        rbac.seed_default_roles(s)
        repo.assign_role(s, principal="ed", role_name="editor")
    client = TestClient(app)

    assert client.get("/api/costs/forecast").status_code == 401
    assert client.get("/api/costs/forecast", headers={"X-Principal": "ed"}).status_code == 200
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Edge cases (guards, degeneracy, budget-scope mapping)
# --------------------------------------------------------------------------- #
def test_seasonal_factors_skips_nonpositive_trend() -> None:
    from cloudwarden.analysis.forecast import linear_trend, seasonal_factors

    start = dt.date(2026, 1, 5)
    # A steep decline: the OLS trend line goes negative for the later days, which then
    # form no ratio (a negative "expected" is meaningless) and are skipped.
    series = [(start + dt.timedelta(days=i), max(100.0 - 10.0 * i, 0.0)) for i in range(20)]
    base = series[0][0].toordinal()
    intercept, slope = linear_trend([(float(d.toordinal() - base), c) for d, c in series])
    factors = seasonal_factors(series, intercept=intercept, slope=slope, base_ordinal=base)

    assert isinstance(factors, dict)  # produced without dividing by a non-positive trend


def test_backtest_mape_skips_zero_actual_days() -> None:
    from cloudwarden.analysis.forecast import backtest_mape

    start = dt.date(2026, 1, 5)
    # A single zero-actual day inside the fold window is skipped (undefined %), but the
    # other folds still score → a MAPE is returned.
    series = [(start + dt.timedelta(days=i), 100.0) for i in range(18)]
    series += [(start + dt.timedelta(days=18), 0.0), (start + dt.timedelta(days=19), 120.0)]
    assert backtest_mape(series, folds=5, min_train=14, seasonal=False) is not None


def test_backtest_mape_none_when_all_folds_zero() -> None:
    from cloudwarden.analysis.forecast import backtest_mape

    start = dt.date(2026, 1, 5)
    # Every held-out day is zero → no defined error → None (never a fabricated 0%).
    series = [(start + dt.timedelta(days=i), 100.0) for i in range(14)]
    series += [(start + dt.timedelta(days=14 + i), 0.0) for i in range(5)]
    assert backtest_mape(series, folds=5, min_train=14, seasonal=False) is None


def test_forecast_for_budget_without_as_of_returns_none() -> None:
    from cloudwarden.analysis.forecast import forecast_for_budget

    # No ``on`` → no reference day to look a forecast up against.
    assert forecast_for_budget(None, {"period": "monthly", "scope_value": "x"}, on=None) is None


def test_forecast_for_budget_total_scope(db) -> None:
    from cloudwarden.analysis.forecast import forecast_for_budget
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    on = dt.date(2026, 5, 15)
    with session_scope() as s:
        repo.upsert_cost_forecast(
            s,
            **_forecast_args(scope_type="total", scope_value="", horizon="month_end", point=999.0),
        )
        # A tenant-wide budget (no scope_value) consumes the ``total`` forecast.
        point = forecast_for_budget(
            s, {"period": "monthly", "scope_type": "subscription", "scope_value": None}, on=on
        )
    assert point == 999.0


def test_forecast_for_budget_unsupported_scope_returns_none(db) -> None:
    from cloudwarden.analysis.forecast import forecast_for_budget
    from cloudwarden.storage.db import session_scope

    on = dt.date(2026, 5, 15)
    with session_scope() as s:
        # account-group budgets have no forecast dimension yet (M14.5) → no metric.
        point = forecast_for_budget(
            s, {"period": "monthly", "scope_type": "account_group", "scope_value": "grp"}, on=on
        )
    assert point is None
