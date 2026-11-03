import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import changesCreate202 from "../mocks/fixtures/changes.create.202.json";
import changesStats from "../mocks/fixtures/changes.stats.200.json";
import classifications from "../mocks/fixtures/classifications.list.200.json";
import runsGetFailed from "../mocks/fixtures/runs.get.failed.200.json";
import runsGet from "../mocks/fixtures/runs.get.200.json";
import sitesGet from "../mocks/fixtures/sites.get.200.json";
import { server } from "../mocks/server";

const maps: FakeMap[] = [];
type Handler = (e?: unknown) => void;
class FakeMap {
  handlers: Record<string, Handler> = {};
  sources: Record<string, unknown> = {};
  layers: Record<string, { paint: Record<string, unknown> }> = {};
  center = { lng: -4.4, lat: 55.15 };
  jumps: unknown[] = [];
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
  setPaintProperty() {}
  fitBounds() {}
  getCenter() {
    return this.center;
  }
  getZoom() {
    return 9;
  }
  getBearing() {
    return 0;
  }
  getPitch() {
    return 0;
  }
  jumpTo(c: unknown) {
    this.jumps.push(c);
  }
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

const { Change, pollConfig } = await import("./Change");
pollConfig.ms = 10;

const [older, newer] = classifications.items;

const renderChange = () =>
  render(
    <MemoryRouter initialEntries={[`/sites/${sitesGet.id}/change`]}>
      <Routes>
        <Route path="/sites/:siteId/change" element={<Change />} />
      </Routes>
    </MemoryRouter>,
  );

const loaded = async () => {
  await screen.findByRole("heading", { name: /change/ });
  await waitFor(() => expect(maps.length).toBeGreaterThan(0));
  act(() => maps.forEach((m) => m.handlers.load?.()));
};

const ready = async () => {
  renderChange();
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: "Compute change" }));
  await screen.findByRole("region", { name: "Transitions" });
};

beforeEach(() => {
  maps.length = 0;
});

describe("change view", () => {
  it("preselects the two latest dates and shows both classification layers in a swipe view", async () => {
    renderChange();
    await loaded();
    expect((screen.getByLabelText("From") as HTMLSelectElement).value).toBe(older.id);
    expect((screen.getByLabelText("To") as HTMLSelectElement).value).toBe(newer.id);
    const tiles = maps.map((m) => (m.sources.classification as { tiles: string[] }).tiles[0]);
    expect(tiles).toEqual([older.tile_url, newer.tile_url]);
    expect(screen.getByTestId("swipe-overlay")).toHaveStyle({ clipPath: "inset(0 0 0 50%)" });
    fireEvent.change(screen.getByLabelText(/Swipe position/), { target: { value: "20" } });
    expect(screen.getByTestId("swipe-overlay")).toHaveStyle({ clipPath: "inset(0 0 0 20%)" });
  });

  it("switches to side by side and keeps the two maps synchronised", async () => {
    renderChange();
    await loaded();
    fireEvent.click(screen.getByLabelText("Side by side"));
    await waitFor(() => expect(screen.queryByTestId("swipe-overlay")).toBeNull());
    const [a, b] = maps.slice(-2);
    act(() => {
      a.handlers.load();
      b.handlers.load();
    });
    a.center = { lng: -4.5, lat: 55.2 };
    act(() => a.handlers.move({ originalEvent: {} }));
    await waitFor(() => expect(b.jumps.at(-1)).toMatchObject({ center: [-4.5, 55.2], zoom: 9 }));
  });

  it("blocks computing when From is not before To", async () => {
    renderChange();
    await loaded();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: newer.id } });
    expect(screen.getByRole("alert")).toHaveTextContent("earlier");
    expect(screen.getByRole("button", { name: "Compute change" })).toBeDisabled();
  });

  it("computes an existing change, lists transitions and shows detail on click", async () => {
    let body: unknown;
    server.use(
      http.post("*/sites/:siteId/changes", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(changesStats.change);
      }),
    );
    await ready();
    expect(body).toEqual({
      from: { year: 2025, season: older.window.season },
      to: { year: 2025, season: newer.window.season },
    });
    fireEvent.click(screen.getByRole("button", { name: /Bog \(peatland\) to Felled or burnt-bare/ }));
    const detail = screen.getByTestId("transition-detail");
    expect(detail).toHaveTextContent("Bog (peatland) to Felled or burnt-bare");
    expect(detail).toHaveTextContent("479.70 ha, 7.37% of the site");
  });

  it("shows the change raster when the layer toggle is on", async () => {
    await ready();
    const toggle = screen.getByLabelText("Show change layer");
    expect(toggle).toBeEnabled();
    fireEvent.click(toggle);
    await screen.findByRole("application", { name: "Change map" });
    await waitFor(() => expect(maps.length).toBeGreaterThan(2));
    const map = maps[maps.length - 1];
    act(() => map.handlers.load());
    expect((map.sources.classification as { tiles: string[] }).tiles[0]).toBe(changesStats.change.tile_url);
    expect(screen.getByRole("link", { name: "Download change raster" })).toHaveAttribute(
      "href",
      changesStats.change.download_url,
    );
  });

  it("polls a queued run until the change is ready", async () => {
    let polls = 0;
    server.use(
      http.post("*/sites/:siteId/changes", () => HttpResponse.json(changesCreate202, { status: 202 })),
      http.get("*/runs/:runId", () => {
        polls++;
        return HttpResponse.json(
          polls < 3
            ? { ...runsGet, kind: "change", status: "running" }
            : { ...runsGet, kind: "change", outputs: { ...runsGet.outputs, change_id: changesStats.change.id } },
        );
      }),
    );
    renderChange();
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Compute change" }));
    expect(await screen.findByText(/Computing change/)).toBeInTheDocument();
    await screen.findByRole("region", { name: "Transitions" });
    expect(polls).toBe(3);
    expect(screen.queryByText(/Computing change/)).toBeNull();
  });

  it("shows the error when the change run fails", async () => {
    server.use(
      http.post("*/sites/:siteId/changes", () => HttpResponse.json(changesCreate202, { status: 202 })),
      http.get("*/runs/:runId", () => HttpResponse.json({ ...runsGetFailed, kind: "change" })),
    );
    renderChange();
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Compute change" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No scenes under 5% cloud cover");
    expect(screen.getByLabelText("Show change layer")).toBeDisabled();
  });

  it("shows the API error when computing is rejected", async () => {
    server.use(
      http.post("*/sites/:siteId/changes", () =>
        HttpResponse.json(
          { code: "classification_missing", message: "No classification for the 'to' window.", detail: {} },
          { status: 422 },
        ),
      ),
    );
    renderChange();
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Compute change" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No classification for the 'to' window.");
  });

  it("explains when the site has no classifications", async () => {
    server.use(http.get("*/sites/:siteId/classifications", () => HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 })));
    renderChange();
    expect(await screen.findByText(/No classifications yet/)).toBeInTheDocument();
    expect(screen.queryByRole("application")).toBeNull();
  });

  it("shows an error for an unknown site", async () => {
    render(
      <MemoryRouter initialEntries={["/sites/nope/change"]}>
        <Routes>
          <Route path="/sites/:siteId/change" element={<Change />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
