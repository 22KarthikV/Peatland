import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { DateSelect } from "../components/DateSelect";
import { Legend } from "../components/Legend";
import { MapView, type Camera } from "../components/MapView";
import { StatsPanel } from "../components/StatsPanel";

type SiteDetail = components["schemas"]["SiteDetail"];
type Classification = components["schemas"]["Classification"];
type ChangeStats = components["schemas"]["ChangeStats"];
type Transition = components["schemas"]["Transition"];
type Run = components["schemas"]["Run"];

/** Poll interval for a queued/running change run. Mutable so tests can shorten it. */
export const pollConfig = { ms: 2000 };

type Compare = "swipe" | "side";
type ChangeState =
  | { kind: "idle" }
  | { kind: "pending"; status: string }
  | { kind: "failed"; message: string }
  | { kind: "ready"; stats: ChangeStats };

const dateLabel = (c: Classification) =>
  c.window.start === c.window.end ? c.window.start : `${c.window.start} to ${c.window.end}`;
const fmt = (n: number) => n.toLocaleString("en-GB", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const boundaryOf = (s: SiteDetail) => s.boundary as unknown as { type: string; coordinates?: unknown };

export function Change() {
  const { siteId = "" } = useParams();
  const [result, setResult] = useState<{
    siteId: string;
    site: SiteDetail | null;
    items: Classification[];
    error: string | null;
  } | null>(null);
  const [fromId, setFromId] = useState<string | null>(null);
  const [toId, setToId] = useState<string | null>(null);
  const [mode, setMode] = useState<Compare>("swipe");
  const [swipe, setSwipe] = useState(50);
  const [camera, setCamera] = useState<Camera | undefined>(undefined);
  const [change, setChange] = useState<ChangeState>({ kind: "idle" });
  const [showChange, setShowChange] = useState(false);
  const [picked, setPicked] = useState<Transition | null>(null);
  const seq = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, c] = await Promise.all([
          api.GET("/sites/{site_id}", { params: { path: { site_id: siteId } } }),
          api.GET("/sites/{site_id}/classifications", { params: { path: { site_id: siteId } } }),
        ]);
        if (cancelled) return;
        if (!s.data || !c.data) {
          setResult({ siteId, site: null, items: [], error: (s.error ?? c.error)?.message ?? "Could not load the site." });
          return;
        }
        const list = [...(c.data.items ?? [])].sort((a, b) => a.window.start.localeCompare(b.window.start));
        setResult({ siteId, site: s.data, items: list, error: null });
        if (list.length >= 2) {
          setFromId(list[list.length - 2].id);
          setToId(list[list.length - 1].id);
        } else if (list.length === 1) {
          setFromId(list[0].id);
          setToId(list[0].id);
        }
      } catch {
        if (!cancelled) setResult({ siteId, site: null, items: [], error: "Could not reach the server." });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [siteId]);

  useEffect(() => () => clearTimeout(timer.current), []);

  const items = useMemo(() => result?.items ?? [], [result]);
  const options = useMemo(() => items.map((c) => ({ id: c.id, label: dateLabel(c) })), [items]);
  const from = items.find((c) => c.id === fromId) ?? null;
  const to = items.find((c) => c.id === toId) ?? null;
  const orderOk = !!from && !!to && from.window.start < to.window.start;

  const reset = useCallback(() => {
    seq.current++;
    clearTimeout(timer.current);
    setChange({ kind: "idle" });
    setShowChange(false);
    setPicked(null);
  }, []);

  const pickFrom = (id: string) => {
    setFromId(id);
    reset();
  };
  const pickTo = (id: string) => {
    setToId(id);
    reset();
  };

  const loadStats = useCallback(async (changeId: string, mine: number) => {
    const r = await api.GET("/changes/{change_id}/stats", { params: { path: { change_id: changeId } } });
    if (mine !== seq.current) return;
    if (r.data && typeof r.data === "object") setChange({ kind: "ready", stats: r.data });
    else setChange({ kind: "failed", message: r.error?.message ?? "Could not load the change statistics." });
  }, []);

  const poll = useCallback(
    async (runId: string, mine: number) => {
      for (;;) {
        await new Promise<void>((resolve) => {
          timer.current = setTimeout(resolve, pollConfig.ms);
        });
        if (mine !== seq.current) return;
        try {
          const r = await api.GET("/runs/{run_id}", { params: { path: { run_id: runId } } });
          if (mine !== seq.current) return;
          const run: Run | undefined = r.data;
          if (!run) {
            setChange({ kind: "failed", message: r.error?.message ?? "Could not check the change run." });
          } else if (run.status === "succeeded") {
            if (run.outputs.change_id) await loadStats(run.outputs.change_id, mine);
            else setChange({ kind: "failed", message: "The run finished but produced no change." });
          } else if (run.status === "failed" || run.status === "cancelled") {
            setChange({ kind: "failed", message: run.error?.message ?? `The change run ${run.status}.` });
          } else {
            setChange({ kind: "pending", status: run.status });
            continue;
          }
        } catch {
          if (mine === seq.current) setChange({ kind: "failed", message: "Could not reach the server." });
        }
        return;
      }
    },
    [loadStats],
  );

  const compute = async () => {
    if (!from || !to || !orderOk) return;
    reset();
    const mine = seq.current;
    setChange({ kind: "pending", status: "queued" });
    try {
      const r = await api.POST("/sites/{site_id}/changes", {
        params: { path: { site_id: siteId } },
        body: {
          from: { year: from.window.year, season: from.window.season },
          to: { year: to.window.year, season: to.window.season },
        },
      });
      if (mine !== seq.current) return;
      if (r.response.status === 202 && r.data && "kind" in r.data) {
        void poll(r.data.id, mine);
      } else if (r.data && "tile_url" in r.data) {
        await loadStats(r.data.id, mine);
      } else {
        setChange({ kind: "failed", message: r.error?.message ?? "Could not compute the change." });
      }
    } catch {
      if (mine === seq.current) setChange({ kind: "failed", message: "Could not reach the server." });
    }
  };

  if (!result || result.siteId !== siteId) return <p role="status">Loading site...</p>;
  if (result.error || !result.site) return <p role="alert">{result.error ?? "Site not found."}</p>;
  const site = result.site;
  if (items.length === 0) {
    return (
      <section>
        <h2>{site.name}: change</h2>
        <p role="status">No classifications yet for this site, so there is nothing to compare.</p>
      </section>
    );
  }

  const stats = change.kind === "ready" ? change.stats : null;
  const layerOn = showChange && !!stats;

  return (
    <section>
      <h2>{site.name}: change</h2>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "end" }}>
        <DateSelect options={options} value={fromId} onChange={pickFrom} label="From" />
        <DateSelect options={options} value={toId} onChange={pickTo} label="To" />
        <fieldset style={{ border: 0, padding: 0, margin: 0 }} disabled={layerOn}>
          <legend>Comparison</legend>
          <label>
            <input type="radio" name="compare" checked={mode === "swipe"} onChange={() => setMode("swipe")} /> Swipe
          </label>{" "}
          <label>
            <input type="radio" name="compare" checked={mode === "side"} onChange={() => setMode("side")} /> Side by side
          </label>
        </fieldset>
        <button type="button" onClick={compute} disabled={!orderOk || change.kind === "pending"}>
          Compute change
        </button>
        <label>
          <input type="checkbox" checked={layerOn} disabled={!stats} onChange={(e) => setShowChange(e.target.checked)} /> Show
          change layer
        </label>
      </div>
      {!orderOk && <p role="alert">Choose a From date that is earlier than the To date.</p>}

      {change.kind === "pending" && <p role="status">Computing change ({change.status})...</p>}
      {change.kind === "failed" && <p role="alert">Change failed: {change.message}</p>}

      {layerOn && stats ? (
        <MapView
          boundary={boundaryOf(site)}
          tileUrl={stats.change.tile_url}
          opacity={0.8}
          camera={camera}
          onCameraChange={setCamera}
          label="Change map"
        />
      ) : mode === "side" ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 8 }}>
          <MapView boundary={boundaryOf(site)} tileUrl={from?.tile_url ?? null} opacity={0.8} camera={camera} onCameraChange={setCamera} label="From map" />
          <MapView boundary={boundaryOf(site)} tileUrl={to?.tile_url ?? null} opacity={0.8} camera={camera} onCameraChange={setCamera} label="To map" />
        </div>
      ) : (
        <div>
          <div style={{ position: "relative" }}>
            <MapView boundary={boundaryOf(site)} tileUrl={from?.tile_url ?? null} opacity={0.8} camera={camera} onCameraChange={setCamera} label="From map" />
            <div
              data-testid="swipe-overlay"
              style={{ position: "absolute", inset: 0, pointerEvents: "none", clipPath: `inset(0 0 0 ${swipe}%)` }}
            >
              <MapView boundary={boundaryOf(site)} tileUrl={to?.tile_url ?? null} opacity={0.8} camera={camera} interactive={false} label="To map" />
            </div>
          </div>
          <label>
            Swipe position{" "}
            <input
              type="range"
              min={0}
              max={100}
              value={swipe}
              onChange={(e) => setSwipe(Number(e.target.value))}
              aria-valuetext={`${swipe}% from, ${100 - swipe}% to`}
            />
          </label>
          <p style={{ margin: 0 }}>
            Left of the slider: {from ? dateLabel(from) : ""}. Right: {to ? dateLabel(to) : ""}.
          </p>
        </div>
      )}
      <Legend />

      {stats && (
        <section aria-label="Transitions">
          <h3>Transitions</h3>
          <p>
            {stats.total_ha.toLocaleString("en-GB")} ha valid area. <a href={stats.change.download_url}>Download change raster</a>
          </p>
          <ul style={{ listStyle: "none", padding: 0 }}>
            {stats.transitions.map((t) => {
              const key = `${t.from_class.id}-${t.to_class.id}`;
              const active = !!picked && `${picked.from_class.id}-${picked.to_class.id}` === key;
              return (
                <li key={key}>
                  <button type="button" aria-pressed={active} onClick={() => setPicked(t)}>
                    {t.from_class.label} to {t.to_class.label}: {fmt(t.area_ha)} ha
                  </button>
                </li>
              );
            })}
          </ul>
          <div role="status" aria-live="polite" data-testid="transition-detail">
            {picked ? (
              <>
                <strong>
                  {picked.from_class.label} to {picked.to_class.label}
                </strong>
                <br />
                {picked.from_class.id === picked.to_class.id ? "Unchanged. " : ""}
                {fmt(picked.area_ha)} ha, {fmt(picked.percent)}% of the site
              </>
            ) : (
              "Select a transition to see its detail."
            )}
          </div>
        </section>
      )}
      {stats && <StatsPanel stats={stats} />}
    </section>
  );
}
