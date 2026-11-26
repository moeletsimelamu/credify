import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";

import type { CurrentUser, Role } from "@/api/types";
import { AuthContext, type AuthContextValue } from "@/features/auth/useAuth";

const PERMISSIONS: Record<Role, string[]> = {
  ADMIN: [
    "user:manage",
    "product:manage",
    "audit:read",
    "applicant:read_any",
    "application:read_any",
    "application:note",
    "application:assign",
    "application:decide",
    "loan:read_any",
    "loan:disburse",
    "repayment:record",
    "analytics:portfolio",
    "analytics:model",
    "model:manage",
  ],
  LOAN_OFFICER: [
    "applicant:read_any",
    "application:read_any",
    "application:note",
    "application:assign",
    "application:request_info",
    "application:recommend",
    "affordability:run",
    "loan:read_any",
    "loan:disburse",
    "repayment:record",
    "analytics:portfolio",
  ],
  CREDIT_ANALYST: [
    "applicant:read_any",
    "application:read_any",
    "application:note",
    "application:request_info",
    "application:decide",
    "affordability:run",
    "risk:run",
    "risk:annotate",
    "loan:read_any",
    "analytics:portfolio",
    "analytics:model",
  ],
  APPLICANT: ["applicant:write_own", "application:create_own", "repayment:record_own"],
};

export function makeUser(role: Role, overrides: Partial<CurrentUser> = {}): CurrentUser {
  return {
    id: `user-${role.toLowerCase()}`,
    email: `${role.toLowerCase()}@example.test`,
    full_name: `Test ${role.toLowerCase()}`,
    role,
    is_active: true,
    created_at: "2026-11-01T08:00:00Z",
    last_login_at: null,
    permissions: PERMISSIONS[role],
    ...overrides,
  };
}

interface Options {
  /** Signed-in user; null renders as signed out. */
  user?: CurrentUser | null;
  path?: string;
  route?: string;
  auth?: Partial<AuthContextValue>;
}

/** Render with a fresh query client, a memory router and a fixed signed-in user. */
export function renderWithProviders(ui: ReactElement, options: Options = {}) {
  const { user = makeUser("LOAN_OFFICER"), path = "/", route = "/", auth = {} } = options;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const value: AuthContextValue = {
    state: user ? { status: "authenticated", user } : { status: "anonymous", user: null },
    login: async () => user!,
    logout: async () => {},
    can: (permission) => user?.permissions.includes(permission) ?? false,
    ...auth,
  };
  const router = createMemoryRouter(
    [
      { path, element: ui },
      { path: "*", element: <p>Elsewhere</p> },
    ],
    { initialEntries: [route] },
  );
  const result = render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={value}>
        <RouterProvider router={router} />
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
  return { ...result, router, queryClient, user: userEvent.setup() };
}

/** Axe accessibility check (colour contrast needs a real renderer, so it is skipped). */
export async function expectAccessible(container: Element) {
  const results = await axe.run(container, {
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  const messages = results.violations.map(
    (violation) =>
      `${violation.id}: ${violation.help} → ${violation.nodes.map((n) => n.target.join(" ")).join(", ")}`,
  );
  if (messages.length) throw new Error(`Accessibility violations:\n${messages.join("\n")}`);
}
