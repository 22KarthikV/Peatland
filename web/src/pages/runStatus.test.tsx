import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import failed from "../mocks/fixtures/runs.get.failed.200.json";
import succeeded from "../mocks/fixtures/runs.get.200.json";
import queued from "../mocks/fixtures/runs.create.202.json";
import { server } from "../mocks/server";
import { pollConfig, RunStatus } from "./RunStatus";

vi.mock("../config", async (orig) => ({
  ...(await orig<typeof import("../config")>()),
  config: { apiBaseUrl: "http://localhost:8000", apiKey: "k", useMocks: false },
}));

const original = { ...pollConfig };

function renderAt(id: string) {
  return render(
    <MemoryRouter initialEntries={[`/runs/${id}`]}>
      <Routes>
        <Route path="/runs/:runId" element={<RunStatus />} />
        <Route path="/sites/:siteId" element={<p>site page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  Object.assign(pollConfig, { ms: 10, factor: 1, maxMs: 10 });
});
afterEach(() => {
  Object.assign(pollConfig, original);
});

describe("RunStatus", () => {
  it("shows a succeeded run with a link to the site map", async () => {
    server.use(http.get("*/runs/:id", () => HttpResponse.json(succeeded)));
    renderAt(succeeded.id);
    expect(await screen.findByText("succeeded")).toBeInTheDocument();
    expect(screen.getByText("classify: 2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /site map/i })).toHaveAttribute("href", `/sites/${succeeded.site_id}`);
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("polls until a terminal state, then stops", async () => {
    let calls = 0;
    server.use(
      http.get("*/runs/:id", () => {
        calls++;
        if (calls === 1) return HttpResponse.json(queued);
        if (calls === 2) return HttpResponse.json({ ...succeeded, status: "running", progress: { step: "composite", done: 1, total: 2 } });
        return HttpResponse.json(succeeded);
      }),
    );
    renderAt(succeeded.id);
    expect(await screen.findByText("queued")).toBeInTheDocument();
    expect(await screen.findByText("composite: 1 of 2")).toBeInTheDocument();
    expect(await screen.findByText("succeeded")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 80));
    expect(calls).toBe(3);
  });

  it("stops polling on unmount", async () => {
    let calls = 0;
    server.use(
      http.get("*/runs/:id", () => {
        calls++;
        return HttpResponse.json(queued);
      }),
    );
    const { unmount } = renderAt(queued.id);
    await screen.findByText("queued");
    unmount();
    const seen = calls;
    await new Promise((r) => setTimeout(r, 80));
    expect(calls).toBeLessThanOrEqual(seen + 1);
  });

  it("shows the error of a failed run and retries with the same params", async () => {
    let body: unknown;
    server.use(
      http.get("*/runs/:id", () => HttpResponse.json(failed)),
      http.post("*/sites/:siteId/runs", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...queued, id: "99999999-0000-4000-8000-000000000000" }, { status: 202 });
      }),
    );
    renderAt(failed.id);
    expect(await screen.findByText(/no_usable_scenes/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/No scenes under 5%/);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(body).toEqual(failed.params));
    // Navigated to the new run, which the handler still answers with the failed fixture.
    await waitFor(() => expect(screen.getByRole("heading", { level: 2, name: "Run status" })).toBeInTheDocument());
  });

  it("shows an error when the retry is rejected", async () => {
    server.use(
      http.get("*/runs/:id", () => HttpResponse.json(failed)),
      http.post("*/sites/:siteId/runs", () =>
        HttpResponse.json({ code: "rate_limited", message: "Too many requests.", detail: null }, { status: 429 }),
      ),
    );
    renderAt(failed.id);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Too many requests.")).toBeInTheDocument();
  });

  it("shows a not-found error without polling forever", async () => {
    let calls = 0;
    server.use(
      http.get("*/runs/:id", () => {
        calls++;
        return HttpResponse.json({ code: "not_found", message: "No such run.", detail: null }, { status: 404 });
      }),
    );
    renderAt("nope");
    expect(await screen.findByText("No such run.")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 60));
    expect(calls).toBe(1);
  });

  it("backs off between polls", async () => {
    Object.assign(pollConfig, { ms: 20, factor: 2, maxMs: 80 });
    const times: number[] = [];
    server.use(
      http.get("*/runs/:id", () => {
        times.push(Date.now());
        return HttpResponse.json(queued);
      }),
    );
    const { unmount } = renderAt(queued.id);
    await waitFor(() => expect(times.length).toBeGreaterThanOrEqual(4), { timeout: 2000 });
    unmount();
    const gaps = times.slice(1).map((t, i) => t - times[i]);
    expect(gaps[2]).toBeGreaterThan(gaps[0]);
  });
});
