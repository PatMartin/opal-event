import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Action } from "../types";
import { JsonField } from "../components/JsonField";

const BLANK_ACTION = `{
  "id": "example_action",
  "type": "shell",
  "enabled": true,
  "mode": "exec",
  "executable": "/bin/echo",
  "args": ["hello"],
  "stdin": "none",
  "timeout_seconds": 10
}`;

export default function ActionsPage() {
  const [items, setItems] = useState<Action[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [createBody, setCreateBody] = useState(BLANK_ACTION);
  const [createValid, setCreateValid] = useState(true);
  const [executeActionId, setExecuteActionId] = useState("");
  const [executeEvent, setExecuteEvent] = useState('{\n  "topic": "manual.test",\n  "data": {}\n}');
  const [executeResult, setExecuteResult] = useState<string | null>(null);

  async function load() {
    try {
      setError(null);
      const data = await api.listActions();
      setItems(data.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      setError(null);
      const body = JSON.parse(createBody);
      await api.createAction(body);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function toggleEnabled(action: Action) {
    try {
      await api.patchAction(action.id, { enabled: !action.enabled });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function remove(actionId: string) {
    try {
      await api.deleteAction(actionId);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function execute(e: React.FormEvent) {
    e.preventDefault();
    try {
      setError(null);
      const event = JSON.parse(executeEvent);
      const execution = await api.executeAction(executeActionId, event);
      setExecuteResult(`Execution ${execution.id}: ${execution.status}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <div>
      <h2>Actions</h2>
      {error && <div className="error-banner">{error}</div>}

      <form className="panel" onSubmit={create}>
        <h3>Create action</h3>
        <p className="muted">Discriminated by "type": shell, http, or python. See README for each schema.</p>
        <JsonField label="Action JSON" value={createBody} onChange={(t, _p, v) => { setCreateBody(t); setCreateValid(v); }} rows={10} />
        <button type="submit" disabled={!createValid}>
          Create
        </button>
      </form>

      <form className="panel" onSubmit={execute}>
        <h3>Execute action manually</h3>
        <label>
          Action ID
          <select value={executeActionId} onChange={(e) => setExecuteActionId(e.target.value)}>
            <option value="">-- choose --</option>
            {items.map((a) => (
              <option key={a.id} value={a.id}>
                {a.id}
              </option>
            ))}
          </select>
        </label>
        <JsonField label="Event JSON" value={executeEvent} onChange={(t) => setExecuteEvent(t)} rows={5} />
        <button type="submit" disabled={!executeActionId}>
          Execute
        </button>
        {executeResult && <p className="muted">{executeResult}</p>}
      </form>

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Type</th>
            <th>Enabled</th>
            <th>Details</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((action) => (
            <tr key={action.id}>
              <td>{action.id}</td>
              <td>{action.type}</td>
              <td>
                <span className={`badge ${action.enabled ? "enabled" : "degraded"}`}>{action.enabled ? "enabled" : "disabled"}</span>
              </td>
              <td>
                <pre>{JSON.stringify(action, null, 0)}</pre>
              </td>
              <td>
                <button className="secondary" onClick={() => toggleEnabled(action)}>
                  {action.enabled ? "Disable" : "Enable"}
                </button>{" "}
                <button className="danger" onClick={() => remove(action.id)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
