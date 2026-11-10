import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Health, ServiceStatus, Stats } from "../types";

export default function HealthPage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);

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
    void refreshLog();
    const interval = setInterval(load, 3000);
    return () => clearInterval(interval);
  }, []);

  async function refreshLog() {
    try {
      const tail = await api.serviceLog(10);
      setLogLines(tail.lines);
    } catch {
      setLogLines([]); // no managed log file yet — fine
    }
  }

  async function manageService(op: "start" | "stop") {
    try {
      setError(null);
      const status = op === "start" ? await api.serviceStart() : await api.serviceStop();
      setServiceStatus(status);
      if (status.state === "stopped") setLogLines([]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

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

      <div className="panel">
        <h3>Managed service</h3>
        <p className="muted">
          Controls the pidfile-tracked server process (start doubles as restart; stopping the process you are
          connected to terminates it after responding).
        </p>
        <button type="button" onClick={() => manageService("start")}>
          Start / restart
        </button>{" "}
        <button type="button" className="danger" onClick={() => manageService("stop")}>
          Stop
        </button>{" "}
        <button type="button" className="secondary" onClick={() => void refreshLog()}>
          Refresh log
        </button>
        {serviceStatus && (
          <p>
            state: <span className={`badge ${serviceStatus.state}`}>{serviceStatus.state}</span>
            {serviceStatus.pid != null && ` pid ${serviceStatus.pid}`}
            {serviceStatus.previous_pid != null && ` (was ${serviceStatus.previous_pid})`}
          </p>
        )}
        {logLines.length > 0 && (
          <pre className="scrollable-pre">{logLines.join("\n")}</pre>
        )}
      </div>
    </div>
  );
}
