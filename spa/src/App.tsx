import { useState } from "react";
import { getContextIds, getToken, setContextIds, setToken } from "./api";
import EventsPage from "./pages/EventsPage";
import RulesPage from "./pages/RulesPage";
import ActionsPage from "./pages/ActionsPage";
import ExecutionsPage from "./pages/ExecutionsPage";
import SettingsPage from "./pages/SettingsPage";
import HealthPage from "./pages/HealthPage";

const TABS = [
  { key: "health", label: "Health", component: HealthPage },
  { key: "events", label: "Events", component: EventsPage },
  { key: "rules", label: "Rules", component: RulesPage },
  { key: "actions", label: "Actions", component: ActionsPage },
  { key: "executions", label: "Executions", component: ExecutionsPage },
  { key: "settings", label: "Settings", component: SettingsPage },
] as const;

export default function App() {
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("health");
  const [tokenInput, setTokenInput] = useState(getToken());
  const [contextIds, setContextIdsState] = useState(getContextIds());

  function updateContextId(field: "sessionId" | "transactionId" | "causationId", value: string) {
    const next = { ...contextIds, [field]: value || undefined };
    setContextIdsState(next);
    setContextIds(next);
  }
  const Active = TABS.find((t) => t.key === tab)!.component;

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>Opal Event</h1>
        <nav>
          {TABS.map((t) => (
            <button key={t.key} className={t.key === tab ? "active" : ""} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </nav>
        <div style={{ padding: "16px" }}>
          <label className="muted">
            Bearer token
            <input
              type="password"
              value={tokenInput}
              onChange={(e) => {
                setTokenInput(e.target.value);
                setToken(e.target.value);
              }}
              placeholder="(if REST auth enabled)"
            />
          </label>
          <label className="muted">
            X-Opal-Session-Id
            <input
              value={contextIds.sessionId || ""}
              onChange={(e) => updateContextId("sessionId", e.target.value)}
              placeholder="(optional correlation ID)"
            />
          </label>
          <label className="muted">
            X-Opal-Transaction-Id
            <input
              value={contextIds.transactionId || ""}
              onChange={(e) => updateContextId("transactionId", e.target.value)}
              placeholder="(optional; server assigns if absent)"
            />
          </label>
          <label className="muted">
            X-Opal-Causation-Id
            <input
              value={contextIds.causationId || ""}
              onChange={(e) => updateContextId("causationId", e.target.value)}
              placeholder="(optional correlation ID)"
            />
          </label>
        </div>
      </aside>
      <main className="content">
        <Active />
      </main>
    </div>
  );
}
