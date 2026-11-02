import { describe, expect, it } from "vitest";
import { createApiClient } from "./client";
import sitesList from "../mocks/fixtures/sites.list.200.json";
import { readConfig } from "../config";

describe("api client", () => {
  it("sends X-API-Key and returns the sites fixture", async () => {
    const client = createApiClient({ apiBaseUrl: "http://api.test", apiKey: "k" });
    const { data, response } = await client.GET("/sites");
    expect(response.status).toBe(200);
    expect(data?.items[0]?.slug).toBe(sitesList.items[0]?.slug);
  });

  it("gets 401 without a key", async () => {
    const client = createApiClient({ apiBaseUrl: "http://api.test", apiKey: "" });
    const { error, response } = await client.GET("/sites");
    expect(response.status).toBe(401);
    expect(error?.code).toBeTruthy();
  });

  it("reads config with defaults", () => {
    expect(readConfig({ VITE_API_BASE_URL: "http://x/", VITE_API_KEY: "z" })).toEqual({
      apiBaseUrl: "http://x",
      apiKey: "z",
      useMocks: false,
    });
  });
});
