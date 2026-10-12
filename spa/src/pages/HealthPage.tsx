import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Health, Stats } from "../types";

export default function HealthPage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setError(null);
      const [h, s] = await Promise.all([api.getHealth(), api.getStats()]);
      setHealth(h);
      setStats(s);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    load();
    const interval = setInterval(load, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <h2>Service health</h2>
      {error && <div className="error-banner">{error}</div>}
      {health && (
        <div className="panel">
          <p>
            Status: <span className={`badge ${health.status}`}>{health.status}</span>
          </p>
          <p>Broker running: {health.broker_running ? "yes" : "no"}</p>
          <p>Database ready: {health.database_ready ? "yes" : "no"}</p>
          <p>Pending restart: {health.pending_restart ? "yes (edit config restart-only fields applied)" : "no"}</p>
        </div>
      )}
      {stats && (
        <div className="stat-grid">
          {Object.entries(stats).map(([key, value]) => (
            <div className="stat-card" key={key}>
              <div className="value">{value}</div>
              <div className="label">{key.replace(/_/g, " ")}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
