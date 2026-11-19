import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { SettingsMap } from "../types";

// v0.1.0: GET /api/v1/setting/ is a flat OpalSettingsMap and the collection PATCH is retired;
// each changed key is written individually with PUT /api/v1/setting/{key} + If-Match.
const EDITABLE_KEYS = [
  "execution.max_workers",
  "execution.queue_size",
  "execution.default_timeout_seconds",
  "execution.max_output_bytes",
  "persistence.event_mode",
  "persistence.retention_days",
  "max_event_bytes",
] as const;

type EditableKey = (typeof EDITABLE_KEYS)[number];

export default function SettingsPage() {
  const [map, setMap] = useState<SettingsMap | null>(null);
  const [saved, setSavedMap] = useState<SettingsMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedFlag, setSavedFlag] = useState(false);

  async function load() {
    try {
      setError(null);
      const { settings } = await api.getSettings();
      setMap(settings);
      setSavedMap(settings);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    load();
  }, []);

  function setKey(key: EditableKey, value: unknown) {
    setMap((m) => (m ? { ...m, [key]: value } : m));
  }

  const num = (key: EditableKey) => Number(map?.[key] ?? 0);
  const str = (key: EditableKey) => String(map?.[key] ?? "");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!map || !saved) return;
    try {
      setError(null);
      setSavedFlag(false);
      for (const key of EDITABLE_KEYS) {
        if (map[key] !== saved[key]) {
          const { etag } = await api.getSettings();
          await api.putSetting(key, map[key], etag);
        }
      }
      setSavedFlag(true);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  if (!map) return <p className="muted">Loading...</p>;

  return (
    <div>
      <h2>Settings</h2>
      <p className="muted">
        Portable, non-secret, live-adjustable settings only. Network bindings, CURVE keys, bearer tokens, and the
        database path require editing the YAML config file directly and restarting the service.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {savedFlag && <p className="muted">Saved.</p>}
      <form className="panel" onSubmit={save}>
        <h3>Execution</h3>
        <label>
          Max workers
          <input
            type="number"
            min={1}
            max={128}
            value={num("execution.max_workers")}
            onChange={(e) => setKey("execution.max_workers", Number(e.target.value))}
          />
        </label>
        <label>
          Queue size
          <input
            type="number"
            min={1}
            max={100000}
            value={num("execution.queue_size")}
            onChange={(e) => setKey("execution.queue_size", Number(e.target.value))}
          />
        </label>
        <label>
          Default timeout (seconds)
          <input
            type="number"
            min={1}
            max={3600}
            value={num("execution.default_timeout_seconds")}
            onChange={(e) => setKey("execution.default_timeout_seconds", Number(e.target.value))}
          />
        </label>
        <label>
          Max output bytes
          <input
            type="number"
            min={0}
            max={10485760}
            value={num("execution.max_output_bytes")}
            onChange={(e) => setKey("execution.max_output_bytes", Number(e.target.value))}
          />
        </label>

        <h3>Persistence</h3>
        <label>
          Event mode
          <select
            value={str("persistence.event_mode")}
            onChange={(e) => setKey("persistence.event_mode", e.target.value)}
          >
            <option value="all">all</option>
            <option value="matched">matched</option>
            <option value="none">none</option>
          </select>
        </label>
        <label>
          Retention (days)
          <input
            type="number"
            min={1}
            max={3650}
            value={num("persistence.retention_days")}
            onChange={(e) => setKey("persistence.retention_days", Number(e.target.value))}
          />
        </label>

        <h3>ZeroMQ</h3>
        <label>
          Max event bytes (ceiling 1048576)
          <input
            type="number"
            min={1}
            max={1048576}
            value={num("max_event_bytes")}
            onChange={(e) => setKey("max_event_bytes", Number(e.target.value))}
          />
        </label>

        <button type="submit">Save</button>
      </form>
    </div>
  );
}
