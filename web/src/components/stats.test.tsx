import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StatsPanel } from "./StatsPanel";
import changesStats from "../mocks/fixtures/changes.stats.200.json";
import { server } from "../mocks/server";

vi.mock("../config", async (orig) => ({ ...(await orig<typeof import("../config")>()), config: { apiBaseUrl: "http://api.test", apiKey: "k", useMocks: false } }));

// jsdom has no layout, so ResponsiveContainer would render nothing. Give it a fixed size.
vi.mock("recharts", async (orig) => {
  const actual = await orig<typeof import("recharts")>();
  const { cloneElement } = await import("react");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement<{ width?: number; height?: number }> }) =>
      cloneElement(children, { width: 600, height: 300 }),
  };
});

type Stats = Parameters<typeof StatsPanel>[0]["stats"];
const stats = changesStats as unknown as Stats;

const rowsOf = (table: HTMLElement) =>
  within(table)
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("cell").map((c) => c.textContent));

describe("StatsPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows area per transition and per class in ha and percent", () => {
    render(<StatsPanel stats={stats} />);
    const t = screen.getByRole("table", { name: "Area by transition" });
    expect(rowsOf(t)).toHaveLength(6);
    expect(rowsOf(t)[0]).toEqual(["Bog (peatland)", "Bog (peatland)", "3,730.60", "57.28%"]);
    expect(screen.getByRole("table", { name: "Area by class, from date" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Area by class, to date" })).toBeInTheDocument();
    expect(screen.getByText(/6,512\.40 ha compared/)).toBeInTheDocument();
    expect(screen.getByText(/2 class changes detected/)).toBeInTheDocument();
  });

  it("sorts by clicking headers and exposes aria-sort", () => {
    render(<StatsPanel stats={stats} />);
    const t = screen.getByRole("table", { name: "Area by transition" });
    const area = within(t).getByRole("columnheader", { name: /Area \(ha\)/ });
    expect(area).toHaveAttribute("aria-sort", "descending");
    const fromHeader = within(t).getByRole("columnheader", { name: /From class/ });
    expect(fromHeader).toHaveAttribute("aria-sort", "none");

    fireEvent.click(within(fromHeader).getByRole("button"));
    expect(fromHeader).toHaveAttribute("aria-sort", "ascending");
    expect(area).toHaveAttribute("aria-sort", "none");
    expect(rowsOf(t)[0][0]).toBe("Bog (peatland)");
    expect(rowsOf(t)[5][0]).toBe("Woodland");

    fireEvent.click(within(fromHeader).getByRole("button"));
    expect(fromHeader).toHaveAttribute("aria-sort", "descending");
    expect(rowsOf(t)[0][0]).toBe("Woodland");

    fireEvent.click(within(area).getByRole("button"));
    fireEvent.click(within(area).getByRole("button"));
    expect(area).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(within(area).getByRole("button"));
    expect(area).toHaveAttribute("aria-sort", "ascending");
    expect(rowsOf(t)[0][2]).toBe("30.10");
  });

  it("headers are real buttons, so they are keyboard operable", () => {
    render(<StatsPanel stats={stats} />);
    const t = screen.getByRole("table", { name: "Area by transition" });
    const b = within(t).getByRole("button", { name: /To class/ });
    b.focus();
    expect(b).toHaveFocus();
    expect(b.tagName).toBe("BUTTON");
  });

  it("renders the chart with a text alternative", async () => {
    const { container } = render(<StatsPanel stats={stats} />);
    const fig = screen.getByRole("img", { name: /Area by transition \(hectares\)/ });
    expect(fig).toBeInTheDocument();
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle").length).toBe(6));
    const fills = [...container.querySelectorAll(".recharts-bar-rectangle path")].map((p) => p.getAttribute("fill"));
    expect(fills).toContain("#D55E00");
    expect(fills).toContain("#0072B2");
  });

  it("downloads the CSV with Accept: text/csv", async () => {
    let accept: string | null = null;
    server.use(
      http.get("*/changes/:id/stats", ({ request }) => {
        accept = request.headers.get("Accept");
        return new HttpResponse("from_class_id,from_class\n", { headers: { "Content-Type": "text/csv" } });
      }),
    );
    const create = vi.fn(() => "blob:x");
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<StatsPanel stats={stats} />);
    fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(accept).toBe("text/csv");
    expect(create).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith("blob:x");
  });

  it("shows an alert when the CSV download fails", async () => {
    server.use(http.get("*/changes/:id/stats", () => HttpResponse.json({ code: "not_found", message: "No such change", detail: {} }, { status: 404 })));
    render(<StatsPanel stats={stats} />);
    fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("handles the empty state", () => {
    render(<StatsPanel stats={{ ...stats, total_ha: 0, transitions: [], from_class_areas: [], to_class_areas: [] }} />);
    expect(screen.getByRole("status")).toHaveTextContent(/No change statistics/);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download CSV" })).not.toBeInTheDocument();
  });

  it("says so when nothing changed class", () => {
    const same = stats.transitions.filter((t) => t.from_class.id === t.to_class.id);
    render(<StatsPanel stats={{ ...stats, transitions: same }} />);
    expect(screen.getByText(/No change detected/)).toBeInTheDocument();
  });
});
