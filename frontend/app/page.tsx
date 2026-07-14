"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { apiGet, API_BASE, GRAFANA_BASE, money } from "./lib/api";
import type { AISummary } from "./lib/api";

/** Latest governance/FinOps run — the subset the Overview surfaces (see backend `runs`). */
interface RunLatest {
  run_id?: string | null;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  mock?: boolean | null;
}

/** `/api/costs/summary` — total plus the by-dimension breakdowns it returns inline. */
interface CostSummary {
  total?: number;
  currency?: string;
  by_region?: unknown[];
  by_type?: unknown[];
}

/**
 * A fetch that is still in flight, succeeded with data, or failed. Modelling the
 * failure explicitly is the whole point: the previous `.catch(() => fallback)`
 * pattern made every error look like real (empty) data, so a down backend showed
 * a fabricated $0.00 and the error banner was dead code.
 */
type Loadable<T> =
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "error"; message: string };

const LOADING = { state: "loading" } as const;

async function load<T>(path: string): Promise<Loadable<T>> {
  try {
    return { state: "ok", data: await apiGet<T>(path) };
  } catch (e) {
    return { state: "error", message: e instanceof Error ? e.message : String(e) };
  }
}

/** Human relative time from an ISO stamp ("2 hours ago"), i18n-safe. Null if unparseable. */
function timeAgo(iso?: string | null): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const secs = Math.round((then - Date.now()) / 1000); // negative = in the past
  const abs = Math.abs(secs);
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31536000],
    ["month", 2592000],
    ["week", 604800],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
    ["second", 1],
  ];
  for (const [unit, s] of units) {
    if (abs >= s || unit === "second") return rtf.format(Math.round(secs / s), unit);
  }
  return null;
}

/** Elapsed wall-clock between two ISO stamps ("3m 12s"). Null unless both are valid. */
function duration(startIso?: string | null, endIso?: string | null): string | null {
  if (!startIso || !endIso) return null;
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m}m ${s % 60}s` : `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Run status → pill class, matching the /runs and /executions convention exactly. */
function runBadgeClass(status?: string | null): string {
  if (status === "succeeded") return "badge approved";
  if (status === "failed") return "badge rejected";
  return "badge";
}

/**
 * Renders a card's value across all three states. `renderOk` only runs once the
 * fetch has genuinely succeeded, so callers never have to defend against a
 * fabricated fallback — loading shows a skeleton, failure shows an em-dash.
 */
function CardValue<T>({
  loadable,
  renderOk,
}: {
  loadable: Loadable<T>;
  renderOk: (data: T) => ReactNode;
}) {
  if (loadable.state === "loading") {
    return <div className="skeleton skeleton-value" aria-hidden />;
  }
  if (loadable.state === "error") {
    return (
      <>
        <div className="value unavailable">—</div>
        <div className="card-note">Unavailable</div>
      </>
    );
  }
  return <>{renderOk(loadable.data)}</>;
}

