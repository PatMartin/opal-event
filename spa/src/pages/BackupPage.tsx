import { useState } from "react";
import { api, ApiError } from "../api";
import type { BackupImportResult, ConfigBackup, ValidationReport } from "../types";

export default function BackupPage() {
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [backup, setBackup] = useState<ConfigBackup | null>(null);
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<BackupImportResult | null>(null);

  function describe(err: unknown): string {
    return err instanceof ApiError ? err.message : String(err);
  }

  async function downloadBackup() {
    try {
      setError(null);
      const data = await api.exportBackup();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `opal_event_backup_${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(describe(err));
    }
  }

  async function onFileSelected(file: File | undefined) {
    setError(null);
    setReport(null);
    setResult(null);
    setConfirmed(false);
    setBackup(null);
    if (!file) {
      setFileName(null);
      return;
    }
    setFileName(file.name);
    try {
      const parsed = JSON.parse(await file.text()) as ConfigBackup;
      setBackup(parsed);
      setReport(await api.validateBackup(parsed));
    } catch (err) {
      setFileName(null);
      setError(err instanceof SyntaxError ? `file is not valid JSON: ${err.message}` : describe(err));
    }
  }

  async function doImport() {
    if (!backup || !report?.valid || !confirmed) return;
    setImporting(true);
    setError(null);
    try {
      // Optimistic concurrency: fetch the current revision so a concurrent change is a 409, not a silent overwrite.
      const { etag } = await api.getSettings();
      const res = await api.importBackup(backup, etag);
      setResult(res);
      setReport(null);
      setBackup(null);
      setFileName(null);
      setConfirmed(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(`import rejected: the configuration changed concurrently (${err.message}). Re-select the file and try again.`);
      } else {
        setError(describe(err));
      }
    } finally {
      setImporting(false);
    }
  }

  return (
    <div>
      <h2>Backup</h2>
      {error && <div className="error-banner">{error}</div>}
      {result && (
        <div className="panel">
          <p>
            Import succeeded. New revision: <code>{result.revision}</code>
          </p>
          {result.pending_restart && (
            <p className="muted">Some changes require a service restart to take effect (pending_restart).</p>
          )}
        </div>
      )}

      <div className="panel">
        <h3>Export</h3>
        <p className="muted">
          Downloads the portable configuration (settings, actions, rules) as JSON. Host-bound values and secrets are
          never included.
        </p>
        <button className="secondary" onClick={() => void downloadBackup()}>
          Download backup
        </button>
      </div>

      <div className="panel">
        <h3>Import</h3>
        <label>
          Backup file (.json)
          <input type="file" accept=".json,application/json" onChange={(e) => void onFileSelected(e.target.files?.[0])} />
        </label>
        {fileName && report === null && !error && <p className="muted">Validating {fileName}…</p>}
        {report && (
          <div>
            {report.errors.length > 0 && (
              <ul className="error-banner">
                {report.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            )}
            {report.warnings.length > 0 && (
              <ul className="muted">
                {report.warnings.map((w, i) => (
                  <li key={i}>warning: {w}</li>
                ))}
              </ul>
            )}
            {report.valid ? (
              <p>Backup is valid{report.warnings.length > 0 ? " (with warnings)" : ""}.</p>
            ) : (
              <p className="error-banner">Backup is invalid and cannot be imported.</p>
            )}
          </div>
        )}
        {report?.valid && (
          <div>
            <label>
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                style={{ width: "auto", marginRight: 8 }}
              />
              I understand this replaces the current actions, rules, and portable settings.
            </label>
            <button type="submit" disabled={!confirmed || importing} onClick={() => void doImport()}>
              {importing ? "Importing…" : "Import backup"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
