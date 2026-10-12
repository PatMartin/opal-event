import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Rule, RuleTestResult } from "../types";
import { JsonField } from "../components/JsonField";

const BLANK_RULE = `{
  "id": "example_rule",
  "enabled": true,
  "topic": "vision.*",
  "conditions": [
    { "path": "data.object", "operator": "eq", "value": "person" }
  ],
  "actions": ["announce_person"]
}`;

export default function RulesPage() {
  const [items, setItems] = useState<Rule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [createBody, setCreateBody] = useState(BLANK_RULE);
  const [createValid, setCreateValid] = useState(true);
  const [testRuleId, setTestRuleId] = useState("");
  const [testEvent, setTestEvent] = useState('{\n  "topic": "vision.object.detected",\n  "data": {"object": "person"}\n}');
  const [testResult, setTestResult] = useState<RuleTestResult | null>(null);

  async function load() {
    try {
      setError(null);
      const data = await api.listRules();
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
      await api.createRule(body);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function toggleEnabled(rule: Rule) {
    try {
      await api.patchRule(rule.id, { enabled: !rule.enabled });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function remove(ruleId: string) {
    try {
      await api.deleteRule(ruleId);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function runTest(e: React.FormEvent) {
    e.preventDefault();
    try {
      setError(null);
      const event = JSON.parse(testEvent);
      setTestResult(await api.testRule(testRuleId, event));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <div>
      <h2>Rules</h2>
      {error && <div className="error-banner">{error}</div>}

      <form className="panel" onSubmit={create}>
        <h3>Create rule</h3>
        <JsonField label="Rule JSON" value={createBody} onChange={(t, _p, v) => { setCreateBody(t); setCreateValid(v); }} rows={8} />
        <button type="submit" disabled={!createValid}>
          Create
        </button>
      </form>

      <form className="panel" onSubmit={runTest}>
        <h3>Test rule (dry run, no execution)</h3>
        <label>
          Rule ID
          <select value={testRuleId} onChange={(e) => setTestRuleId(e.target.value)}>
            <option value="">-- choose --</option>
            {items.map((r) => (
              <option key={r.id} value={r.id}>
                {r.id}
              </option>
            ))}
          </select>
        </label>
        <JsonField label="Sample event JSON" value={testEvent} onChange={(t) => setTestEvent(t)} rows={5} />
        <button type="submit" disabled={!testRuleId}>
          Test
        </button>
        {testResult && (
          <div className="panel">
            <p>
              Matched: <strong>{testResult.matched ? "yes" : "no"}</strong>
            </p>
            <p>Action IDs: {testResult.action_ids.join(", ") || "(none)"}</p>
            <pre>{JSON.stringify(testResult.checks, null, 2)}</pre>
          </div>
        )}
      </form>

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Enabled</th>
            <th>Topic</th>
            <th>Conditions</th>
            <th>Actions</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((rule) => (
            <tr key={rule.id}>
              <td>{rule.id}</td>
              <td>
                <span className={`badge ${rule.enabled ? "enabled" : "degraded"}`}>{rule.enabled ? "enabled" : "disabled"}</span>
              </td>
              <td>{rule.topic}</td>
              <td>
                <pre>{JSON.stringify(rule.conditions)}</pre>
              </td>
              <td>{rule.actions.join(", ")}</td>
              <td>
                <button className="secondary" onClick={() => toggleEnabled(rule)}>
                  {rule.enabled ? "Disable" : "Enable"}
                </button>{" "}
                <button className="danger" onClick={() => remove(rule.id)}>
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
