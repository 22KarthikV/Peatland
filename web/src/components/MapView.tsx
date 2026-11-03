import { LngLatBounds, Map as MapLibreMap, NavigationControl, type GeoJSONSourceSpecification, type MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";

/**
 * Base map: OpenStreetMap raster tiles (no key). Attribution is shown by MapLibre's control.
 * Note the OSM tile usage policy: fine for development and demos, use a hosted provider for real traffic.
 */
export const OSM_ATTRIBUTION = "© OpenStreetMap contributors";
const BOUNDARY_SRC = "boundary";
const CLASS_SRC = "classification";

export interface MapViewProps {
  boundary: { type: string; coordinates?: unknown } | null;
  tileUrl: string | null;
  opacity: number;
  onHover?: (pos: { lng: number; lat: number } | null) => void;
  /** Shared camera for synchronised views. Applied with jumpTo when it differs from the map's own. */
  camera?: Camera;
  /** Called with the new camera when the user (not code) moves this map. */
  onCameraChange?: (camera: Camera) => void;
  /** False makes the map ignore the pointer (used for the clipped overlay in swipe mode). */
  interactive?: boolean;
  label?: string;
}

export interface Camera {
  center: [number, number];
  zoom: number;
  bearing: number;
  pitch: number;
}

/** Thin MapLibre wrapper. Everything else on the page is plain React and tested without the map. */
export function MapView({ boundary, tileUrl, opacity, onHover, camera, onCameraChange, interactive = true, label = "Site map" }: MapViewProps) {
  const box = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const hoverRef = useRef(onHover);
  const opacityRef = useRef(opacity);
  const cameraCbRef = useRef(onCameraChange);
  useEffect(() => {
    hoverRef.current = onHover;
    opacityRef.current = opacity;
    cameraCbRef.current = onCameraChange;
  });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!box.current) return;
    const map = new MapLibreMap({
      container: box.current,
      center: [-4.4, 55.15],
      zoom: 9,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            maxzoom: 19,
            attribution: OSM_ATTRIBUTION,
          },
        },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      },
    });
    mapRef.current = map;
    map.addControl(new NavigationControl(), "top-right");
    map.on("load", () => setReady(true));
    map.on("mousemove", (e: MapMouseEvent) => hoverRef.current?.({ lng: e.lngLat.lng, lat: e.lngLat.lat }));
    map.on("mouseout", () => hoverRef.current?.(null));
    map.on("move", (e: { originalEvent?: unknown }) => {
      if (!e.originalEvent || !cameraCbRef.current) return;
      const c = map.getCenter();
      cameraCbRef.current({ center: [c.lng, c.lat], zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });
    });
    return () => {
      setReady(false);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !boundary) return;
    map.addSource(BOUNDARY_SRC, { type: "geojson", data: { type: "Feature", properties: {}, geometry: boundary } as unknown as GeoJSONSourceSpecification["data"] });
    map.addLayer({
      id: "boundary-line",
      type: "line",
      source: BOUNDARY_SRC,
      paint: { "line-color": "#000000", "line-width": 2 },
    });
    const pts: [number, number][] = [];
    const walk = (c: unknown): void => {
      if (Array.isArray(c) && typeof c[0] === "number") pts.push([c[0], c[1] as number]);
      else if (Array.isArray(c)) c.forEach(walk);
    };
    walk((boundary as { coordinates?: unknown }).coordinates);
    if (pts.length) {
      const b = new LngLatBounds(pts[0], pts[0]);
      pts.forEach((p) => b.extend(p));
      map.fitBounds(b, { padding: 40, animate: false });
    }
    return () => {
      if (map.getLayer("boundary-line")) map.removeLayer("boundary-line");
      if (map.getSource(BOUNDARY_SRC)) map.removeSource(BOUNDARY_SRC);
    };
  }, [ready, boundary]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !tileUrl) return;
    map.addSource(CLASS_SRC, { type: "raster", tiles: [tileUrl], tileSize: 256 });
    // Insert below the boundary line when present.
    map.addLayer(
      {
        id: "classification-layer",
        type: "raster",
        source: CLASS_SRC,
        paint: { "raster-opacity": opacityRef.current, "raster-resampling": "nearest" },
      },
      map.getLayer("boundary-line") ? "boundary-line" : undefined,
    );
    return () => {
      if (map.getLayer("classification-layer")) map.removeLayer("classification-layer");
      if (map.getSource(CLASS_SRC)) map.removeSource(CLASS_SRC);
    };
  }, [ready, tileUrl, boundary]);

  useEffect(() => {
    const map = mapRef.current;
    if (map && ready && map.getLayer("classification-layer")) {
      map.setPaintProperty("classification-layer", "raster-opacity", opacity);
    }
  }, [ready, opacity, tileUrl, boundary]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !camera) return;
    const c = map.getCenter();
    if (
      Math.abs(c.lng - camera.center[0]) < 1e-9 &&
      Math.abs(c.lat - camera.center[1]) < 1e-9 &&
      Math.abs(map.getZoom() - camera.zoom) < 1e-9 &&
      map.getBearing() === camera.bearing &&
      map.getPitch() === camera.pitch
    )
      return;
    map.jumpTo(camera);
  }, [ready, camera]);

  return (
    <div
      ref={box}
      role="application"
      aria-label={label}
      style={{ width: "100%", height: 480, pointerEvents: interactive ? undefined : "none" }}
    />
  );
}
