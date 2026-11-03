import { useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type ApiError } from "../api/client";
import { ApiErrorMessage } from "../components/ApiErrorMessage";
import { BoundaryPreview } from "../components/BoundaryPreview";
import { validateBoundary, type BoundaryResult } from "../components/boundary";
import { networkError } from "../components/networkError";

export function NewSite() {
  const navigate = useNavigate();
  const ids = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [result, setResult] = useState<BoundaryResult | null>(null);
  const [fileName, setFileName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const [nameError, setNameError] = useState("");

  async function onFile(file: File | undefined) {
    setServerError(null);
    if (!file) {
      setResult(null);
      setFileName("");
      return;
    }
    setFileName(file.name);
    const text = await file.text();
    setResult(validateBoundary(text));
    setName((n) => n || file.name.replace(/\.(geo)?json$/i, "").replace(/[-_]+/g, " "));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setServerError(null);
    if (!name.trim()) {
      setNameError("Enter a site name.");
      return;
    }
    setNameError("");
    if (!result?.ok) {
      fileRef.current?.focus();
      return;
    }
    setSubmitting(true);
    try {
      const { data, error } = await api.POST("/sites", { body: { name: name.trim(), boundary: result.geometry } });
      if (data) navigate(`/sites/${data.id}`);
      else setServerError(error ?? networkError);
    } catch {
      setServerError(networkError);
    } finally {
      setSubmitting(false);
    }
  }

  const fileErr = result && !result.ok ? result.error : "";
  return (
    <>
      <h2>New site</h2>
      <form onSubmit={onSubmit} noValidate>
        <p>
          <label htmlFor={`${ids}-name`}>Site name</label>{" "}
          <input
            id={`${ids}-name`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={!!nameError}
            aria-describedby={nameError ? `${ids}-name-err` : undefined}
            required
          />
          {nameError && (
            <span id={`${ids}-name-err`} role="alert">
              {" "}
              {nameError}
            </span>
          )}
        </p>
        <p>
          <label htmlFor={`${ids}-file`}>Boundary GeoJSON file</label>{" "}
          <input
            id={`${ids}-file`}
            ref={fileRef}
            type="file"
            accept=".geojson,.json,application/geo+json,application/json"
            onChange={(e) => void onFile(e.target.files?.[0])}
            aria-invalid={!!fileErr}
            aria-describedby={`${ids}-hint${fileErr ? ` ${ids}-file-err` : ""}`}
          />
        </p>
        <p id={`${ids}-hint`}>
          A Polygon, MultiPolygon or Feature in WGS 84 (lon/lat), closed rings, under 50,000 ha, inside the supported
          region.
        </p>
        {fileErr && (
          <p id={`${ids}-file-err`} role="alert">
            {fileName}: {fileErr}
          </p>
        )}
        {result?.ok && (
          <section aria-label="Boundary summary">
            <p role="status">
              {fileName}: {result.polygons} polygon{result.polygons === 1 ? "" : "s"}, about{" "}
              {Math.round(result.areaHa).toLocaleString("en-GB")} ha (estimate; the server computes the exact area).
            </p>
            <BoundaryPreview geometry={result.geometry} />
          </section>
        )}
        {serverError && <ApiErrorMessage error={serverError} />}
        <p>
          <button type="submit" disabled={submitting}>
            {submitting ? "Creating..." : "Create site"}
          </button>
        </p>
      </form>
    </>
  );
}
