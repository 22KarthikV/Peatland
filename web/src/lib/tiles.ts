import { classById, NODATA, type LandClass } from "./classes";

/** Builds the TiTiler point URL on the same origin and COG as a classification `tile_url`. */
export function pointUrl(tileUrl: string, lon: number, lat: number): string {
  const u = new URL(tileUrl.replace("{z}", "0").replace("{x}", "0").replace("{y}", "0"));
  const cog = u.searchParams.get("url");
  if (!cog) throw new Error("tile_url has no url parameter");
  return `${u.origin}/cog/point/${lon},${lat}?url=${encodeURIComponent(cog)}`;
}

export type PointResult = { kind: "class"; cls: LandClass } | { kind: "nodata" };

/** Class under a coordinate. Outside the raster or nodata gives `nodata`; other failures throw. */
export async function fetchClassAt(tileUrl: string, lon: number, lat: number): Promise<PointResult> {
  const res = await fetch(pointUrl(tileUrl, lon, lat));
  if (res.status === 404 || res.status === 204) return { kind: "nodata" };
  if (!res.ok) throw new Error(`point lookup failed (${res.status})`);
  const body = (await res.json()) as { values?: Array<number | null> };
  const v = body.values?.[0];
  if (v === null || v === undefined || v === NODATA) return { kind: "nodata" };
  const cls = classById(v);
  return cls ? { kind: "class", cls } : { kind: "nodata" };
}
