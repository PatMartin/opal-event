import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Persona, ValidationReport } from "../types";
import { JsonField } from "../components/JsonField";

const BLANK_PERSONA = `{
  "persona_id": "amy",
  "name": "Amy",
  "voice_id": "en-gb-alan-low",
  "enabled": true
}`;

export default function PersonasPage() {
  const [items, setItems] = useState<Persona[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [enabledOnly, setEnabledOnly] = useState<boolean | undefined>(undefined);
  const [createBody, setCreateBody] = useState(BLANK_PERSONA);
  const [createValid, setCreateValid] = useState(true);
  const [validateId, setValidateId] = useState("");
  const [report, setReport] = useState<ValidationReport | null>(null);

  async function load(enabled?: boolean) {
    try {
      setError(null);
      setItems(await api.listPersonas(enabled));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    load(enabledOnly);
  }, [enabledOnly]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      setError(null);
      await api.createPersona(JSON.parse(createBody));
      await load(enabledOnly);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function toggleEnabled(persona: Persona) {
    try {
      await api.patchPersona(persona.persona_id, { enabled: !persona.enabled });
      await load(enabledOnly);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function remove(personaId: string) {
    try {
      await api.deletePersona(personaId);
      await load(enabledOnly);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function runValidate(e: React.FormEvent) {
    e.preventDefault();
    try {
      setError(null);
      setReport(await api.validatePersona(validateId));
    } catch (err) {
      setReport(null);
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <div>
      <h2>Personas</h2>
      {error && <div className="error-banner">{error}</div>}

      <form className="panel" onSubmit={create}>
        <h3>Register persona</h3>
        <JsonField label="Persona JSON" value={createBody} onChange={(t, _p, v) => { setCreateBody(t); setCreateValid(v); }} rows={7} />
        <button type="submit" disabled={!createValid}>
          Create
        </button>
      </form>

      <form className="panel" onSubmit={runValidate}>
        <h3>Validate persona</h3>
        <label>
          Persona ID
          <select aria-label="Persona ID" value={validateId} onChange={(e) => setValidateId(e.target.value)}>
            <option value="">-- choose --</option>
            {items.map((p) => (
              <option key={p.persona_id} value={p.persona_id}>
                {p.persona_id}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={!validateId}>
          Validate
        </button>
        {report && (
          <div className="panel">
            <p>
              Valid: <strong>{report.valid ? "yes" : "no"}</strong>
            </p>
            {report.errors.length > 0 && <pre>errors: {report.errors.join("\n")}</pre>}
            {report.warnings.length > 0 && <pre>warnings: {report.warnings.join("\n")}</pre>}
          </div>
        )}
      </form>

      <div className="panel">
        <label>
          Show
          <select
            aria-label="Enabled filter"
            value={enabledOnly === undefined ? "all" : enabledOnly ? "enabled" : "disabled"}
            onChange={(e) => setEnabledOnly(e.target.value === "all" ? undefined : e.target.value === "enabled")}
          >
            <option value="all">all</option>
            <option value="enabled">enabled only</option>
            <option value="disabled">disabled only</option>
          </select>
        </label>
      </div>

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>Enabled</th>
            <th>Voice</th>
            <th>Updated</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((persona) => (
            <tr key={persona.persona_id}>
              <td>{persona.persona_id}</td>
              <td>
                {persona.name}
                {persona.origin === "owner" && <span className="badge"> owner</span>}
              </td>
              <td>
                <span className={`badge ${persona.enabled ? "enabled" : "degraded"}`}>
                  {persona.enabled ? "enabled" : "disabled"}
                </span>
              </td>
              <td>{persona.voice_id || "-"}</td>
              <td>{persona.updated_at}</td>
              <td>
                <button className="secondary" onClick={() => toggleEnabled(persona)}>
                  {persona.enabled ? "Disable" : "Enable"}
                </button>{" "}
                <button className="danger" onClick={() => remove(persona.persona_id)}>
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
