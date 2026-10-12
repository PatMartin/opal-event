import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Execution } from "../types";
import { StatusBadge } from "../components/StatusBadge";

export default function ExecutionsPage() {
  const [items, setItems] = useState<Execution[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  async function load() {
    try {
      setError(null);
      const data = await api.listExecutions({ status: status || undefined, limit: 100 });
      setItems(data.items);
      setHasMore(data.has_more);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    load();
    const interval = setInterval(load, 4000);
    return () => clearInterval(interval);
  }, [status]);

  return (
    <div>
      <h2>Executions</h2>
      {error && <div className="error-banner">{error}</div>}
      <div className="panel">
        <label>
          Filter by status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">(any)</option>
            {["queued", "running", "succeeded", "failed", "timed_out", "dropped"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <button className="secondary" onClick={load}>
          Refresh
        </button>
      </div>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Action</th>
            <th>Rule</th>
            <th>Trigger</th>
            <th>Status</th>
            <th>Duration</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {items.map((execution) => (
            <tr key={execution.id}>
              <td>{execution.id}</td>
              <td>{execution.action_id}</td>
              <td>{execution.rule_id || "-"}</td>
              <td>{execution.trigger}</td>
              <td>
                <StatusBadge value={execution.status} />
              </td>
              <td>{execution.duration_ms != null ? `${execution.duration_ms}ms` : "-"}</td>
              <td>{execution.error_message || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {hasMore && <p className="muted">More executions are available; narrow the status filter to see fewer/more recent results.</p>}
    </div>
  );
}
