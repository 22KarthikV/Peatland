import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import classifications from "../mocks/fixtures/classifications.list.200.json";
import sitesGet from "../mocks/fixtures/sites.get.200.json";
import { server } from "../mocks/server";
import { pointUrl } from "../lib/tiles";

// MapLibre needs WebGL; replace it with a recorder. MapView stays real so its calls are checked.
const maps: FakeMap[] = [];
type Handler = (e?: unknown) => void;
class FakeMap {
  handlers: Record<string, Handler> = {};
  sources: Record<string, unknown> = {};
  layers: Record<string, { paint: Record<string, unknown> }> = {};
  constructor() {
    maps.push(this);
  }
  on(ev: string, fn: Handler) {
    this.handlers[ev] = fn;
  }
  addControl() {}
  addSource(id: string, s: unknown) {
    this.sources[id] = s;
  }
  addLayer(l: { id: string; paint?: Record<string, unknown> }) {
    this.layers[l.id] = { paint: { ...l.paint } };
  }
  getLayer(id: string) {
    return this.layers[id];
  }
  getSource(id: string) {
    return this.sources[id];
  }
  removeLayer(id: string) {
    delete this.layers[id];
  }
  removeSource(id: string) {
    delete this.sources[id];
  }
  setPaintProperty(id: string, k: string, v: unknown) {
    this.layers[id].paint[k] = v;
  }
  fitBounds() {}
  remove() {}
}
vi.mock("maplibre-gl", () => ({
  Map: FakeMap,
  NavigationControl: class {},
  LngLatBounds: class {
    extend() {}
  },
}));
vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));
vi.mock("../config", async (orig) => {
  const m = await orig<typeof import("../config")>();
  return { ...m, config: { apiBaseUrl: "http://api.test", apiKey: "k", useMocks: false } };
});

const { SiteMap } = await import("./SiteMap");

const renderMap = () =>
  render(
    <MemoryRouter initialEntries={[`/sites/${sitesGet.id}`]}>
      <Routes>
        <Route path="/sites/:siteId" element={<SiteMap />} />
      </Routes>
    </MemoryRouter>,
  );

const loaded = async () => {
  await screen.findByRole("heading", { name: sitesGet.name });
  await waitFor(() => expect(maps.length).toBeGreaterThan(0));
  const map = maps[maps.length - 1];
  act(() => map.handlers.load());
  return map;
};

beforeEach(() => {
  maps.length = 0;
});

describe("site map", () => {
  it("loads the site, offers dates and shows the latest classification layer and boundary", async () => {
    renderMap();
    const map = await loaded();
    const select = screen.getByLabelText("Date") as HTMLSelectElement;
    expect([...select.options].map((o) => o.text)).toEqual(["2025-03-19", "2025-04-08"]);
    expect(select.value).toBe(classifications.items[1].id);
    expect(map.sources.boundary).toBeDefined();
    expect((map.sources.classification as { tiles: string[] }).tiles[0]).toBe(classifications.items[1].tile_url);
  });

  it("switches the layer when another date is chosen", async () => {
    renderMap();
    const map = await loaded();
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: classifications.items[0].id } });
    expect((map.sources.classification as { tiles: string[] }).tiles[0]).toBe(classifications.items[0].tile_url);
  });

  it("applies the opacity slider to the raster layer", async () => {
    renderMap();
    const map = await loaded();
    expect(map.layers["classification-layer"].paint["raster-opacity"]).toBe(0.8);
    fireEvent.change(screen.getByLabelText("Opacity"), { target: { value: "0.3" } });
    expect(map.layers["classification-layer"].paint["raster-opacity"]).toBe(0.3);
  });

  it("always shows the legend with every class and its Okabe-Ito colour", async () => {
    renderMap();
    await loaded();
    const legend = screen.getByRole("region", { name: "Legend" });
    for (const label of ["Other", "Bog (peatland)", "Woodland", "Felled or burnt-bare"]) {
      expect(legend).toHaveTextContent(label);
    }
    expect(screen.getByTestId("swatch-bog")).toHaveStyle({ backgroundColor: "#0072B2" });
    expect(screen.getByTestId("swatch-felled_or_burnt_bare")).toHaveStyle({ backgroundColor: "#D55E00" });
  });

  it("shows the class name under the pointer via the TiTiler point endpoint", async () => {
    let requested = "";
    server.use(
      http.get("*/cog/point/:coords", ({ request }) => {
        requested = request.url;
        return HttpResponse.json({ coordinates: [-4.47, 55.12], values: [3], band_names: ["b1"] });
      }),
    );
    renderMap();
    const map = await loaded();
    act(() => map.handlers.mousemove({ lngLat: { lng: -4.47, lat: 55.12 } }));
    await waitFor(() => expect(screen.getByTestId("hover-class")).toHaveTextContent("Felled or burnt-bare"));
    expect(requested).toBe(pointUrl(classifications.items[1].tile_url, -4.47, 55.12));
  });

  it("shows No data when the point is outside the raster, and clears on mouse out", async () => {
    server.use(http.get("*/cog/point/:coords", () => HttpResponse.json({ detail: "outside" }, { status: 404 })));
    renderMap();
    const map = await loaded();
    act(() => map.handlers.mousemove({ lngLat: { lng: 0, lat: 0 } }));
    await waitFor(() => expect(screen.getByTestId("hover-class")).toHaveTextContent("No data"));
    act(() => map.handlers.mouseout());
    expect(screen.getByTestId("hover-class")).toHaveTextContent("Move the pointer");
  });

  it("shows an error when the site cannot be loaded", async () => {
    server.use(http.get("*/sites/:siteId", () => HttpResponse.json({ code: "not_found", message: "Site not found" }, { status: 404 })));
    renderMap();
    expect(await screen.findByRole("alert")).toHaveTextContent("Site not found");
  });
});

describe("pointUrl", () => {
  it("keeps the TiTiler origin and the encoded cog url", () => {
    expect(pointUrl(classifications.items[0].tile_url, -4.4, 55.1)).toBe(
      "http://localhost:8001/cog/point/-4.4,55.1?url=s3%3A%2F%2Fpeatwatch-data%2Fclassifications%2Fglen-trool%2F2025%2Fw20250319-20250319%2Funet-1.0.0.tif",
    );
  });
});
