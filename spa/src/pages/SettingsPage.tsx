import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { PortableSettings } from "../types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<PortableSettings | null>(null);
  const [etag, setEtag] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function load() {
    try {
      setError(null);
      const { settings, etag } = await api.getSettings();
      setSettings(settings);
      setEtag(etag);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!settings) return;
    try {
      setError(null);
      setSaved(false);
      await api.patchSettings(
        {
          execution: settings.execution,
          persistence: settings.persistence,
          max_event_bytes: settings.max_event_bytes,
        },
        etag,
      );
      setSaved(true);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  if (!settings) return <p className="muted">Loading...</p>;

  return (
    <div>
      <h2>Settings</h2>
      <p className="muted">
        Portable, non-secret, live-adjustable settings only. Network bindings, CURVE keys, bearer tokens, and the
        database path require editing the YAML config file directly and restarting the service.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {saved && <p className="muted">Saved.</p>}
      <form className="panel" onSubmit={save}>
        <h3>Execution</h3>
        <label>
          Max workers
          <input
            type="number"
            min={1}
            max={128}
            value={settings.execution.max_workers}
            onChange={(e) => setSettings({ ...settings, execution: { ...settings.execution, max_workers: Number(e.target.value) } })}
          />
        </label>
        <label>
          Queue size
          <input
            type="number"
            min={1}
            max={100000}
            value={settings.execution.queue_size}
            onChange={(e) => setSettings({ ...settings, execution: { ...settings.execution, queue_size: Number(e.target.value) } })}
          />
        </label>
        <label>
          Default timeout (seconds)
          <input
            type="number"
            min={1}
            max={3600}
            value={settings.execution.default_timeout_seconds}
            onChange={(e) =>
              setSettings({ ...settings, execution: { ...settings.execution, default_timeout_seconds: Number(e.target.value) } })
            }
          />
        </label>
        <label>
          Max output bytes
          <input
            type="number"
            min={0}
            max={10485760}
            value={settings.execution.max_output_bytes}
            onChange={(e) => setSettings({ ...settings, execution: { ...settings.execution, max_output_bytes: Number(e.target.value) } })}
          />
        </label>

        <h3>Persistence</h3>
        <label>
          Event mode
          <select
            value={settings.persistence.event_mode}
            onChange={(e) =>
              setSettings({ ...settings, persistence: { ...settings.persistence, event_mode: e.target.value as "all" | "matched" | "none" } })
            }
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
            value={settings.persistence.retention_days}
            onChange={(e) =>
              setSettings({ ...settings, persistence: { ...settings.persistence, retention_days: Number(e.target.value) } })
            }
          />
        </label>

        <h3>ZeroMQ</h3>
        <label>
          Max event bytes (ceiling 1048576)
          <input
            type="number"
            min={1}
            max={1048576}
            value={settings.max_event_bytes}
            onChange={(e) => setSettings({ ...settings, max_event_bytes: Number(e.target.value) })}
          />
        </label>

        <button type="submit">Save</button>
      </form>
    </div>
  );
}
