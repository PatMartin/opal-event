import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Profile } from "../types";
import { JsonField } from "../components/JsonField";
import { StatusBadge } from "../components/StatusBadge";

// Profiles (v0.1.0, kernel v3): namespaced documents — `shared` plus a `modules` map of
// module-owned compartments. opal-event's compartment is a closed empty schema, so
// activation is a local selection only; foreign compartments are preserved verbatim.
export default function ProfilesPage() {
  const [items, setItems] = useState<Profile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [modulesJson, setModulesJson] = useState("{}");
  const [modulesValid, setModulesValid] = useState(true);
  const [activate, setActivate] = useState(false);

  async function load() {
    try {
      setError(null);
      setItems(await api.listProfiles());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      setError(null);
      const modules = JSON.parse(modulesJson || "{}");
      await api.createProfile({
        name,
        description: description || null,
        is_active: activate,
        modules,
      });
      setName("");
      setDescription("");
      setModulesJson("{}");
      setActivate(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function activateProfile(id: string) {
    try {
      setError(null);
      await api.activateProfile(id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function remove(id: string) {
    if (!window.confirm(`Delete profile ${id}?`)) return;
    try {
      setError(null);
      await api.deleteProfile(id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <div>
      <h2>Profiles</h2>
      <form className="panel" onSubmit={create}>
        <h3>Create profile</h3>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required placeholder="high_throughput" />
        </label>
        <label>
          Description
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <JsonField
          label="Module compartments (OpalProfileModules JSON; opal-event is empty)"
          value={modulesJson}
          onChange={(text, _p, valid) => {
            setModulesJson(text);
            setModulesValid(valid);
          }}
        />
        <label>
          <input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} /> Activate on
          create
        </label>
        <button type="submit" disabled={!modulesValid || !name}>
          Create
        </button>
      </form>

      {error && <div className="error-banner">{error}</div>}

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>Active</th>
            <th>Modules</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.id}>
              <td>{p.id}</td>
              <td>
                {p.name}
                {p.description && <div className="muted">{p.description}</div>}
              </td>
              <td>{p.is_active ? <StatusBadge value="enabled" /> : "-"}</td>
              <td>
                <pre>{Object.keys(p.modules ?? {}).length ? JSON.stringify(p.modules) : "-"}</pre>
              </td>
              <td>
                {!p.is_active && (
                  <button type="button" className="secondary" onClick={() => activateProfile(p.id)}>
                    Activate
                  </button>
                )}{" "}
                <button type="button" className="danger" onClick={() => remove(p.id)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {items.length === 0 && <p className="muted">No profiles defined.</p>}
    </div>
  );
}
