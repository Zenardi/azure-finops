"""Project spend to period-end over the ``cost_snapshots`` time-series (M14.4).

FinOps reporting answers "what did we spend?"; leadership keeps asking "where will
we *land* this month?". This forecaster projects spend to **month-end** and
**quarter-end** per scope (total / subscription / service), with a **prediction
interval** and a **backtested accuracy (MAPE)** so the number carries its own
credibility, and it feeds the forecasted-to-exceed budget rule (:mod:`.budgets`).

The model is deliberately **transparent and explainable** — a seasonal-naive +
linear-trend decomposition, not a black box:

* **Trend.** An ordinary least-squares line over the trailing window's daily totals
  (indexed by calendar day, so gaps don't distort the slope).
* **Weekday seasonality.** Multiplicative per-weekday factors (that weekday's median
  ratio to the trend line), so a heavy-weekend / light-weekend pattern is projected,
  not averaged away. Each remaining day is ``max(trend, 0) × weekday_factor``.
* **Period projection.** ``point = actual-to-date (this period) + Σ projected
  remaining days``. The interval widens with the residual spread and the number of
  days still to project (``z · σ · √remaining``), floored at what's already spent.
* **Honest accuracy.** A rolling-origin one-step-ahead backtest over the tail of the
  window records a MAPE next to every confident forecast.
* **Graceful degradation.** Below ``min_history`` days the forecaster still emits a
  **clearly-labelled low-confidence** linear estimate (wider band, no seasonality,
  no backtest) — never a silently fabricated number, and never nothing at all.

The pure helpers (:func:`linear_trend`, :func:`seasonal_factors`,
:func:`forecast_scope`, :func:`backtest_mape`) are unit-tested without a database on
deterministic synthetic series; :func:`compute_cost_forecasts` injects its data
source so the read/project/persist flow is exercised offline.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..storage import repository as repo
from .budgets import period_bounds

logger = logging.getLogger("cloudwarden.forecast")

# The grains a forecast is produced at. ``total`` is the whole tenant; the others map
# to a ``cost_snapshots`` column (validated against a whitelist in the repository).
# ``tag``/``team`` have no cost dimension until M14.5 and are intentionally omitted.
DEFAULT_FORECAST_SCOPES = ("total", "subscription", "service")
DEFAULT_HORIZONS = ("month_end", "quarter_end")

# Model tuning (overridable via config / call args).
DEFAULT_WINDOW_DAYS = 90  # trailing window the trend + seasonality are fit on
DEFAULT_MIN_HISTORY = 14  # below this many days → a labelled low-confidence estimate
DEFAULT_BACKTEST_DAYS = 14  # rolling one-step-ahead folds feeding the MAPE
DEFAULT_CONFIDENCE_PCT = 80.0  # prediction-interval confidence level

# Confidence level → standard-normal z. A small transparent table beats importing a
# stats package for the inverse CDF; unknown levels fall back to the 80% z.
_Z_BY_PCT = {80: 1.2816, 85: 1.4395, 90: 1.6449, 95: 1.9600, 99: 2.5758}
# Thin-history bands are widened by this factor (honest about the extra uncertainty).
_LOW_CONFIDENCE_WIDENING = 2.0


@dataclass(frozen=True)
class Forecast:
    """One scope's projection to a period end, with its interval and credibility."""

    horizon: str  # month_end | quarter_end
    as_of: dt.date  # the day the forecast was computed
    period_end: dt.date  # the day being projected to
    point: float  # projected total spend for the period
    lower: float  # lower bound of the prediction interval
    upper: float  # upper bound of the prediction interval
    actual_to_date: float  # spend already booked this period
    projected: float  # the remaining-days portion (point - actual_to_date)
    remaining_days: int  # days still to project
    mape: float | None  # rolling-backtest mean abs. pct error (None on thin history)
    model: str  # seasonal_trend | linear | linear_low_confidence
    confidence: str  # high | low


