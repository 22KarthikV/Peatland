/** Client-side GeoJSON boundary validation. The server stays authoritative; this gives fast feedback. */

export type Ring = number[][];
export type BoundaryGeometry =
  | { type: "Polygon"; coordinates: Ring[] }
  | { type: "MultiPolygon"; coordinates: Ring[][] };

export const MAX_AREA_HA = 50_000;
/** Supported region in EPSG:32630 metres (tasks/handoffs/A3.md): MGRS 30U/30V squares F/G plus tile overlap. */
export const REGION_BOUNDS_M = { minE: 295_100, minN: 5_995_100, maxE: 504_900, maxN: 6_204_900 };

export type BoundaryResult =
  | { ok: true; geometry: BoundaryGeometry; areaHa: number; polygons: number }
  | { ok: false; error: string };

const fail = (error: string): BoundaryResult => ({ ok: false, error });

const isPosition = (p: unknown): p is number[] =>
  Array.isArray(p) && p.length >= 2 && p.every((n) => typeof n === "number" && Number.isFinite(n));

function ringProblem(ring: unknown): string | null {
  if (!Array.isArray(ring) || ring.length < 4) return "Each ring needs at least 4 positions.";
  if (!ring.every(isPosition)) return "Positions must be [longitude, latitude] numbers.";
  const r = ring as number[][];
  if (r.some(([lon, lat]) => Math.abs(lon) > 180 || Math.abs(lat) > 90))
    return "Coordinates must be longitude/latitude in WGS 84 (EPSG:4326).";
  const [a, b] = [r[0], r[r.length - 1]];
  if (a[0] !== b[0] || a[1] !== b[1]) return "A ring is not closed: its first and last positions differ.";
  return null;
}

/** Area of a ring in square metres (spherical, WGS 84 mean radius). */
function ringAreaM2(ring: Ring): number {
  const R = 6_371_008.8;
  const rad = Math.PI / 180;
  let sum = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [l1, p1] = ring[i];
    const [l2, p2] = ring[i + 1];
    sum += (l2 - l1) * rad * (2 + Math.sin(p1 * rad) + Math.sin(p2 * rad));
  }
  return Math.abs((sum * R * R) / 2);
}

export function polygonAreaHa(poly: Ring[]): number {
  const [outer, ...holes] = poly;
  const m2 = ringAreaM2(outer) - holes.reduce((s, h) => s + ringAreaM2(h), 0);
  return Math.max(0, m2) / 10_000;
}

/** WGS 84 lon/lat to UTM zone 30N (EPSG:32630), Krueger series. */
export function toUtm30(lon: number, lat: number): [number, number] {
  const a = 6378137;
  const f = 1 / 298.257223563;
  const n = f / (2 - f);
  const A = (a / (1 + n)) * (1 + n ** 2 / 4 + n ** 4 / 64);
  const alpha = [
    n / 2 - (2 * n ** 2) / 3 + (5 * n ** 3) / 16,
    (13 * n ** 2) / 48 - (3 * n ** 3) / 5,
    (61 * n ** 3) / 240,
  ];
  const phi = (lat * Math.PI) / 180;
  const dl = ((lon + 3) * Math.PI) / 180;
  const c = (2 * Math.sqrt(n)) / (1 + n);
  const t = Math.sinh(Math.atanh(Math.sin(phi)) - c * Math.atanh(c * Math.sin(phi)));
  const xi = Math.atan2(t, Math.cos(dl));
  const eta = Math.atanh(Math.sin(dl) / Math.sqrt(1 + t * t));
  let E = eta;
  let N = xi;
  alpha.forEach((al, i) => {
    const j = 2 * (i + 1);
    E += al * Math.cos(j * xi) * Math.sinh(j * eta);
    N += al * Math.sin(j * xi) * Math.cosh(j * eta);
  });
  return [500_000 + 0.9996 * A * E, 0.9996 * A * N];
}

export function allRings(g: BoundaryGeometry): Ring[] {
  return g.type === "Polygon" ? g.coordinates : g.coordinates.flat();
}

export function validateBoundary(text: string): BoundaryResult {
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    return fail("The file is not valid JSON.");
  }
  if (typeof doc !== "object" || doc === null) return fail("The file must contain a GeoJSON object.");
  let geom = doc as { type?: unknown; coordinates?: unknown; geometry?: unknown };
  if (geom.type === "Feature") geom = (geom.geometry ?? {}) as typeof geom;
  else if (geom.type === "FeatureCollection")
    return fail("FeatureCollection is not supported. Upload a single Polygon, MultiPolygon or Feature.");
  if (geom.type !== "Polygon" && geom.type !== "MultiPolygon")
    return fail(`The geometry must be a Polygon or MultiPolygon, not ${String(geom.type ?? "missing")}.`);

  const polys: unknown[] = geom.type === "Polygon" ? [geom.coordinates] : (geom.coordinates as unknown[]);
  if (!Array.isArray(polys) || polys.length === 0 || polys.some((p) => !Array.isArray(p) || p.length === 0))
    return fail("The geometry has no coordinates.");
  for (const poly of polys as unknown[][]) {
    for (const ring of poly) {
      const problem = ringProblem(ring);
      if (problem) return fail(problem);
    }
  }
  const geometry = (
    geom.type === "Polygon"
      ? { type: "Polygon", coordinates: geom.coordinates }
      : { type: "MultiPolygon", coordinates: geom.coordinates }
  ) as BoundaryGeometry;

  const outside = allRings(geometry).some((ring) =>
    ring.some(([lon, lat]) => {
      const [e, n] = toUtm30(lon, lat);
      const b = REGION_BOUNDS_M;
      return e < b.minE || e > b.maxE || n < b.minN || n > b.maxN;
    }),
  );
  if (outside)
    return fail("The boundary is outside the supported region (UTM zone 30, roughly northern England and southern Scotland).");

  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  const areaHa = polygons.reduce((s, p) => s + polygonAreaHa(p), 0);
  if (areaHa > MAX_AREA_HA)
    return fail(
      `The boundary is about ${Math.round(areaHa).toLocaleString("en-GB")} ha; the maximum is ${MAX_AREA_HA.toLocaleString("en-GB")} ha.`,
    );
  return { ok: true, geometry, areaHa, polygons: polygons.length };
}
