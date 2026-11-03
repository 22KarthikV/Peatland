import { allRings, type BoundaryGeometry } from "./boundary";

/** Small SVG outline of the boundary (equirectangular, scaled by cos(latitude)). */
export function BoundaryPreview({ geometry }: { geometry: BoundaryGeometry }) {
  const rings = allRings(geometry);
  const pts = rings.flat();
  const lons = pts.map((p) => p[0]);
  const lats = pts.map((p) => p[1]);
  const [minLon, maxLon, minLat, maxLat] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)];
  const k = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  const w = Math.max((maxLon - minLon) * k, 1e-9);
  const h = Math.max(maxLat - minLat, 1e-9);
  const size = 240;
  const s = (size - 16) / Math.max(w, h);
  const x = (lon: number) => 8 + (lon - minLon) * k * s;
  const y = (lat: number) => 8 + (maxLat - lat) * s;
  const d = rings
    .map((r) => "M" + r.map(([lon, lat]) => `${x(lon).toFixed(1)},${y(lat).toFixed(1)}`).join("L") + "Z")
    .join("");
  return (
    <svg role="img" aria-label="Boundary preview" viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
      <rect width={size} height={size} fill="none" stroke="currentColor" strokeOpacity="0.3" />
      <path d={d} fill="currentColor" fillOpacity="0.25" stroke="currentColor" fillRule="evenodd" />
    </svg>
  );
}
