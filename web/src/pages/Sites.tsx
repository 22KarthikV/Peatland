import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError } from "../api/client";
import type { components } from "../api/schema";
import { ApiErrorMessage } from "../components/ApiErrorMessage";
import { networkError } from "../components/networkError";

type SiteSummary = components["schemas"]["SiteSummary"];
type State =
  | { kind: "loading" }
  | { kind: "error"; error: ApiError }
  | { kind: "ready"; items: SiteSummary[] };

export function Sites() {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    const timer = setTimeout(
      async () => {
        setState({ kind: "loading" });
        try {
          const q = query.trim();
          const { data, error } = await api.GET("/sites", {
            params: { query: q ? { q } : {} },
            signal: ctrl.signal,
          });
          if (ctrl.signal.aborted) return;
          setState(data ? { kind: "ready", items: data.items } : { kind: "error", error: error ?? networkError });
        } catch {
          if (!ctrl.signal.aborted) setState({ kind: "error", error: networkError });
        }
      },
      query ? 250 : 0,
    );
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [query, attempt]);

  const q = query.trim();
  return (
    <>
      <h2>Sites</h2>
      <p>
        <label>
          Search sites <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
      </p>
      <div aria-live="polite">
        {state.kind === "loading" && <p role="status">Loading sites...</p>}
        {state.kind === "error" && (
          <>
            <ApiErrorMessage error={state.error} />
            <button type="button" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </>
        )}
        {state.kind === "ready" && state.items.length === 0 && (
          <p>
            {q ? `No sites match "${q}".` : "No sites yet."} {!q && <Link to="/sites/new">Add your first site</Link>}
          </p>
        )}
        {state.kind === "ready" && state.items.length > 0 && (
          <ul aria-label="Sites">
            {state.items.map((s) => (
              <li key={s.id}>
                <Link to={`/sites/${s.id}`}>{s.name}</Link> ({s.slug}), {s.area_ha.toLocaleString("en-GB")} ha
                {s.latest_run && <>, latest run: {s.latest_run.status}</>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
