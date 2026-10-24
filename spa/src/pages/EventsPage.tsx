import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { EventItem } from "../types";
import { JsonField } from "../components/JsonField";

export default function EventsPage() {
  const [items, setItems] = useState<EventItem[]>([]);
  const [topic, setTopic] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [publishTopic, setPublishTopic] = useState("intent.recognized");
  const [publishData, setPublishData] = useState('{\n  "key": "value"\n}');
  const [publishDataValid, setPublishDataValid] = useState(true);
  const [publishResult, setPublishResult] = useState<string | null>(null);

  async function loadPage(cursorToLoad: string | null = null, newHistory: string[] = []) {
    try {
      setError(null);
      const data = await api.listEvents({
        topic: topic || undefined,
        cursor: cursorToLoad || undefined,
        limit: 50,
      });
      setItems(data.items);
      setCurrentCursor(cursorToLoad);
      setNextCursor(data.next_cursor);
      setCursorHistory(newHistory);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    loadPage(null, []);
  }, []);

  async function publish(e: React.FormEvent) {
    e.preventDefault();
    try {
      setError(null);
      const data = JSON.parse(publishData || "{}");
      const receipt = await api.publishEvent({ topic: publishTopic, data });
      setPublishResult(`Accepted: ${receipt.id} at ${receipt.published_at}`);
      setTimeout(() => loadPage(currentCursor, cursorHistory), 400);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function clearAll() {
    if (!window.confirm("Are you sure you want to clear all stored events?")) {
      return;
    }
    try {
      setError(null);
      await api.clearEvents();
      await loadPage(null, []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function remove(eventId: string) {
    if (!window.confirm(`Are you sure you want to delete event "${eventId}"?`)) {
      return;
    }
    try {
      setError(null);
      await api.deleteEvent(eventId);
      await loadPage(currentCursor, cursorHistory);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  function handleNext() {
    if (!nextCursor) return;
    loadPage(nextCursor, [...cursorHistory, currentCursor ?? ""]);
  }

  function handlePrevious() {
    if (cursorHistory.length === 0) return;
    const prevCursor = cursorHistory[cursorHistory.length - 1] || null;
    const newHistory = cursorHistory.slice(0, -1);
    loadPage(prevCursor, newHistory);
  }

  return (
    <div>
      <h2>Events</h2>
      <form className="panel" onSubmit={publish}>
        <h3>Publish event</h3>
        <label>
          Topic
          <input value={publishTopic} onChange={(e) => setPublishTopic(e.target.value)} placeholder="intent.recognized" />
        </label>
        <JsonField label="Data (JSON)" value={publishData} onChange={(text, _p, valid) => { setPublishData(text); setPublishDataValid(valid); }} />
        <button type="submit" disabled={!publishDataValid}>
          Publish
        </button>
        {publishResult && <p className="muted">{publishResult}</p>}
      </form>

      {error && <div className="error-banner">{error}</div>}

      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          loadPage(null, []);
        }}
      >
        <label>
          Filter by topic
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="vision.*" />
        </label>
        <button type="submit" className="secondary">
          Refresh
        </button>{" "}
        <button type="button" className="danger" onClick={clearAll}>
          Clear all events
        </button>
      </form>

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Topic</th>
            <th>Source</th>
            <th>Timestamp</th>
            <th>Data</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((event) => (
            <tr key={event.id}>
              <td>{event.id}</td>
              <td>{event.topic}</td>
              <td>{event.source}</td>
              <td>{event.timestamp}</td>
              <td>
                <pre>{JSON.stringify(event.data)}</pre>
              </td>
              <td>
                <button type="button" className="danger" onClick={() => remove(event.id)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {items.length === 0 && <p className="muted">No events stored (or persistence.event_mode excludes them).</p>}

      <div style={{ display: "flex", gap: "8px", marginTop: "12px", alignItems: "center" }}>
        <button
          type="button"
          className="secondary"
          disabled={cursorHistory.length === 0}
          onClick={handlePrevious}
        >
          Previous
        </button>
        <button
          type="button"
          className="secondary"
          disabled={!nextCursor}
          onClick={handleNext}
        >
          Next
        </button>
        {cursorHistory.length > 0 && (
          <span className="muted">Page {cursorHistory.length + 1}</span>
        )}
      </div>
    </div>
  );
}