export default function Overview() {
  const [summary, setSummary] = useState<Loadable<AISummary | null>>(LOADING);
  const [cost, setCost] = useState<Loadable<CostSummary>>(LOADING);
  const [run, setRun] = useState<Loadable<RunLatest | null>>(LOADING);

  const refresh = useCallback(() => {
    setSummary(LOADING);
    setCost(LOADING);
    setRun(LOADING);
    // Each request settles independently: a partial outage still shows what loaded.
    load<AISummary | null>("/api/summary").then(setSummary);
    load<CostSummary>("/api/costs/summary").then(setCost);
    load<RunLatest | null>("/api/runs/latest").then(setRun);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const states = [summary, cost, run];
  const failed = states.filter((s) => s.state === "error").length;
  const loading = states.some((s) => s.state === "loading");
  const allFailed = failed === states.length;

  // Real denominator for the savings ratio: only when the cost total actually loaded.
  const spendForRatio =
    cost.state === "ok" && typeof cost.data.total === "number" && cost.data.total > 0
      ? cost.data.total
      : null;

  const runId = run.state === "ok" && run.data?.run_id ? run.data.run_id : undefined;

  return (
    <>
      <h1>Overview</h1>
      <p className="sub">Cost, savings, and the latest governance run across your clouds.</p>

      {failed > 0 && (
        <div className="err banner" role="alert">
          <div>
            <strong>
              {allFailed
                ? `Can’t reach the API at ${API_BASE}.`
                : "Some data couldn’t be loaded."}
            </strong>
            <div className="err-detail">
              {allFailed
                ? "Is the backend running?"
                : `${failed} of ${states.length} requests failed — showing what loaded.`}
            </div>
          </div>
          <button type="button" className="retry" onClick={refresh}>
            Retry
          </button>
        </div>
      )}

      <div className="cards kpis" aria-live="polite" aria-busy={loading}>
        <Link className="card kpi" href="/costs">
          <div
            className="label"
            title="Amortized: upfront reservation & commitment costs are spread evenly across the 30 days, not charged in a lump on the purchase date."
          >
            Cost (30d, amortized)
          </div>
          <CardValue
            loadable={cost}
            renderOk={(d) => {
              if (typeof d.total !== "number") {
                return (
                  <>
                    <div className="value unavailable">—</div>
                    <div className="card-note">No cost data</div>
                  </>
                );
              }
              const regions = Array.isArray(d.by_region) ? d.by_region.length : 0;
              const types = Array.isArray(d.by_type) ? d.by_type.length : 0;
              const scope = [
                regions && `${regions} region${regions === 1 ? "" : "s"}`,
                types && `${types} type${types === 1 ? "" : "s"}`,
              ]
                .filter(Boolean)
                .join(" · ");
              return (
                <>
                  <div className="value">{money(d.total, d.currency)}</div>
                  {scope && <div className="card-note">{scope}</div>}
                </>
              );
            }}
          />
          <span className="card-link">
            Cost breakdown <span aria-hidden="true">→</span>
          </span>
        </Link>

        <Link className="card kpi" href="/recommendations">
          <div className="label">Potential monthly savings</div>
          <CardValue
            loadable={summary}
            renderOk={(d) =>
              d && typeof d.total_potential_savings === "number" ? (
                <>
                  <div className="value green">{money(d.total_potential_savings, d.currency)}</div>
                  {spendForRatio != null && (
                    <div className="card-note">
                      ≈{Math.round((d.total_potential_savings / spendForRatio) * 100)}% of 30-day
                      spend
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="value unavailable">—</div>
                  <div className="card-note">No summary yet</div>
                </>
              )
            }
          />
          <span className="card-link">
            Recommendations <span aria-hidden="true">→</span>
          </span>
        </Link>

        <Link className="card kpi" href="/runs" title={runId ? `Run ${runId}` : undefined}>
          <div className="label">Last run</div>
          <CardValue
            loadable={run}
            renderOk={(d) => {
              if (!d) {
                return (
                  <div className="run-head">
                    <span className="badge">No runs yet</span>
                  </div>
                );
              }
              const ago = timeAgo(d.started_at);
              const dur = duration(d.started_at, d.finished_at);
              return (
                <>
                  <div className="run-head">
                    <span className={runBadgeClass(d.status)}>{d.status ?? "unknown"}</span>
                    {d.mock && <span className="badge">mock</span>}
                  </div>
                  <div className="card-note">
                    {ago ?? "Time unknown"}
                    {dur ? ` · ran ${dur}` : ""}
                  </div>
                </>
              );
            }}
          />
          <span className="card-link">
            Run history <span aria-hidden="true">→</span>
          </span>
        </Link>
      </div>

      <h2>AI executive summary</h2>
      {summary.state === "loading" ? (
        <div className="summary" aria-busy="true">
          <div className="skeleton skeleton-line" aria-hidden />
          <div className="skeleton skeleton-line" aria-hidden />
          <div className="skeleton skeleton-line short" aria-hidden />
        </div>
      ) : summary.state === "error" ? (
        <div className="summary summary-error">Couldn’t load the summary — {summary.message}</div>
      ) : summary.data?.executive_summary ? (
        <div className="summary">
          {summary.data.executive_summary}
          {summary.data.provider && (
            <div className="muted summary-meta">
              via {summary.data.provider}/{summary.data.model}
            </div>
          )}
        </div>
      ) : (
        <div className="summary">
          No summary yet. Trigger a run from the <Link href="/runs">Runs</Link> page to generate one.
        </div>
      )}

      <h2>Dashboards</h2>
      <div className="links">
        <a
          href={`${GRAFANA_BASE}/d/finops-cost`}
          target="_blank"
          rel="noreferrer"
          aria-label="Grafana — Cost Overview (opens in new tab)"
        >
          Grafana — Cost Overview ↗
        </a>
        <a
          href={`${GRAFANA_BASE}/d/finops-recs`}
          target="_blank"
          rel="noreferrer"
          aria-label="Grafana — Recommendations (opens in new tab)"
        >
          Grafana — Recommendations ↗
        </a>
        <Link href="/recommendations">Review recommendations →</Link>
      </div>
    </>
  );
}
