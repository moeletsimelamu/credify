/**
 * The one place the SPA talks to the API (ARCHITECTURE.md §6–7).
 *
 * - The access token lives in memory only. The refresh token is an HttpOnly cookie the browser
 *   sends to /api/v1/auth; script never sees it.
 * - A 401 on an authenticated call triggers one refresh and one retry. Refreshes are
 *   de-duplicated inside the tab and serialised across tabs (Web Locks) because the server
 *   rotates the refresh token on every use and treats reuse of an old one as theft.
 * - Errors are RFC 9457 problem documents, surfaced as ApiError.
 */
import type { CurrentUser, TokenResponse } from "./types";

export const API_BASE = "/api/v1";

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  code?: string;
  detail?: string;
  request_id?: string | null;
  errors?: Array<{ loc: Array<string | number>; msg: string; type?: string }>;
  violations?: string[];
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly problem: Problem;

  constructor(status: number, problem: Problem) {
    super(problem.detail || problem.title || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = problem.code ?? "UNKNOWN";
    this.problem = problem;
  }

  /** Field-level messages keyed by the request-body field name. */
  get fieldErrors(): Record<string, string> {
    const fields: Record<string, string> = {};
    for (const error of this.problem.errors ?? []) {
      const path = error.loc.filter((part) => part !== "body");
      const field = path.join(".");
      if (field && !fields[field]) fields[field] = error.msg.replace(/^Value error, /, "");
    }
    for (const violation of this.problem.violations ?? []) {
      const match = /^([\w.]+): (.+)$/.exec(violation);
      if (match?.[1] && match[2] && !fields[match[1]]) fields[match[1]] = match[2];
    }
    return fields;
  }
}

export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("The server could not be reached. Check your connection and try again.");
    this.name = "NetworkError";
    this.cause = cause;
  }
}

// --- Session state -----------------------------------------------------------------------

let accessToken: string | null = null;
type SessionListener = (user: CurrentUser | null) => void;
const listeners = new Set<SessionListener>();

export function setSession(token: TokenResponse | null): void {
  accessToken = token?.access_token ?? null;
  for (const listener of listeners) listener(token?.user ?? null);
}

export function onSessionChange(listener: SessionListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

let refreshInFlight: Promise<TokenResponse> | null = null;

async function doRefresh(): Promise<TokenResponse> {
  const response = await rawFetch(`${API_BASE}/auth/refresh`, { method: "POST" });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as TokenResponse;
}

/** Hold a cross-tab lock while refreshing, so two tabs never present the same token. */
async function refreshWithLock(): Promise<TokenResponse> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  if (!locks) return doRefresh();
  return await locks.request("credify-refresh", doRefresh);
}

/** Exchange the refresh cookie for a new access token. Shared by concurrent callers. */
export function refreshSession(): Promise<TokenResponse> {
  if (!refreshInFlight) {
    refreshInFlight = refreshWithLock()
      .then((token) => {
        setSession(token);
        return token;
      })
      .catch((error: unknown) => {
        setSession(null);
        throw error;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

// --- Requests ----------------------------------------------------------------------------

export type QueryValue = string | number | boolean | null | undefined | Array<string | number>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, QueryValue>;
  /** Optimistic-concurrency precondition: the resource version the user was looking at. */
  ifMatch?: number;
  idempotencyKey?: string;
  signal?: AbortSignal;
  /** false for login/register/refresh, which must not trigger a refresh-and-retry. */
  auth?: boolean;
}

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((item) => params.append(key, String(item)));
    else params.append(key, String(value));
  }
  const search = params.toString();
  return search ? `${url}?${search}` : url;
}

async function rawFetch(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, { credentials: "same-origin", ...init });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new NetworkError(error);
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let problem: Problem = { status: response.status, title: response.statusText };
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") problem = body as Problem;
  } catch {
    // Not JSON (e.g. a proxy error page); keep the status line.
  }
  return new ApiError(response.status, problem);
}

function buildInit(options: RequestOptions): RequestInit {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.auth !== false && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (options.ifMatch !== undefined) headers["If-Match"] = `"${options.ifMatch}"`;
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  return {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  };
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = buildUrl(path, options.query);
  let response = await rawFetch(url, buildInit(options));

  if (response.status === 401 && options.auth !== false) {
    try {
      await refreshSession();
    } catch {
      throw await toApiError(response);
    }
    response = await rawFetch(url, buildInit(options));
  }

  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** A fresh idempotency key per user intent (one click of "Record payment"). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

/** Human message for any error a query or mutation can raise. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "You don't have permission to do that.";
    if (error.status === 409 && error.code === "CONCURRENT_MODIFICATION")
      return "Someone else changed this record. Reload to see the latest version, then try again.";
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
