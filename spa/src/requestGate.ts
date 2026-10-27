/** Monotonic request sequencer: only the most recently issued request is "current". Any response
 * carrying an older id is stale and must be discarded instead of applied to component state.
 * `invalidate()` retires every outstanding id (filter change / unmount / new selection). */
export interface RequestGate {
  issue(): number;
  isCurrent(id: number): boolean;
  invalidate(): void;
}

export function createRequestGate(): RequestGate {
  let current = 0;
  return {
    issue: () => ++current,
    isCurrent: (id) => id === current,
    invalidate: () => void ++current,
  };
}

/** Runs `fn` as a gated request: resolves to the result only if still current, else null; errors
 * are re-thrown only when current (stale failures never surface as visible errors). */
export async function fetchGuarded<T>(gate: RequestGate, fn: () => Promise<T>): Promise<T | null> {
  const id = gate.issue();
  try {
    const result = await fn();
    return gate.isCurrent(id) ? result : null;
  } catch (err) {
    if (gate.isCurrent(id)) throw err;
    return null;
  }
}
