import type { ApiError } from "../api/client";

export const networkError: ApiError = {
  code: "internal_error",
  message: "Could not reach the server. Check your connection and try again.",
  detail: null,
};
