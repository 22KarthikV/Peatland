import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError } from "../api/client";
import type { components } from "../api/schema";
import { ApiErrorMessage } from "../components/ApiErrorMessage";
import { networkError } from "../components/networkError";

type Run = components["schemas"]["Run"];
type RunCreate = components["schemas"]["RunCreate"];

/** Poll delay starts at `ms` and grows by `factor` after every poll, up to `maxMs`. Mutable so tests can shorten it. */
export const pollConfig = { ms: 2000, factor: 1.5, maxMs: 15000, maxFailures: 3 };

const TERMINAL: ReadonlyArray<Run["status"]> = ["succeeded", "failed", "cancelled"];
export const isTerminal = (s: Run["status"]) => TERMINAL.includes(s);

type State =
  | { kind: "loading" }
  | { kind: "ready"; run: Run }
  | { kind: "error"; error: ApiError };

const fmtTime = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString("en-GB") : "not yet");

export function RunStatus() {
  const { runId = "" } = useParams();
  // Keyed so that moving to another run (after Retry) starts from a clean state.
  return <RunView key={runId} runId={runId} />;
}

function RunView({ runId }: { runId: string }) {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [retry, setRetry] = useState<{ busy: boolean; error: ApiError | null }>({ busy: false, error: null });
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = pollConfig.ms;
    let failures = 0;

    const tick = async () => {
      try {
        const { data, error } = await api.GET("/runs/{run_id}", {
          params: { path: { run_id: runId } },
          signal: ctrl.signal,
        });
        if (ctrl.signal.aborted) return;
        if (data) {
          failures = 0;
          setState({ kind: "ready", run: data });
          if (isTerminal(data.status)) return;
        } else {
          setState({ kind: "error", error: error ?? networkError });
          return; // API errors (404, 401, ...) will not fix themselves
        }
      } catch {
        if (ctrl.signal.aborted) return;
        if (++failures >= pollConfig.maxFailures) {
          setState({ kind: "error", error: networkError });
          return;
        }
      }
      timer = setTimeout(tick, delay);
      delay = Math.min(delay * pollConfig.factor, pollConfig.maxMs);
    };
    void tick();

    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [runId, attempt]);

  const onRetry = useCallback(
    async (run: Run) => {
      setRetry({ busy: true, error: null });
      try {
        const body = run.params as unknown as RunCreate;
        const { data, error } = await api.POST("/sites/{site_id}/runs", {
          params: { path: { site_id: run.site_id } },
          body,
        });
        if (!alive.current) return;
        if (data) {
          setRetry({ busy: false, error: null });
          navigate(`/runs/${data.id}`);
        } else {
          setRetry({ busy: false, error: error ?? networkError });
        }
      } catch {
        if (alive.current) setRetry({ busy: false, error: networkError });
      }
    },
    [navigate],
  );

  return (
    <section aria-labelledby="run-heading">
      <h2 id="run-heading">Run status</h2>
      {state.kind === "loading" && <p role="status">Loading run...</p>}
      {state.kind === "error" && (
        <>
          <ApiErrorMessage error={state.error} />
          <button type="button" onClick={() => {
              setState({ kind: "loading" });
              setAttempt((n) => n + 1);
            }}>
            Try again
          </button>
        </>
      )}
      {state.kind === "ready" && <RunBody run={state.run} onRetry={onRetry} retry={retry} />}
    </section>
  );
}

function RunBody({
  run,
  onRetry,
  retry,
}: {
  run: Run;
  onRetry: (run: Run) => void;
  retry: { busy: boolean; error: ApiError | null };
}) {
  const { step, done, total } = run.progress;
  const hasProgress = typeof done === "number" && typeof total === "number" && total > 0;
  const active = !isTerminal(run.status);
  const progressText = hasProgress
    ? `${step ?? "Step"}: ${done} of ${total}`
    : active
      ? "Waiting for progress..."
      : "No progress recorded.";

  return (
    <>
      <p>
        Run <code>{run.id}</code> ({run.kind}, model {run.model.name} {run.model.version})
      </p>
      <div aria-live="polite" aria-atomic="true" data-testid="run-live">
        <p>
          Status: <strong>{run.status}</strong>
        </p>
        <p>{progressText}</p>
      </div>
      {hasProgress && (
        <p>
          <label htmlFor="run-progress">Progress</label>{" "}
          <progress id="run-progress" max={total} value={done}>
            {done} of {total}
          </progress>
        </p>
      )}
      <dl>
        <dt>Created</dt>
        <dd>{fmtTime(run.created_at)}</dd>
        <dt>Started</dt>
        <dd>{fmtTime(run.started_at)}</dd>
        <dt>Finished</dt>
        <dd>{fmtTime(run.finished_at)}</dd>
      </dl>

      {run.status === "failed" &&
        (run.error ? (
          <ApiErrorMessage error={run.error as ApiError} />
        ) : (
          <p role="alert">The run failed without an error message.</p>
        ))}
      {run.status === "cancelled" && <p role="alert">This run was cancelled.</p>}
      {(run.status === "failed" || run.status === "cancelled") && (
        <p>
          <button type="button" onClick={() => onRetry(run)} disabled={retry.busy}>
            {retry.busy ? "Retrying..." : "Retry"}
          </button>
        </p>
      )}
      {retry.error && <ApiErrorMessage error={retry.error} />}

      {run.status === "succeeded" && (
        <p>
          <Link to={`/sites/${run.site_id}`}>View the site map</Link>
          {run.outputs.change_id && (
            <>
              {" "}
              <Link to={`/sites/${run.site_id}/change`}>View the change</Link>
            </>
          )}
        </p>
      )}
    </>
  );
}
