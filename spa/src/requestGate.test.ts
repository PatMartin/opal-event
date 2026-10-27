import { describe, expect, it } from "vitest";
import { createRequestGate, fetchGuarded } from "./requestGate";

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("fetchGuarded", () => {
  it("drops an older page response that resolves after a newer one", async () => {
    const gate = createRequestGate();
    const slow = deferred<string>();
    const fast = deferred<string>();
    const a = fetchGuarded(gate, () => slow.promise); // e.g. page-1 refresh
    const b = fetchGuarded(gate, () => fast.promise); // e.g. page-2 navigation
    fast.resolve("newer");
    expect(await b).toBe("newer");
    slow.resolve("older"); // resolves last but is already superseded
    expect(await a).toBeNull();
  });

  it("invalidates in-flight requests when filters change", async () => {
    const gate = createRequestGate();
    const pending = deferred<string>();
    const stale = fetchGuarded(gate, () => pending.promise);
    gate.invalidate(); // user changed the status filter
    const fresh = fetchGuarded(gate, () => Promise.resolve("filtered"));
    pending.resolve("unfiltered");
    expect(await stale).toBeNull();
    expect(await fresh).toBe("filtered");
  });

  it("keeps pagination winning over a slower refresh", async () => {
    const gate = createRequestGate();
    const refresh = deferred<string>();
    const refreshResult = fetchGuarded(gate, () => refresh.promise); // 4s auto-refresh fires
    const nav = fetchGuarded(gate, () => Promise.resolve("page-3")); // user clicks Next meanwhile
    expect(await nav).toBe("page-3");
    refresh.resolve("page-1");
    expect(await refreshResult).toBeNull(); // refresh must not yank the user back
  });

  it("keeps only the newest detail selection", async () => {
    const gate = createRequestGate();
    const first = deferred<string>();
    const d1 = fetchGuarded(gate, () => first.promise); // click execution A
    const d2 = fetchGuarded(gate, () => Promise.resolve("exec-B")); // click execution B quickly
    expect(await d2).toBe("exec-B");
    first.resolve("exec-A");
    expect(await d1).toBeNull();
  });

  it("swallows results and errors after unmount-style invalidation", async () => {
    const gate = createRequestGate();
    const pending = deferred<string>();
    const failing = deferred<string>();
    const ok = fetchGuarded(gate, () => pending.promise);
    const bad = fetchGuarded(gate, () => failing.promise);
    gate.invalidate(); // component unmounted
    pending.resolve("late");
    failing.reject(new Error("late error"));
    expect(await ok).toBeNull();
    expect(await bad).toBeNull(); // stale error is discarded, not thrown
  });

  it("re-throws errors for the still-current request", async () => {
    const gate = createRequestGate();
    const failing = fetchGuarded(gate, () => Promise.reject(new Error("boom")));
    await expect(failing).rejects.toThrow("boom");
  });
});
