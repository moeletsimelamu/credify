import { screen, waitFor } from "@testing-library/react";

import { mockApi } from "@/test/api";
import { expectAccessible, makeUser, renderWithProviders } from "@/test/render";

import { LoginPage, RegisterPage } from "./AuthPages";
import { RequireAuth } from "./RequireAuth";

describe("LoginPage", () => {
  it("validates before calling the API", async () => {
    const login = vi.fn();
    const { user, container } = renderWithProviders(<LoginPage />, {
      user: null,
      path: "/login",
      route: "/login",
      auth: { login },
    });
    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Email is required")).toBeInTheDocument();
    expect(screen.getByText("Password is required")).toBeInTheDocument();
    expect(screen.getByLabelText(/Email/)).toHaveFocus();
    expect(login).not.toHaveBeenCalled();
    await expectAccessible(container);
  });

  it("shows the server's message when sign-in fails", async () => {
    const login = vi.fn().mockRejectedValue(new Error("Invalid email or password"));
    const { user } = renderWithProviders(<LoginPage />, {
      user: null,
      path: "/login",
      route: "/login",
      auth: { login },
    });
    await user.type(await screen.findByLabelText(/Email/), "someone@example.test");
    await user.type(screen.getByLabelText(/Password/), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(login).toHaveBeenCalledWith("someone@example.test", "wrong-password");
  });

  it("returns a signed-in user to a same-app ?next= page", async () => {
    const { router } = renderWithProviders(<LoginPage />, {
      user: makeUser("CREDIT_ANALYST"),
      path: "/login",
      route: "/login?next=%2Foffice%2Fmodels",
    });
    await waitFor(() => expect(router.state.location.pathname).toBe("/office/models"));
  });

  it("ignores an off-site ?next= and goes home instead", async () => {
    const { router } = renderWithProviders(<LoginPage />, {
      user: makeUser("APPLICANT"),
      path: "/login",
      route: "/login?next=%2F%2Fevil.example",
    });
    await waitFor(() => expect(router.state.location.pathname).toBe("/portal"));
  });
});

describe("RegisterPage", () => {
  it("checks the password rules and that both passwords match", async () => {
    const { user } = renderWithProviders(<RegisterPage />, {
      user: null,
      path: "/register",
      route: "/register",
    });
    await user.type(await screen.findByLabelText(/Full name/), "Thandi Mokoena");
    await user.type(screen.getByLabelText(/^Email/), "thandi@example.test");
    await user.type(screen.getByLabelText(/^Password/), "short");
    await user.type(screen.getByLabelText(/Confirm password/), "different");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Use at least 12 characters")).toBeInTheDocument();
    expect(screen.getByText("Passwords don't match")).toBeInTheDocument();
  });

  it("puts server validation messages on the right field", async () => {
    mockApi({
      "POST /api/v1/auth/register": {
        status: 422,
        body: {
          status: 422,
          code: "VALIDATION_FAILED",
          detail: "One or more fields are invalid.",
          errors: [{ loc: ["body", "email"], msg: "An account with this email already exists" }],
        },
      },
    });
    const { user } = renderWithProviders(<RegisterPage />, {
      user: null,
      path: "/register",
      route: "/register",
    });
    await user.type(await screen.findByLabelText(/Full name/), "Thandi Mokoena");
    await user.type(screen.getByLabelText(/^Email/), "thandi@example.test");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase");
    await user.type(screen.getByLabelText(/Confirm password/), "a long passphrase");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(
      await screen.findByText("An account with this email already exists"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Email/)).toBeInvalid();
  });
});

describe("RequireAuth", () => {
  it("sends a signed-out visitor to sign in without remembering the last page", async () => {
    const { router } = renderWithProviders(<RequireAuth area="office" />, {
      user: null,
      path: "/office",
      route: "/office",
      auth: { state: { status: "anonymous", user: null, reason: "signed-out" } },
    });
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).toBe("");
  });

  it("remembers the page when the session expired", async () => {
    const { router } = renderWithProviders(<RequireAuth area="office" />, {
      user: null,
      path: "/office/*",
      route: "/office/loans",
      auth: { state: { status: "anonymous", user: null, reason: "expired" } },
    });
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).toBe("?next=%2Foffice%2Floans&expired=1");
  });

  it("keeps applicants out of the back office", async () => {
    const { router } = renderWithProviders(<RequireAuth area="office" />, {
      user: makeUser("APPLICANT"),
      path: "/office",
      route: "/office",
    });
    await waitFor(() => expect(router.state.location.pathname).toBe("/portal"));
  });
});
