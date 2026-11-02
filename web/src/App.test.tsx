import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App shell", () => {
  it.each([
    ["/", "Sites"],
    ["/sites/new", "New site"],
    ["/sites/abc", "Site map abc"],
    ["/sites/abc/change", "Change abc"],
    ["/runs/r1", "Run status r1"],
  ])("renders %s", (path, heading) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { level: 2, name: heading })).toBeInTheDocument();
  });
});
