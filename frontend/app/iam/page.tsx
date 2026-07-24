"use client";

import { useCallback, useEffect, useState } from "react";
import {
  collectIam,
  getIamFindings,
  getIamScore,
  IamCategory,
  IamFinding,
  IamScore,
  IamSeverity,
  shortId,
} from "../lib/api";

const CLOUDS = ["all", "aws", "azure", "gcp"] as const;

// Severity -> badge class (critical/high stand out — honest, not green by omission).
const SEV_META: Record<IamSeverity, { cls: string; label: string }> = {
  critical: { cls: "badge rejected", label: "Critical" },
  high: { cls: "badge rejected", label: "High" },
  medium: { cls: "badge", label: "Medium" },
  low: { cls: "badge", label: "Low" },
};

const CATEGORY_LABEL: Record<IamCategory, string> = {
  over_privilege: "Over-privilege",
  unused_principal: "Unused principal",
  stale_credential: "Stale credential",
  missing_mfa: "Missing MFA",
  public_exposure: "Public exposure",
};

export default function IamRisk() {
  const [provider, setProvider] = useState<string>("all");
  const [findings, setFindings] = useState<IamFinding[]>([]);
  const [score, setScore] = useState<IamScore | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [f, s] = await Promise.all([getIamFindings(provider), getIamScore(provider)]);
      setFindings(f);
      setScore(s);
      setErr("");
    } catch (e) {
      setErr(String(e));
      setFindings([]);
      setScore(null);
    } finally {
      setLoading(false);
    }
  }, [provider]);

  useEffect(() => {
    load();
  }, [load]);

  const rescan = useCallback(async () => {
    setScanning(true);
    try {
      await collectIam(provider);
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setScanning(false);
    }
  }, [provider, load]);

  return (
    <>
      <h1>Identity / IAM risk</h1>
      <p className="sub">
        Identity posture across the clouds: over-permissioned principals, unused principals,
        stale credentials, missing MFA and public exposure. Each finding carries{" "}
        <strong>evidence + severity</strong>, and the <strong>0-100 risk score</strong> is the
        capped sum of finding weights — reproducible from the findings. Advisory only: nothing here
        changes an identity.
      </p>

      <form className="history-controls" onSubmit={(e) => e.preventDefault()}>
        <div className="field">
          <label htmlFor="iam-cloud">Cloud</label>
          <select id="iam-cloud" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {CLOUDS.map((c) => (
              <option key={c} value={c}>
                {c === "all" ? "All clouds" : c}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ alignSelf: "end" }}>
          <button className="btn" type="button" onClick={rescan} disabled={scanning}>
            {scanning ? "Scanning…" : "Run scan"}
          </button>
        </div>
      </form>

      {err && <div className="err">{err}</div>}

      {score && (
        <p className="sub" style={{ marginTop: 0 }}>
          <span className={score.overall > 0 ? "badge rejected" : "badge"}>
            Overall risk {score.overall}/100
          </span>{" "}
          <span className="muted">{findings.length} findings</span>
        </p>
      )}

      {score && score.accounts.length > 0 && (
        <table style={{ marginBottom: "1.5rem" }}>
          <thead>
            <tr>
              <th>Cloud</th>
              <th>Account</th>
              <th className="num">Risk score</th>
              <th className="num">Critical</th>
              <th className="num">High</th>
              <th className="num">Medium</th>
              <th className="num">Findings</th>
            </tr>
          </thead>
          <tbody>
            {score.accounts.map((a) => (
              <tr key={`${a.provider}/${a.account_id}`}>
                <td>
                  <span className="badge">{a.provider}</span>
                </td>
                <td className="muted" title={a.account_id ?? ""}>
                  {shortId(a.account_id ?? "—")}
                </td>
                <td className="num">
                  <span className={a.score > 0 ? "badge rejected" : "badge"}>{a.score}</span>
                </td>
                <td className="num">{a.by_severity.critical}</td>
                <td className="num">{a.by_severity.high}</td>
                <td className="num">{a.by_severity.medium}</td>
                <td className="num">{a.finding_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Findings</h2>
      <table>
        <thead>
          <tr>
            <th>Severity</th>
            <th>Category</th>
            <th>Principal</th>
            <th>Cloud</th>
            <th>Basis</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((f) => {
            const meta = SEV_META[f.severity];
            return (
              <tr key={f.id}>
                <td>
                  <span className={meta.cls}>{meta.label}</span>
                </td>
                <td>{CATEGORY_LABEL[f.category] ?? f.category}</td>
                <td className="muted" title={f.principal_id}>
                  {shortId(f.principal_id)}
                </td>
                <td>
                  <span className="badge">{f.provider}</span>
                </td>
                <td className="muted">{f.rationale}</td>
              </tr>
            );
          })}
          {findings.length === 0 && !err && (
            <tr>
              <td colSpan={5} className="muted">
                {loading
                  ? "Loading…"
                  : "No findings yet. Run a scan to collect identity posture."}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
