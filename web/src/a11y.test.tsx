import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { axe } from "vitest-axe";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import sitesGet from "./mocks/fixtures/sites.get.200.json";

// MSW handlers require a key; the map is mocked globally in test-setup.ts.
vi.mock("./config", async (orig) => ({
  ...(await orig<typeof import("./config")>()),
  config: { apiBaseUrl: "http://localhost:8000", apiKey: "k", useMocks: false },
}));

const runId = "07dbc9ed-81fa-456e-9c07-62de4d5f7089";

describe("axe checks", () => {
  // `ready` is something that only appears once the page has loaded its data.
  it.each([
    ["Sites", "/sites", () => screen.findByRole("link", { name: /glen trool/i })],
    ["NewSite", "/sites/new", () => screen.findByLabelText(/site name/i)],
    ["SiteMap", `/sites/${sitesGet.id}`, () => screen.findByRole("form", { name: /run analysis/i })],
    ["Change", `/sites/${sitesGet.id}/change`, () => screen.findByRole("button", { name: /compute change/i })],
    ["RunStatus", `/runs/${runId}`, () => screen.findByText(/succeeded/)],
  ])("%s has no violations", async (_name, path, ready) => {
    const { container } = render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    );
    await ready();
    const results = await axe(container);
    expect(results.violations).toEqual([]);
  });
});
