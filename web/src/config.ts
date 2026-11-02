export interface AppConfig {
  apiBaseUrl: string;
  apiKey: string;
  useMocks: boolean;
}

export function readConfig(env: Record<string, string | boolean | undefined> = import.meta.env): AppConfig {
  const str = (v: unknown, fallback: string) => (typeof v === "string" && v !== "" ? v : fallback);
  return {
    apiBaseUrl: str(env.VITE_API_BASE_URL, "http://localhost:8000").replace(/\/+$/, ""),
    apiKey: str(env.VITE_API_KEY, ""),
    useMocks: env.VITE_USE_MOCKS === "true",
  };
}

export const config = readConfig();
