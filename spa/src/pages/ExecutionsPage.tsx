import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { Execution } from "../types";
import { StatusBadge } from "../components/StatusBadge";
import { createRequestGate, fetchGuarded } from "../requestGate";

const PAGE_SIZE = 50;

export default function ExecutionsPage() {
  const [items, setItems] = useState<Execution[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [cursors, setCursors] = useState<(string | null)[]>([null]); // cursors[i] is the cursor used to fetch page i
  const [page, setPage] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const pageRef = useRef(page);
  pageRef.current = page;
  // One gate for ALL list loads: the newest load (refresh, pagination, or filter) always wins;
  // any response that resolves after a newer load was issued is discarded as stale.
  const listGate = useRef(createRequestGate()).current;

  async function load(cursor: string | null, statusFilter: string) {
    try {
      setError(null);
      const data = await fetchGuarded(listGate, () =>
        api.listExecutions({
          status: statusFilter || undefined,
          limit: PAGE_SIZE,
          cursor: cursor ?? undefined,
        }),
      );
      if (data === null) return; // stale response — a newer load already superseded this one
      setItems(data.items);
      setNextCursor(data.next_cursor);
      setHasMore(data.has_more);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    // Filters changed: outstanding loads are invalidated, pagination resets, refresh restarts.
    listGate.invalidate();
    setPage(0);
    setCursors([null]);
    setSelectedId(null);
    void load(null, status);
    const interval = setInterval(() => {
      // Refresh only the first page; browsing older history must not be yanked back to page 1.
      if (pageRef.current === 0) void load(null, status);
    }, 4000);
    return () => {
      clearInterval(interval);
      listGate.invalidate(); // unmount / filter change: in-flight loads can no longer apply
    };
  }, [status]);

  async function goNext() {
    if (!nextCursor) return;
    const target = page + 1;
    setCursors((prev) => {
      const c = prev.slice(0, target);
      c[target] = nextCursor;
      return c;
    });
    setPage(target);
    await load(nextCursor, status);
  }

  async function goPrev() {
    if (page === 0) return;
    const target = page - 1;
    setPage(target);
    await load(cursors[target], status);
  }

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
        <button className="secondary" onClick={() => void load(cursors[page], status)}>
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
            <tr key={execution.id} className="clickable" onClick={() => setSelectedId(execution.id)}>
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
      <div className="pagination-bar">
        <button className="secondary" disabled={page === 0} onClick={() => void goPrev()}>
          &larr; Previous
        </button>
        <span className="muted">Page {page + 1}</span>
        <button className="secondary" disabled={!hasMore || !nextCursor} onClick={() => void goNext()}>
          Next &rarr;
        </button>
      </div>
      {selectedId && <ExecutionDetail id={selectedId} onClose={() => setSelectedId(null)} />}
    </div>
  );
}

function ExecutionDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const [detail, setDetail] = useState<Execution | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  // Gate per mounted instance: a slower fetch for a previous selection can never overwrite
  // the detail of the selection the user made afterwards.
  const gate = useRef(createRequestGate()).current;

  useEffect(() => {
    setDetail(null);
    setDetailError(null);
    fetchGuarded(gate, () => api.getExecution(id))
      .then((d) => {
        if (d !== null) setDetail(d);
      })
      .catch((err) => setDetailError(err instanceof ApiError ? err.message : String(err)));
    return () => gate.invalidate();
  }, [id]);

  if (detailError) {
    return (
      <div className="panel">
        <div className="error-banner">{detailError}</div>
        <button className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="panel">
        <p className="muted">Loading execution {id}&hellip;</p>
      </div>
    );
  }

  const fields: [string, React.ReactNode][] = [
    ["ID", detail.id],
    ["Status", <StatusBadge key="s" value={detail.status} />],
    ["Trigger", detail.trigger],
    ["Action", detail.action_id],
    ["Rule", detail.rule_id || "-"],
    ["Event", detail.event_id || "-"],
    ["Action revision", detail.action_revision || "-"],
    ["Created", detail.created_at],
    ["Started", detail.started_at || "-"],
    ["Finished", detail.finished_at || "-"],
    ["Duration", detail.duration_ms != null ? `${detail.duration_ms}ms` : "-"],
    ["Exit code", detail.exit_code != null ? String(detail.exit_code) : "-"],
    ["HTTP status", detail.http_status != null ? String(detail.http_status) : "-"],
    ["Error", detail.error_code || detail.error_message ? `${detail.error_code || ""} ${detail.error_message || ""}`.trim() : "-"],
  ];

  return (
    <div className="panel">
      <h3>
        Execution {detail.id}{" "}
        <button className="secondary" onClick={onClose}>
          Close
        </button>
      </h3>
      <dl className="detail-grid">
        {fields.map(([label, value]) => (
          <span key={label} style={{ display: "contents" }}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </span>
        ))}
      </dl>
      {detail.stdout && (
        <>
          <h4>stdout</h4>
          <pre className="scrollable-pre">{detail.stdout}</pre>
        </>
      )}
      {detail.stderr && (
        <>
          <h4>stderr</h4>
          <pre className="scrollable-pre">{detail.stderr}</pre>
        </>
      )}
      {detail.result != null && (
        <>
          <h4>result</h4>
          <pre className="scrollable-pre">{JSON.stringify(detail.result, null, 2)}</pre>
        </>
      )}
    </div>
  );
}
