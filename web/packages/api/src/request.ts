// The one request funnel for session-authed BFF endpoints: network
// failure, the 401 mapping, error-detail extraction, and contract
// validation live here so no fetcher family can forget a case.

import type { ZodType } from "zod";

import { buildApiUrl } from "./routes.ts";

export type ApiResult<T> =
  | { status: "ok"; data: T }
  | { status: "unauthenticated" }
  | { status: "error"; message: string; code?: string };

export const GENERIC_FAILURE = "Something went wrong. Please try again.";

// The transport floor every fetcher family shares: null = the network
// itself failed (server unreachable), everything else answers with the
// response and its best-effort JSON body (null body for 204 and
// non-JSON payloads; empty 401s must not masquerade as network loss).
// Meaning is assigned above this line, by request() or a domain
// classifier, never here.
export async function fetchJson(path: string, init?: RequestInit): Promise<{ res: Response; body: unknown } | null> {
  try {
    const res = await fetch(buildApiUrl(path), init);
    const body = res.status === 204 ? null : await res.json().catch(() => null);
    return { res, body };
  } catch {
    return null;
  }
}

// The {error, detail} envelope's human-readable reason, when present.
export function errorDetail(body: unknown): string {
  return typeof body === "object" && body !== null && "detail" in body
    ? String((body as { detail: unknown }).detail)
    : "";
}

// The envelope's machine-readable code (the domain error enums).
export function errorCode(body: unknown): string {
  return typeof body === "object" && body !== null && "error" in body
    ? String((body as { error: unknown }).error)
    : "";
}

// schema: the zod validator for the response body, or null for
// no-content endpoints (delete), whose success carries no data.
export async function request<T>(path: string, schema: ZodType<T>, init?: RequestInit): Promise<ApiResult<T>>;
export async function request(path: string, schema: null, init?: RequestInit): Promise<ApiResult<null>>;
export async function request<T>(
  path: string,
  schema: ZodType<T> | null,
  init?: RequestInit,
): Promise<ApiResult<T | null>> {
  const fetched = await fetchJson(path, { credentials: "include", ...init });
  if (!fetched) return { status: "error", message: "Could not reach the server." };
  const { res, body } = fetched;
  // 403 lands with 401: the app's endpoints never answer 403 for a
  // signed-in user except when the session predates a required scope,
  // and only a fresh login can mint it (the discover proxy's
  // data_access_denied on save is the live case).
  if (res.status === 401 || res.status === 403) return { status: "unauthenticated" };
  if (!res.ok) {
    // Only a 400's or 409's detail is user copy (the request was
    // wrong, or lost a race the user should hear about); other
    // statuses' bodies are internals, not messages.
    // detail AND code share the gate: other statuses' bodies are
    // internals, not user copy or classification.
    const classified = res.status === 400 || res.status === 409;
    return {
      status: "error",
      message: (classified ? errorDetail(body) : "") || GENERIC_FAILURE,
      code: (classified ? errorCode(body) : "") || undefined,
    };
  }
  if (schema === null) return { status: "ok", data: null };
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    if (process.env.NODE_ENV !== "production") console.error(`contract mismatch at ${path}:`, parsed.error);
    return { status: "error", message: GENERIC_FAILURE };
  }
  return { status: "ok", data: parsed.data };
}

// Verb conveniences over request(): bodies are JSON unless FormData
// (the browser must set the multipart boundary itself).
function bodyInit(method: string, body?: FormData | Record<string, unknown>): RequestInit {
  if (body === undefined) return { method };
  if (body instanceof FormData) return { method, body };
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const http = {
  get<T>(path: string, schema: ZodType<T>): Promise<ApiResult<T>> {
    return request(path, schema);
  },
  post<T>(path: string, schema: ZodType<T>, body?: FormData | Record<string, unknown>): Promise<ApiResult<T>> {
    return request(path, schema, bodyInit("POST", body));
  },
  patch<T>(path: string, schema: ZodType<T>, body: Record<string, unknown>): Promise<ApiResult<T>> {
    return request(path, schema, bodyInit("PATCH", body));
  },
  delete(path: string): Promise<ApiResult<null>> {
    return request(path, null, { method: "DELETE" });
  },
};
