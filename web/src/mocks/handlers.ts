import { http, HttpResponse } from "msw";
import changesCreate200 from "./fixtures/changes.create.200.json";
import changesCreate202 from "./fixtures/changes.create.202.json";
import changesStats from "./fixtures/changes.stats.200.json";
import changesStatsCsv from "./fixtures/changes.stats.200.csv?raw";
import classifications from "./fixtures/classifications.list.200.json";
import error401 from "./fixtures/error.401.json";
import error404 from "./fixtures/error.404.json";
import health from "./fixtures/health.200.json";
import runsCreate202 from "./fixtures/runs.create.202.json";
import runsGet from "./fixtures/runs.get.200.json";
import sitesCreate from "./fixtures/sites.create.201.json";
import sitesGet from "./fixtures/sites.get.200.json";
import sitesList from "./fixtures/sites.list.200.json";

/** Matches any origin, so the handlers work whatever VITE_API_BASE_URL is. */
const any = (path: string) => `*${path}`;

const needsKey = <T extends (info: { request: Request }) => Response | Promise<Response>>(fn: T) =>
  (info: { request: Request }) =>
    info.request.headers.get("X-API-Key") ? fn(info) : HttpResponse.json(error401, { status: 401 });

export const handlers = [
  http.get(any("/health"), () => HttpResponse.json(health)),
  http.get(
    any("/sites"),
    needsKey(({ request }) => {
      const q = new URL(request.url).searchParams.get("q")?.toLowerCase();
      if (!q) return HttpResponse.json(sitesList);
      const items = sitesList.items.filter(
        (s) => s.name.toLowerCase().includes(q) || s.slug.toLowerCase().includes(q),
      );
      return HttpResponse.json({ ...sitesList, items, total: items.length });
    }),
  ),
  http.post(
    any("/sites"),
    needsKey(() =>
      HttpResponse.json(sitesCreate, { status: 201, headers: { Location: `/sites/${sitesCreate.id}` } }),
    ),
  ),
  http.get(
    any("/sites/:siteId"),
    needsKey(({ request }) =>
      request.url.includes(sitesGet.id) ? HttpResponse.json(sitesGet) : HttpResponse.json(error404, { status: 404 }),
    ),
  ),
  http.post(
    any("/sites/:siteId/runs"),
    needsKey(() =>
      HttpResponse.json(runsCreate202, { status: 202, headers: { Location: `/runs/${runsCreate202.id}` } }),
    ),
  ),
  http.get(any("/runs/:runId"), needsKey(() => HttpResponse.json(runsGet))),
  http.get(any("/sites/:siteId/classifications"), needsKey(() => HttpResponse.json(classifications))),
  http.post(
    any("/sites/:siteId/changes"),
    needsKey(() => HttpResponse.json(changesCreate200)),
  ),
  http.get(
    any("/changes/:changeId/stats"),
    needsKey(({ request }) =>
      request.headers.get("Accept")?.includes("text/csv")
        ? new HttpResponse(changesStatsCsv, { headers: { "Content-Type": "text/csv" } })
        : HttpResponse.json(changesStats),
    ),
  ),
];

// TiTiler point lookup (no API key). The mock always reports class 1 (bog).
handlers.push(
  http.get("*/cog/point/:coords", ({ params }) => {
    const [lon, lat] = String(params.coords).split(",").map(Number);
    return HttpResponse.json({ coordinates: [lon, lat], values: [1], band_names: ["b1"] });
  }),
);

export { changesCreate202 };
