import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { validateBoundary } from "../components/boundary";
import error422 from "../mocks/fixtures/error.422.boundary_too_large.json";
import sitesCreate from "../mocks/fixtures/sites.create.201.json";
import { server } from "../mocks/server";

// openapi-fetch captures global fetch when the client is created, which is before MSW patches it at
// test start. Build the client per call instead (with a key, as the mock server requires).
vi.mock("../api/client", async (importOriginal) => {
  const m = await importOriginal<typeof import("../api/client")>();
  const make = () => m.createApiClient({ apiBaseUrl: "http://api.test", apiKey: "k" });
  const lazy = {
    GET: (path: string, init?: object) => (make().GET as (p: string, i?: object) => unknown)(path, init),
    POST: (path: string, init?: object) => (make().POST as (p: string, i?: object) => unknown)(path, init),
  } as unknown as typeof m.api;
  return { ...m, api: lazy };
});

const renderAt =(path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );

// Small square near Glen Trool (zone 30, inside the region), ring closed.
const square = (d = 0.02) => ({
  type: "Polygon",
  coordinates: [
    [
      [-4.5, 55.1],
      [-4.5 + d, 55.1],
      [-4.5 + d, 55.1 + d],
      [-4.5, 55.1 + d],
      [-4.5, 55.1],
    ],
  ],
});
const upload = (obj: unknown, name = "moor.geojson") => {
  const input = screen.getByLabelText("Boundary GeoJSON file");
  const body = typeof obj === "string" ? obj : JSON.stringify(obj);
  fireEvent.change(input, { target: { files: [new File([body], name, { type: "application/geo+json" })] } });
};
const listItem = "Glen Trool to Loch Doon burn";

describe("Sites page", () => {
  it("lists sites, then searches", async () => {
    renderAt("/sites");
    expect(screen.getByText(/Loading sites/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: listItem })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search sites"), { target: { value: "zzz" } });
    expect(await screen.findByText(/No sites match "zzz"/)).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    server.use(http.get("*/sites", () => HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 })));
    renderAt("/sites");
    expect(await screen.findByText(/No sites yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add your first site" })).toBeInTheDocument();
  });

  it("shows API errors and retries", async () => {
    server.use(
      http.get("*/sites", () => HttpResponse.json({ code: "internal_error", message: "Boom", detail: null }, { status: 500 }), {
        once: true,
      }),
    );
    renderAt("/sites");
    expect(await screen.findByRole("alert")).toHaveTextContent("Boom");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: listItem })).toBeInTheDocument();
  });

  it("shows a network failure", async () => {
    server.use(http.get("*/sites", () => HttpResponse.error()));
    renderAt("/sites");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
  });
});

describe("validateBoundary", () => {
  it("accepts Polygon, Feature and MultiPolygon", () => {
    expect(validateBoundary(JSON.stringify(square()))).toMatchObject({ ok: true });
    expect(validateBoundary(JSON.stringify({ type: "Feature", properties: {}, geometry: square() }))).toMatchObject({
      ok: true,
    });
    const mp = { type: "MultiPolygon", coordinates: [square().coordinates, square(0.01).coordinates] };
    expect(validateBoundary(JSON.stringify(mp))).toMatchObject({ ok: true, polygons: 2 });
  });

  it("estimates area reasonably", () => {
    // 0.02 deg x 0.02 deg at 55.1N is about 1.27 km x 2.22 km = ~282 ha.
    const r = validateBoundary(JSON.stringify(square()));
    expect(r.ok && r.areaHa).toBeGreaterThan(260);
    expect(r.ok && r.areaHa).toBeLessThan(300);
  });

  const open = [[-4.5, 55.1], [-4.4, 55.1], [-4.4, 55.2], [-4.5, 55.2]];
  const short = [[-4.5, 55.1], [-4.4, 55.1], [-4.5, 55.1]];
  it.each([
    ["{bad", /not valid JSON/],
    [JSON.stringify({ type: "Point", coordinates: [-4.5, 55.1] }), /Polygon or MultiPolygon/],
    [JSON.stringify({ type: "Polygon", coordinates: [short] }), /at least 4/],
    [JSON.stringify({ type: "Polygon", coordinates: [[...open, [-4.5, 55.15]]] }), /not closed/],
    [JSON.stringify({ type: "Polygon", coordinates: [[[10, 50], [10.1, 50], [10.1, 50.1], [10, 50]]] }), /outside the supported region/],
    [JSON.stringify(square(0.6)), /maximum is 50,000 ha/],
  ])("rejects case %#", (text, msg) => {
    const r = validateBoundary(text);
    expect(r.ok).toBe(false);
    expect(!r.ok && r.error).toMatch(msg);
  });
});

describe("NewSite page", () => {
  it("shows a validation error for bad files and does not call the API", async () => {
    const spy = vi.fn();
    server.use(http.post("*/sites", () => (spy(), HttpResponse.json(sitesCreate, { status: 201 }))));
    renderAt("/sites/new");
    upload("{nope");
    expect(await screen.findByRole("alert")).toHaveTextContent("not valid JSON");
    fireEvent.click(screen.getByRole("button", { name: "Create site" }));
    expect(spy).not.toHaveBeenCalled();
  });

  it("previews, submits and navigates to the new site", async () => {
    let body: { name?: string; boundary?: { type?: string } } = {};
    server.use(
      http.post("*/sites", async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(sitesCreate, { status: 201 });
      }),
    );
    renderAt("/sites/new");
    upload(square());
    expect(await screen.findByRole("img", { name: "Boundary preview" })).toBeInTheDocument();
    expect(screen.getByLabelText("Site name")).toHaveValue("moor");
    fireEvent.click(screen.getByRole("button", { name: "Create site" }));
    expect(await screen.findByRole("heading", { level: 2, name: /Site map/ })).toBeInTheDocument();
    expect(body).toMatchObject({ name: "moor", boundary: { type: "Polygon" } });
  });

  it("requires a name", async () => {
    renderAt("/sites/new");
    upload(square());
    await screen.findByRole("img", { name: "Boundary preview" });
    fireEvent.change(screen.getByLabelText("Site name"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Create site" }));
    expect(await screen.findByText("Enter a site name.")).toBeInTheDocument();
  });

  it("shows server 422 errors in {code,message,detail} form", async () => {
    server.use(http.post("*/sites", () => HttpResponse.json(error422, { status: 422 })));
    renderAt("/sites/new");
    upload(square());
    await screen.findByRole("img", { name: "Boundary preview" });
    fireEvent.click(screen.getByRole("button", { name: "Create site" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("maximum is 50 000 ha");
    expect(alert).toHaveTextContent("boundary_too_large");
    expect(alert).toHaveTextContent("max_area_ha: 50000");
    await waitFor(() => expect(screen.getByRole("button", { name: "Create site" })).toBeEnabled());
  });
});
