import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, type ApiError } from "../api/client";
import { ApiErrorMessage } from "./ApiErrorMessage";
import { networkError } from "./networkError";

/** Minimal new-run entry point: one date window, default model and cloud cover. */
export function RunAnalysis({ siteId }: { siteId: string }) {
  const ids = useId();
  const navigate = useNavigate();
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const invalid = !start || !end ? "Choose a start and an end date." : start > end ? "The start date must not be after the end date." : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (invalid) return;
    setBusy(true);
    setError(null);
    try {
      const { data, error: err } = await api.POST("/sites/{site_id}/runs", {
        params: { path: { site_id: siteId } },
        body: { windows: [{ start, end }], max_cloud_cover_pct: 60 },
      });
      if (data) navigate(`/runs/${data.id}`);
      else {
        setError(err ?? networkError);
        setBusy(false);
      }
    } catch {
      setError(networkError);
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} aria-labelledby={`${ids}-h`} noValidate>
      <h3 id={`${ids}-h`}>Run analysis</h3>
      <label htmlFor={`${ids}-start`}>Start date</label>{" "}
      <input id={`${ids}-start`} type="date" value={start} onChange={(e) => setStart(e.target.value)} />{" "}
      <label htmlFor={`${ids}-end`}>End date</label>{" "}
      <input id={`${ids}-end`} type="date" value={end} onChange={(e) => setEnd(e.target.value)} />{" "}
      <button type="submit" disabled={busy}>
        {busy ? "Starting..." : "Run analysis"}
      </button>
      {start && end && invalid && <p role="alert">{invalid}</p>}
      {error && <ApiErrorMessage error={error} />}
    </form>
  );
}
