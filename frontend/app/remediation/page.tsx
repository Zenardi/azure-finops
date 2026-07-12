"use client";

import { useEffect, useState } from "react";
import { apiGet, shortId } from "../lib/api";

function ts(value?: string | null): string {
  return value ? value.replace("T", " ").slice(0, 19) : "—";
}

export default function Remediation() {
  const [actions, setActions] = useState<any[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    apiGet<any[]>("/api/remediation?limit=100").then(setActions).catch((e) => setErr(String(e)));
  }, []);

  return (
    <>
      <h1>Remediation audit</h1>
      <p className="sub">
        Every remediation attempt is recorded here. Dry-run is the default; real Azure writes
        require <code>REMEDIATION_ENABLED=true</code> plus the write service principal, and only
        for allow-listed resource groups (resources tagged <code>finops:exclude</code> are never
        touched).
      </p>
      {err && <div className="err">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>When</th><th>Action</th><th>Resource</th><th>Dry-run</th><th>Status</th><th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {actions.map((a, i) => (
            <tr key={i}>
              <td className="muted">{ts(a.requested_at)}</td>
              <td>{a.action_type}</td>
              <td>{shortId(a.resource_id || "")}</td>
              <td>{a.dry_run ? "yes" : "no"}</td>
              <td>
                <span
                  className={`badge ${
                    a.status === "executed"
                      ? "approved"
                      : a.status === "failed" || a.status === "blocked"
                        ? "rejected"
                        : ""
                  }`}
                >
                  {a.status}
                </span>
              </td>
              <td className="muted">{a.error || ""}</td>
            </tr>
          ))}
          {actions.length === 0 && !err && (
            <tr><td colSpan={6} className="muted">No remediation attempts yet.</td></tr>
          )}
        </tbody>
      </table>
    </>
  );
}
