import createClient, { type Middleware } from "openapi-fetch";
import { config as defaultConfig, type AppConfig } from "../config";
import type { components, paths } from "./schema";

export type Site = components["schemas"]["SiteDetail"];
export type Run = components["schemas"]["Run"];
export type ApiError = components["schemas"]["Error"];

/** Adds the X-API-Key header to every request when a key is configured. */
export function apiKeyMiddleware(apiKey: string): Middleware {
  return {
    onRequest({ request }) {
      if (apiKey) request.headers.set("X-API-Key", apiKey);
      return request;
    },
  };
}

export function createApiClient(cfg: Pick<AppConfig, "apiBaseUrl" | "apiKey"> = defaultConfig) {
  const client = createClient<paths>({
    baseUrl: cfg.apiBaseUrl,
    // Resolve fetch per call so test servers (MSW) that patch globalThis.fetch are honoured.
    fetch: (request) => globalThis.fetch(request),
  });
  client.use(apiKeyMiddleware(cfg.apiKey));
  return client;
}

export const api = createApiClient();
