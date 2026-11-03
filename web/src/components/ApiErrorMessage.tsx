import type { ApiError } from "../api/client";

/** Shows a project {code,message,detail} error; detail entries are listed as key/value pairs. */
export function ApiErrorMessage({ error }: { error: ApiError }) {
  const detail =
    error.detail && typeof error.detail === "object" && !Array.isArray(error.detail)
      ? Object.entries(error.detail as Record<string, unknown>)
      : [];
  return (
    <div role="alert">
      <p>
        <strong>{error.message}</strong> <code>{error.code}</code>
      </p>
      {detail.length > 0 && (
        <ul>
          {detail.map(([k, v]) => (
            <li key={k}>
              {k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
