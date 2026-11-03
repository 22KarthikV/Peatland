import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { server } from "./mocks/server";

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

// MapLibre needs WebGL, which jsdom lacks. Tests that care about the map install their own mock.
vi.mock("maplibre-gl", () => ({
  Map: class {
    on() {}
    addControl() {}
    getLayer() {}
    getSource() {}
    remove() {}
  },
  NavigationControl: class {},
  LngLatBounds: class {
    extend() {}
  },
}));
vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));