def linear_trend(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Ordinary least-squares ``(intercept, slope)`` for ``y ≈ intercept + slope·x``.

    Degenerate inputs collapse gracefully: an empty series is ``(0, 0)`` and a single
    point (or a series with no spread in ``x``) is flat at its mean level.
    """
    n = len(points)
    if n == 0:
        return 0.0, 0.0
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    var_x = sum((x - mean_x) ** 2 for x, _ in points)
    if n < 2 or var_x == 0:
        return mean_y, 0.0
    cov = sum((x - mean_x) * (y - mean_y) for x, y in points)
    slope = cov / var_x
    intercept = mean_y - slope * mean_x
    return intercept, slope


def seasonal_factors(
    series: Iterable[tuple[dt.date, float]],
    *,
    intercept: float,
    slope: float,
    base_ordinal: int,
) -> dict[int, float]:
    """Multiplicative per-weekday factors: that weekday's median ratio to the trend.

    A factor of ``1.0`` means the weekday runs at the trend level; ``3.0`` means it
    runs 3× (a heavy weekend). Days where the trend prediction is ≤ 0 form no ratio
    (skipped); a weekday absent from the window defaults to ``1.0`` at lookup.
    """
    ratios: dict[int, list[float]] = defaultdict(list)
    for day, cost in series:
        pred = intercept + slope * (day.toordinal() - base_ordinal)
        if pred <= 0:
            continue
        ratios[day.weekday()].append(float(cost) / pred)
    return {weekday: statistics.median(values) for weekday, values in ratios.items()}


def period_bounds_for(horizon: str, on: dt.date) -> tuple[dt.date, dt.date]:
    """The inclusive ``(start, end)`` of the calendar period a ``horizon`` targets."""
    period = "quarterly" if horizon == "quarter_end" else "monthly"
    return period_bounds(period, on)


def _predict_day(
    day: dt.date, *, intercept: float, slope: float, base_ordinal: int, factors: dict[int, float]
) -> float:
    """Projected spend for one calendar ``day`` = ``max(trend, 0) × weekday factor``."""
    trend = intercept + slope * (day.toordinal() - base_ordinal)
    factor = factors.get(day.weekday(), 1.0)
    return max(trend, 0.0) * factor


def _z_for(confidence_pct: float) -> float:
    return _Z_BY_PCT.get(int(round(confidence_pct)), _Z_BY_PCT[80])


def backtest_mape(
    series: list[tuple[dt.date, float]],
    *,
    folds: int,
    min_train: int,
    seasonal: bool,
) -> float | None:
    """Rolling-origin one-step-ahead MAPE (percent) over the tail of ``series``.

    For each of the last ``folds`` days (that still leave ``min_train`` days to train
    on), fit the trend + seasonality on everything before it and predict that day;
    the mean absolute percentage error is the model's honest accuracy. Returns
    ``None`` when the history is too short to hold out even one fold, or when every
    held-out day has zero actual (an undefined percentage).
    """
    data = sorted(series)
    n = len(data)
    if n < min_train + 1:
        return None
    errors: list[float] = []
    for i in range(max(min_train, n - folds), n):
        train = data[:i]
        target_day, actual = data[i]
        if actual == 0:
            continue  # percentage error is undefined against a zero actual
        base = train[0][0].toordinal()
        intercept, slope = linear_trend([(float(d.toordinal() - base), c) for d, c in train])
        factors = (
            seasonal_factors(train, intercept=intercept, slope=slope, base_ordinal=base)
            if seasonal
            else {}
        )
        pred = _predict_day(
            target_day, intercept=intercept, slope=slope, base_ordinal=base, factors=factors
        )
        errors.append(abs(actual - pred) / abs(actual))
    if not errors:
        return None
    return round(sum(errors) / len(errors) * 100.0, 4)


def forecast_scope(
    series: Iterable[tuple[dt.date, float]],
    *,
    as_of: dt.date,
    horizon: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_history: int = DEFAULT_MIN_HISTORY,
    backtest_days: int = DEFAULT_BACKTEST_DAYS,
    confidence_pct: float = DEFAULT_CONFIDENCE_PCT,
    seasonal: bool = True,
) -> Forecast | None:
    """Project one scope's spend to the ``horizon`` period end as of ``as_of``.

    Fits an OLS trend (and, when confident, weekday seasonality) on the trailing
    ``window_days``, sums the already-booked spend for the current period with the
    projected remaining days, and attaches a prediction interval and backtest MAPE.
    Returns ``None`` only when there is **no** data to fit (never a fabricated
    number). With fewer than ``min_history`` days the result is labelled
    ``confidence='low'`` (``model='linear_low_confidence'``): a wider band, no
    seasonality, and no backtest — degraded, but surfaced.
    """
    by_date: dict[dt.date, float] = defaultdict(float)
    for day, cost in series:
        by_date[day] += float(cost)
    win_start = as_of - dt.timedelta(days=window_days)
    window = sorted((d, c) for d, c in by_date.items() if win_start <= d <= as_of)
    if not window:
        return None

    confident = len(window) >= min_history
    use_seasonal = seasonal and confident
    period_start, period_end = period_bounds_for(horizon, as_of)

    base = window[0][0].toordinal()
    intercept, slope = linear_trend([(float(d.toordinal() - base), c) for d, c in window])
    factors = (
        seasonal_factors(window, intercept=intercept, slope=slope, base_ordinal=base)
        if use_seasonal
        else {}
    )

    def predict(day: dt.date) -> float:
        return _predict_day(
            day, intercept=intercept, slope=slope, base_ordinal=base, factors=factors
        )

    # Already-booked spend for the *current* period (from the full series, not just
    # the fit window — the period start may pre-date the window on a short horizon).
    actual_to_date = sum(c for d, c in by_date.items() if period_start <= d <= as_of)

    remaining = list(_days_between(as_of + dt.timedelta(days=1), period_end))
    projected = sum(predict(d) for d in remaining)
    point = actual_to_date + projected

    residuals = [c - predict(d) for d, c in window]
    sigma = statistics.pstdev(residuals) if len(residuals) >= 2 else 0.0
    z = _z_for(confidence_pct) * (1.0 if confident else _LOW_CONFIDENCE_WIDENING)
    half_width = z * sigma * math.sqrt(len(remaining))
    lower = max(point - half_width, actual_to_date)  # can't land below what's spent
    upper = point + half_width

    mape = (
        backtest_mape(window, folds=backtest_days, min_train=min_history, seasonal=use_seasonal)
        if confident
        else None
    )
    if use_seasonal:
        model = "seasonal_trend"
    elif confident:
        model = "linear"
    else:
        model = "linear_low_confidence"

    return Forecast(
        horizon=horizon,
        as_of=as_of,
        period_end=period_end,
        point=round(point, 6),
        lower=round(lower, 6),
        upper=round(upper, 6),
        actual_to_date=round(actual_to_date, 6),
        projected=round(projected, 6),
        remaining_days=len(remaining),
        mape=mape,
        model=model,
        confidence="high" if confident else "low",
    )


def _days_between(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    """Yield each date from ``start`` to ``end`` inclusive (empty if ``start > end``)."""
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)


def _resolve(value: Any, settings_value: Any) -> Any:
    return settings_value if value is None else value


def _read_series(
    session: Session, scope_type: str, start: dt.date, end: dt.date
) -> tuple[dict[str, list[tuple[dt.date, float]]], dict[str, str]]:
    """Per-scope-value daily series (and currency) over ``[start, end]`` for a grain.

    ``total`` collapses the whole tenant into a single ``""`` series; the other grains
    group by their whitelisted ``cost_snapshots`` column."""
    series_by_value: dict[str, list[tuple[dt.date, float]]] = defaultdict(list)
    currency: dict[str, str] = {}
    if scope_type == "total":
        for row in repo.cost_daily_total(session, start=start, end=end):
            series_by_value[""].append((row["usage_date"], float(row["cost"])))
            currency.setdefault("", row.get("currency") or "USD")
    else:
        for row in repo.cost_daily_by_scope(session, scope_type=scope_type, start=start, end=end):
            series_by_value[row["scope_value"]].append((row["usage_date"], float(row["cost"])))
            currency.setdefault(row["scope_value"], row.get("currency") or "USD")
    return series_by_value, currency


def compute_cost_forecasts(
    session: Session,
    *,
    on: dt.date,
    run_id: str | None = None,
    scopes: Iterable[str] | None = None,
    horizons: Iterable[str] | None = None,
    window_days: int | None = None,
    min_history: int | None = None,
    backtest_days: int | None = None,
    confidence_pct: float | None = None,
    seasonal: bool | None = None,
    settings: Any | None = None,
) -> dict[str, int]:
    """Compute and persist a forecast per scope value × horizon as of ``on``.

    For each grain read the trailing-window series, forecast every scope value to
    each horizon, and upsert the result idempotently (one row per
    scope+horizon+as_of; a re-run of the same day refreshes it). Tuning falls back to
    ``settings`` when an argument is ``None``. Returns counts: ``scopes_scanned``,
    ``forecasts_written``, ``low_confidence``.
    """
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    scopes = tuple(scopes) if scopes is not None else DEFAULT_FORECAST_SCOPES
    horizons = tuple(horizons) if horizons is not None else DEFAULT_HORIZONS
    window_days = int(_resolve(window_days, settings.forecast_window_days))
    min_history = int(_resolve(min_history, settings.forecast_min_history_days))
    backtest_days = int(_resolve(backtest_days, settings.forecast_backtest_days))
    confidence_pct = float(_resolve(confidence_pct, settings.forecast_confidence_pct))
    seasonal = bool(_resolve(seasonal, getattr(settings, "forecast_seasonality", True)))

    start = on - dt.timedelta(days=window_days)
    written = 0
    low_confidence = 0
    for scope_type in scopes:
        series_by_value, currency = _read_series(session, scope_type, start, on)
        for scope_value, series in series_by_value.items():
            for horizon in horizons:
                fc = forecast_scope(
                    series,
                    as_of=on,
                    horizon=horizon,
                    window_days=window_days,
                    min_history=min_history,
                    backtest_days=backtest_days,
                    confidence_pct=confidence_pct,
                    seasonal=seasonal,
                )
                if fc is None:
                    continue
                repo.upsert_cost_forecast(
                    session,
                    scope_type=scope_type,
                    scope_value=scope_value,
                    horizon=fc.horizon,
                    as_of=on,
                    period_end=fc.period_end,
                    point=fc.point,
                    lower=fc.lower,
                    upper=fc.upper,
                    actual_to_date=fc.actual_to_date,
                    projected=fc.projected,
                    mape=fc.mape,
                    model=fc.model,
                    confidence=fc.confidence,
                    currency=currency.get(scope_value, "USD"),
                    run_id=run_id,
                )
                written += 1
                if fc.confidence == "low":
                    low_confidence += 1

    return {
        "scopes_scanned": len(scopes),
        "forecasts_written": written,
        "low_confidence": low_confidence,
    }


# Budget scope → forecast grain. ``account`` is an Azure subscription alias; groups,
# tags and teams have no forecast dimension yet (M14.5) and yield no forecast metric.
_BUDGET_SCOPE_TO_FORECAST = {"subscription": "subscription", "account": "subscription"}


def forecast_for_budget(
    session: Session,
    budget: dict[str, Any],
    start: dt.date | None = None,
    on: dt.date | None = None,
    spend: float | None = None,
) -> float | None:
    """The stored forecast point a budget's forecasted-to-exceed rule consumes.

    Maps the budget's period to a horizon (monthly→``month_end``,
    quarterly→``quarter_end``) and its scope to a forecast grain (a tenant-wide
    budget → ``total``), then returns the most recent forecast at/under ``on``.
    Returns ``None`` — so the forecast rule is simply skipped — when the scope has no
    forecast dimension yet or none has been computed. Signature matches the
    ``forecast_fn`` seam in :func:`.budgets.evaluate_budgets` (positional
    ``session, budget, start, on, spend``)."""
    if on is None:
        return None
    horizon = "quarter_end" if budget.get("period") == "quarterly" else "month_end"
    scope_value = budget.get("scope_value")
    if not scope_value:
        forecast_scope_type, forecast_scope_value = "total", ""
    else:
        forecast_scope_type = _BUDGET_SCOPE_TO_FORECAST.get(
            (budget.get("scope_type") or "subscription").lower()
        )
        if forecast_scope_type is None:
            return None
        forecast_scope_value = scope_value
    record = repo.get_cost_forecast(
        session,
        scope_type=forecast_scope_type,
        scope_value=forecast_scope_value,
        horizon=horizon,
        as_of=on,
    )
    return float(record["point"]) if record else None
