/**
 * YouTube Shorts Factory — API Client
 * =====================================
 * Centralised typed fetch wrapper that communicates with the FastAPI backend.
 * All requests route through `NEXT_PUBLIC_API_URL` (set in `.env.local`).
 *
 * Handles:
 *  - JSON parsing with error interception
 *  - Long timeouts (up to 300 s) for heavy video endpoints
 *  - Consistent error shape returned to callers
 */

// ── Types ───────────────────────────────────────────────────────

export interface ApiError {
  status: number;
  message: string;
  detail?: string;
}

export class ApiRequestError extends Error {
  status: number;
  detail?: string;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.detail = detail;
  }
}

// ── Base URL ────────────────────────────────────────────────────

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ??
  "http://localhost:8000";

// ── Core fetch wrapper ──────────────────────────────────────────

interface FetchOptions extends Omit<RequestInit, "body"> {
  /** Request body — automatically JSON-stringified for objects */
  body?: unknown;
  /** Timeout in milliseconds (default: 30 000 ms) */
  timeout?: number;
}

async function request<T = unknown>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const {
    body,
    timeout = 30_000,
    headers: extraHeaders,
    ...rest
  } = options;

  const url = `${BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(typeof extraHeaders === "object" && extraHeaders !== null
      ? (extraHeaders as Record<string, string>)
      : {}),
  };

  const init: RequestInit = {
    ...rest,
    headers,
    signal: controller.signal,
  };

  // Attach body (JSON-stringify plain objects)
  if (body !== undefined) {
    if (body instanceof FormData) {
      init.body = body;
      // Let the browser set Content-Type with boundary
    } else {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
  }

  try {
    const response = await fetch(url, init);

    // Attempt to parse JSON regardless of status (FastAPI returns JSON errors)
    let data: T | undefined;
    const contentType = response.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) {
      data = (await response.json()) as T;
    }

    if (!response.ok) {
      const detail =
        (data as Record<string, unknown>)?.detail as string | undefined;
      throw new ApiRequestError(
        response.status,
        detail ?? response.statusText ?? "Request failed",
        typeof detail === "string" ? detail : JSON.stringify(data),
      );
    }

    // If no JSON body was returned, return an empty object
    return (data ?? ({} as T)) as T;
  } catch (err) {
    if (err instanceof ApiRequestError) throw err;

    // AbortController timeout
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiRequestError(
        408,
        `Request timed out after ${timeout / 1000}s`,
      );
    }

    // Network / other errors
    throw new ApiRequestError(
      0,
      err instanceof Error ? err.message : "Unknown network error",
    );
  } finally {
    clearTimeout(timer);
  }
}

// ── Convenience methods ─────────────────────────────────────────

/** Standard-timeout GET */
export function get<T = unknown>(path: string, opts?: FetchOptions) {
  return request<T>(path, { ...opts, method: "GET" });
}

/** Standard-timeout POST */
export function post<T = unknown>(
  path: string,
  body?: unknown,
  opts?: FetchOptions,
) {
  return request<T>(path, { ...opts, method: "POST", body });
}

/** Standard-timeout PUT */
export function put<T = unknown>(
  path: string,
  body?: unknown,
  opts?: FetchOptions,
) {
  return request<T>(path, { ...opts, method: "PUT", body });
}

/** Standard-timeout DELETE */
export function del<T = unknown>(path: string, opts?: FetchOptions) {
  return request<T>(path, { ...opts, method: "DELETE" });
}

// ── Extended-timeout variants for heavy video endpoints ─────────
// 300 s = 5 minutes — enough for download + FFmpeg + Gemini round-trips

const HEAVY_TIMEOUT = 300_000;

/** POST with 5-minute timeout (video generation, clipper run, etc.) */
export function postHeavy<T = unknown>(
  path: string,
  body?: unknown,
  opts?: FetchOptions,
) {
  return request<T>(path, {
    ...opts,
    method: "POST",
    body,
    timeout: HEAVY_TIMEOUT,
  });
}

// ── Dashboard aggregate helper ──────────────────────────────────

export interface DashboardStats {
  total_clips: number;
  total_stories: number;
  clips_uploaded: number;
  stories_uploaded: number;
  clips_pending: number;
  stories_pending: number;
  clips_error: number;
  stories_error: number;
  active_channels: number;
}

export function fetchDashboardStats() {
  return get<DashboardStats>("/api/stats");
}
