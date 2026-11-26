import { mockApi, problem } from "@/test/api";

import {
  api,
  ApiError,
  buildUrl,
  errorMessage,
  NetworkError,
  onSessionChange,
  setSession,
} from "./client";
import type { TokenResponse } from "./types";

function token(accessToken: string): TokenResponse {
  return {
    access_token: accessToken,
    expires_at: "2026-11-26T10:00:00Z",
    user: {
      id: "u1",
      email: "officer@example.test",
      full_name: "Officer",
      role: "LOAN_OFFICER",
      is_active: true,
      created_at: "2026-11-01T00:00:00Z",
      last_login_at: null,
      permissions: [],
    },
  };
}

describe("buildUrl", () => {
  it("repeats array parameters and drops empty ones", () => {
    expect(
      buildUrl("/applications", { status: ["SUBMITTED", "UNDER_REVIEW"], page: 2, q: "", x: null }),
    ).toBe("/api/v1/applications?status=SUBMITTED&status=UNDER_REVIEW&page=2");
  });
});

describe("api", () => {
  it("sends the bearer token, If-Match and Idempotency-Key headers", async () => {
    const server = mockApi({
      "POST /api/v1/loan-accounts/l1/repayments": { status: 201, body: { ok: true } },
    });
    setSession(token("access-1"));
    await api("/loan-accounts/l1/repayments", {
      method: "POST",
      body: { amount: "100.00" },
      ifMatch: 7,
      idempotencyKey: "key-1",
    });
    const [request] = server.requests;
    expect(request!.headers.get("Authorization")).toBe("Bearer access-1");
    expect(request!.headers.get("If-Match")).toBe('"7"');
    expect(request!.headers.get("Idempotency-Key")).toBe("key-1");
    expect(request!.body).toEqual({ amount: "100.00" });
  });

  it("refreshes once on 401 and retries with the new token", async () => {
    let calls = 0;
    const server = mockApi({
      "GET /api/v1/applications": (request) => {
        calls += 1;
        return request.headers.get("Authorization") === "Bearer fresh"
          ? { body: { items: [], total: 0 } }
          : problem(401, "Token expired");
      },
      "POST /api/v1/auth/refresh": { body: token("fresh") },
    });
    setSession(token("stale"));
    await expect(api("/applications")).resolves.toEqual({ items: [], total: 0 });
    expect(calls).toBe(2);
    expect(server.calls("POST", "/api/v1/auth/refresh")).toHaveLength(1);
  });

  it("shares one refresh between concurrent 401s, because tokens rotate", async () => {
    const server = mockApi({
      "GET /api/v1/a": (r) =>
        r.headers.get("Authorization") === "Bearer fresh" ? { body: 1 } : problem(401, "expired"),
      "GET /api/v1/b": (r) =>
        r.headers.get("Authorization") === "Bearer fresh" ? { body: 2 } : problem(401, "expired"),
      "POST /api/v1/auth/refresh": async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
        return { body: token("fresh") };
      },
    });
    setSession(token("stale"));
    await expect(Promise.all([api("/a"), api("/b")])).resolves.toEqual([1, 2]);
    expect(server.calls("POST", "/api/v1/auth/refresh")).toHaveLength(1);
  });

  it("ends the session when the refresh fails", async () => {
    mockApi({
      "GET /api/v1/applications": problem(401, "Token expired"),
      "POST /api/v1/auth/refresh": problem(401, "Refresh token reused"),
    });
    const seen: Array<string | null> = [];
    const stop = onSessionChange((user) => seen.push(user?.id ?? null));
    setSession(token("stale"));
    await expect(api("/applications")).rejects.toMatchObject({ status: 401 });
    expect(seen.at(-1)).toBeNull();
    stop();
  });

  it("never refreshes for sign-in calls", async () => {
    const server = mockApi({
      "POST /api/v1/auth/login": problem(401, "Invalid email or password"),
    });
    await expect(api("/auth/login", { method: "POST", body: {}, auth: false })).rejects.toThrow(
      "Invalid email or password",
    );
    expect(server.calls("POST", "/api/v1/auth/refresh")).toHaveLength(0);
  });

  it("maps validation problems to field errors", () => {
    const error = new ApiError(422, {
      detail: "One or more fields are invalid.",
      errors: [{ loc: ["body", "gross_amount"], msg: "Value error, must be positive" }],
      violations: ["net_amount: must not exceed gross_amount"],
    });
    expect(error.fieldErrors).toEqual({
      gross_amount: "must be positive",
      net_amount: "must not exceed gross_amount",
    });
  });

  it("explains permission and concurrency errors in plain words", () => {
    expect(errorMessage(new ApiError(403, { detail: "Missing permission" }))).toMatch(/permission/);
    expect(errorMessage(new ApiError(409, { code: "CONCURRENT_MODIFICATION" }))).toMatch(
      /Someone else/,
    );
  });

  it("reports an unreachable server as a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/applications")).rejects.toBeInstanceOf(NetworkError);
  });
});
