import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { DateSelect } from "../components/DateSelect";
import { Legend } from "../components/Legend";
import { MapView } from "../components/MapView";
import { RunAnalysis } from "../components/RunAnalysis";
import { fetchClassAt, type PointResult } from "../lib/tiles";

type SiteDetail = components["schemas"]["SiteDetail"];
type Classification = components["schemas"]["Classification"];

const HOVER_DELAY_MS = 150;

export function SiteMap() {
  const { siteId = "" } = useParams();
  const [result, setResult] = useState<{
    siteId: string;
    site: SiteDetail | null;
    items: Classification[];
    error: string | null;
  } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [opacity, setOpacity] = useState(0.8);
  const [hover, setHover] = useState<PointResult | "loading" | null>(null);
  const hoverSeq = useRef(0);
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
        } else {
          const list = c.data.items ?? [];
          setResult({ siteId, site: s.data, items: list, error: null });
          setSelected(list.length ? list[list.length - 1].id : null);
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

  const loading = result?.siteId !== siteId;
  const site = result?.site ?? null;
  const error = result?.error ?? null;
  const items = useMemo(() => result?.items ?? [], [result]);
  const options = useMemo(
    () =>
      items.map((c) => ({
        id: c.id,
        label: c.window.start === c.window.end ? c.window.start : `${c.window.start} to ${c.window.end}`,
      })),
    [items],
  );
  const current = items.find((c) => c.id === selected) ?? null;

  const onHover = (pos: { lng: number; lat: number } | null) => {
    clearTimeout(timer.current);
    const seq = ++hoverSeq.current;
    if (!pos || !current) {
      setHover(null);
      return;
    }
    timer.current = setTimeout(async () => {
      setHover("loading");
      try {
        const r = await fetchClassAt(current.tile_url, pos.lng, pos.lat);
        if (seq === hoverSeq.current) setHover(r);
      } catch {
        if (seq === hoverSeq.current) setHover(null);
      }
    }, HOVER_DELAY_MS);
  };

  if (loading) return <p role="status">Loading site...</p>;
  if (error || !site) return <p role="alert">{error ?? "Site not found."}</p>;

  const hoverText =
    hover === null
      ? "Move the pointer over the map to see the class."
      : hover === "loading"
        ? "Looking up class..."
        : hover.kind === "class"
          ? hover.cls.label
          : "No data";

  return (
    <section>
      <h2>{site.name}</h2>
      {items.length === 0 && <p role="status">No classifications yet for this site.</p>}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
        <DateSelect options={options} value={selected} onChange={setSelected} />
        <div>
          <label htmlFor="opacity">Opacity </label>
          <input
            id="opacity"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))}
            aria-valuetext={`${Math.round(opacity * 100)}%`}
          />
        </div>
        <p role="status" aria-live="polite" data-testid="hover-class" style={{ margin: 0 }}>
          {hoverText}
        </p>
      </div>
      <MapView
        boundary={site.boundary as unknown as { type: string; coordinates?: unknown }}
        tileUrl={current?.tile_url ?? null}
        opacity={opacity}
        onHover={onHover}
      />
      <Legend />
      <RunAnalysis siteId={siteId} />
    </section>
  );
}
