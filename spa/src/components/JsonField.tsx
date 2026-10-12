import { useState } from "react";

/** A JSON-source textarea with inline parse-error feedback, used for rule/action bodies and event data. */
export function JsonField({
  label,
  value,
  onChange,
  rows = 5,
}: {
  label: string;
  value: string;
  onChange: (text: string, parsed: unknown, valid: boolean) => void;
  rows?: number;
}) {
  const [error, setError] = useState<string | null>(null);

  function handleChange(text: string) {
    try {
      const parsed = text.trim() ? JSON.parse(text) : undefined;
      setError(null);
      onChange(text, parsed, true);
    } catch (err) {
      setError((err as Error).message);
      onChange(text, undefined, false);
    }
  }

  return (
    <label>
      {label}
      <textarea rows={rows} value={value} onChange={(e) => handleChange(e.target.value)} spellCheck={false} />
      {error && <div className="error-banner">{error}</div>}
    </label>
  );
}
