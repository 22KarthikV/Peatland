import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import queued from "../mocks/fixtures/runs.create.202.json";
import { server } from "../mocks/server";
import { RunAnalysis } from "./RunAnalysis";

vi.mock("../config", async (orig) => ({
  ...(await orig<typeof import("../config")>()),
  config: { apiBaseUrl: "http://localhost:8000", apiKey: "k", useMocks: false },
}));

function setup() {
  render(
    <MemoryRouter initialEntries={["/sites/s1"]}>
      <Routes>
        <Route path="/sites/:siteId" element={<RunAnalysis siteId="s1" />} />
        <Route path="/runs/:runId" element={<p>run page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RunAnalysis", () => {
  it("posts one window and navigates to the new run", async () => {
    let body: unknown;
    server.use(
      http.post("*/sites/s1/runs", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(queued, { status: 202 });
      }),
    );
    setup();
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2025-03-01" } });
    fireEvent.change(screen.getByLabelText("End date"), { target: { value: "2025-03-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    expect(await screen.findByText("run page")).toBeInTheDocument();
    expect(body).toEqual({ windows: [{ start: "2025-03-01", end: "2025-03-31" }], max_cloud_cover_pct: 60 });
  });

  it("does not post without dates, and flags a reversed range", async () => {
    let posted = false;
    server.use(
      http.post("*/sites/s1/runs", () => {
        posted = true;
        return HttpResponse.json(queued, { status: 202 });
      }),
    );
    setup();
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2025-04-01" } });
    fireEvent.change(screen.getByLabelText("End date"), { target: { value: "2025-03-01" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/not be after/);
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    await waitFor(() => expect(posted).toBe(false));
  });
});
