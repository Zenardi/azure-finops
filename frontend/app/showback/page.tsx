"use client";

import { useEffect, useState } from "react";
import { getShowback, money, showbackExportUrl, type ShowbackReport } from "../lib/api";

const UNALLOCATED = "unallocated";

export default function Showback() {
  const [tagKey, setTagKey] = useState("owner");
  const [report, setReport] = useState<ShowbackReport | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getShowback({ key: tagKey, days: 30 })
      .then((r) => {
        setReport(r);
        setErr("");
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [tagKey]);

  const currency = report?.currency ?? "USD";
  const allocatedPct = report && report.total > 0 ? (report.allocated / report.total) * 100 : 0;

  return (
    <>
      <h1>Showback / chargeback</h1>
      <p className="sub">
        Spend allocated by the <code>{tagKey}</code> tag over the last 30 days. Untagged spend is
        surfaced in an explicit <strong>unallocated</strong> bucket — the thing to drive down.
      </p>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 12,
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <label style={{ fontSize: 13 }}>
          Group by tag:{" "}
          <input
            value={tagKey}
            onChange={(e) => setTagKey(e.target.value.trim() || "owner")}
            style={{ padding: "3px 8px", borderRadius: 6, border: "1px solid var(--border,#d1d5db)" }}
            aria-label="Tag key to allocate spend by"
          />
        </label>
        <a className="btn" href={showbackExportUrl(tagKey, 30, "csv")}>
          Export CSV
        </a>
        <a className="btn" href={showbackExportUrl(tagKey, 30, "json")}>
          Export JSON
        </a>
      </div>

      {err && <div className="err">{err}</div>}

      {report && (
        <section className="panel" aria-labelledby="alloc-h" style={{ marginBottom: 16 }}>
          <h2 className="panel-title" id="alloc-h">
            Allocation
          </h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 24, marginBottom: 12 }}>
            <Stat label="Total" value={money(report.total, currency)} />
            <Stat
              label="Allocated"
              value={money(report.allocated, currency)}
              sub={`${allocatedPct.toFixed(1)}%`}
            />
            <Stat
              label="Unallocated"
              value={money(report.unallocated, currency)}
              sub={`${(100 - allocatedPct).toFixed(1)}%`}
              warn
            />
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border,#e5e7eb)" }}>
                <th style={{ padding: "4px 8px" }}>Tag value</th>
                <th style={{ padding: "4px 8px" }}>Team</th>
                <th style={{ padding: "4px 8px", textAlign: "right" }}>Cost</th>
                <th style={{ padding: "4px 8px", textAlign: "right" }}>Share</th>
              </tr>
            </thead>
            <tbody>
              {report.allocations.map((a) => {
                const isUnalloc = a.tag_value === UNALLOCATED;
                return (
                  <tr
                    key={a.tag_value}
                    style={{
                      borderBottom: "1px solid var(--border,#f1f5f9)",
                      color: isUnalloc ? "#b45309" : undefined,
                      fontWeight: isUnalloc ? 600 : undefined,
                    }}
                  >
                    <td style={{ padding: "4px 8px" }}>{a.tag_value}</td>
                    <td style={{ padding: "4px 8px" }} className="muted">
                      {a.team ?? "—"}
                    </td>
                    <td style={{ padding: "4px 8px", textAlign: "right" }}>
                      {money(a.cost, currency)}
                    </td>
                    <td style={{ padding: "4px 8px", textAlign: "right" }} className="muted">
                      {(a.share * 100).toFixed(1)}%
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      {loading && !report && <div className="skeleton-row" />}
    </>
  );
}

function Stat({
  label,
  value,
  sub,
  warn,
}: {
  label: string;
  value: string;
  sub?: string;
  warn?: boolean;
}) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 12 }}>
        {label}
      </div>
      <div style={{ fontSize: 20, fontWeight: 600, color: warn ? "#b45309" : undefined }}>
        {value}
      </div>
      {sub && (
        <div className="muted" style={{ fontSize: 12 }}>
          {sub}
        </div>
      )}
    </div>
  );
}
