import { useState } from "react";
import { api, ApiError } from "../api";
import type { ConfigLoadResult, ConfigSnapshot, ConfigValidation } from "../types";

function describe(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

export default function SnapshotPage() {
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState<ConfigLoadResult | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<ConfigSnapshot | null>(null);
  const [validation, setValidation] = useState<ConfigValidation | null>(null);
  const [busy, setBusy] = useState(false);

  async function saveManaged() {
    try {
      setError(null);
      await api.saveConfigSnapshot("managed");
    } catch (e) {
      setError(describe(e));
    }
  }

  async function download() {
    try {
      setError(null);
      const { snapshot } = await api.saveConfigSnapshot("response");
      const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `opal-event-snapshot-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(describe(e));
    }
  }

  async function onFile(file: File | undefined) {
    setValidation(null);
    setApplied(null);
    setCandidate(null);
    setFileName(null);
    if (!file) return;
    setFileName(file.name);
    try {
      const parsed = JSON.parse(await file.text());
      const report = await api.validateConfigSnapshot(parsed);
      setCandidate(parsed);
      setValidation(report);
    } catch (e) {
      setFileName(null);
      setError(e instanceof SyntaxError ? `file is not valid JSON: ${e.message}` : describe(e));
    }
  }

  async function load(snapshot: ConfigSnapshot | null) {
    if (!window.confirm("Load snapshot? This replaces all portable settings, rules, actions, personas and profiles."))
      return;
    setBusy(true);
    setError(null);
    try {
      const { etag } = await api.getSettings();
      const result = await api.loadConfigSnapshot(snapshot, etag);
      setApplied(result);
      setValidation(null);
      setCandidate(null);
      setFileName(null);
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h2>Configuration snapshots</h2>
      {error && <div className="error-banner">{error}</div>}
      {applied && (
        <div className="panel">
          <p>Snapshot applied.</p>
          {applied.pending_restart_fields && applied.pending_restart_fields.length > 0 && (
            <p className="muted">Restart required for: {applied.pending_restart_fields.join(", ")}</p>
          )}
          {(applied.warnings ?? []).map((w, i) => (
            <p key={i} className="muted">
              {w.path}: {w.message}
            </p>
          ))}
        </div>
      )}
      <div className="panel">
        <h3>Save</h3>
        <p className="muted">
          Snapshots carry portable settings, profiles, personas, rules and actions — never secrets, history or runtime
          state.
        </p>
        <button className="secondary" onClick={() => void saveManaged()}>
          Save managed snapshot
        </button>{" "}
        <button className="secondary" onClick={() => void download()}>
          Download snapshot
        </button>
      </div>
      <div className="panel">
        <h3>Load managed snapshot</h3>
        <p className="muted">Atomically restores the service-managed snapshot saved via "Save managed snapshot".</p>
        <button className="secondary" disabled={busy} onClick={() => void load(null)}>
          Load managed snapshot
        </button>
      </div>
      <div className="panel">
        <h3>Load from file</h3>
        <label>
          Snapshot file (.json)
          <input
            type="file"
            accept=".json,application/json"
            onChange={(e) => void onFile(e.target.files?.[0])}
          />
        </label>
        {fileName && !validation && !error && <p className="muted">Validating {fileName}…</p>}
        {validation && (
          <div>
            {validation.errors.length > 0 && (
              <ul className="error-banner">
                {validation.errors.map((e, i) => (
                  <li key={i}>
                    [{e.code}] {e.path}: {e.message}
                  </li>
                ))}
              </ul>
            )}
            {validation.warnings.length > 0 && (
              <ul className="muted">
                {validation.warnings.map((w, i) => (
                  <li key={i}>
                    [{w.code}] {w.path}: {w.message}
                  </li>
                ))}
              </ul>
            )}
            {validation.valid && <p className="muted">{fileName} is valid.</p>}
            <button className="secondary" disabled={!validation.valid || busy} onClick={() => void load(candidate)}>
              Load {fileName}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
