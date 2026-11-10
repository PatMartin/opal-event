import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Profile } from "../types";
import { JsonField } from "../components/JsonField";
import { StatusBadge } from "../components/StatusBadge";

// Profiles (v1.7.1): named presets of portable operating defaults. Activation applies
// the stored settings patch to live config; exactly one profile is active at a time.
export default function ProfilesPage() {
  const [items, setItems] = useState<Profile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [settingsJson, setSettingsJson] = useState("{}");
  const [settingsValid, setSettingsValid] = useState(true);
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
      const settings = JSON.parse(settingsJson || "{}");
      await api.createProfile({
        name,
        description: description || null,
        is_active: activate,
        settings: Object.keys(settings).length ? settings : null,
      });
      setName("");
      setDescription("");
      setSettingsJson("{}");
      setActivate(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function activateProfile(id: number) {
    try {
      setError(null);
      await api.activateProfile(id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function remove(id: number) {
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
          label="Settings preset (PortableSettingsPatch JSON)"
          value={settingsJson}
          onChange={(text, _p, valid) => {
            setSettingsJson(text);
            setSettingsValid(valid);
          }}
        />
        <label>
          <input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} /> Activate on
          create
        </label>
        <button type="submit" disabled={!settingsValid || !name}>
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
            <th>Preset</th>
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
                <pre>{p.settings ? JSON.stringify(p.settings) : "-"}</pre>
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
