/**
 * A tiny fetch router for component tests: map "METHOD /api/v1/path" to a response and assert
 * on the requests that were made. Paths match exactly, without the query string, unless the
 * key includes one.
 */
import { vi } from "vitest";

export interface RecordedRequest {
  method: string;
  path: string;
  url: URL;
  headers: Headers;
  body: unknown;
}

type Reply =
  | { status?: number; body?: unknown; headers?: Record<string, string> }
  | ((
      request: RecordedRequest,
    ) => { status?: number; body?: unknown } | Promise<{ status?: number; body?: unknown }>);

export function json(body: unknown, status = 200) {
  return { status, body };
}

export function problem(status: number, detail: string, extra: Record<string, unknown> = {}) {
  return { status, body: { status, title: "Error", detail, code: "TEST", ...extra } };
}

export function mockApi(routes: Record<string, Reply | object | string | number | boolean | null>) {
  const requests: RecordedRequest[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
    const request: RecordedRequest = {
      method,
      path: url.pathname,
      url,
      headers: new Headers(init.headers),
      body,
    };
    requests.push(request);
    const key = [`${method} ${url.pathname}${url.search}`, `${method} ${url.pathname}`].find(
      (candidate) => candidate in routes,
    );
    if (!key) {
      return new Response(JSON.stringify({ detail: `No mock for ${method} ${url.pathname}` }), {
        status: 404,
        headers: { "Content-Type": "application/problem+json" },
      });
    }
    const route = routes[key];
    const reply =
      typeof route === "function"
        ? await (route as (r: RecordedRequest) => unknown)(request)
        : route && typeof route === "object" && "body" in route
          ? route
          : { body: route };
    const { status = 200, body: payload } = reply as { status?: number; body?: unknown };
    return new Response(status === 204 ? null : JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    requests,
    fetchMock,
    calls: (method: string, path: string) =>
      requests.filter((r) => r.method === method && r.path === path),
  };
}
