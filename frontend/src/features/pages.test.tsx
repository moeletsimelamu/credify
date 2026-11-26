/** Whole pages: workspace, loan account, profile, products, audit, home and session restore. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";

import { mockApi, problem } from "@/test/api";
import { AFFORDABILITY, APPLICATION, LOAN, RISK } from "@/test/fixtures";
import { expectAccessible, makeUser, renderWithProviders } from "@/test/render";

import { AuditPage } from "./admin/AuditPage";
import { ProductsPage } from "./admin/ProductsPage";
import { ProfilePage } from "./applicants/ProfilePage";
import { AuthProvider } from "./auth/AuthProvider";
import { useAuth } from "./auth/useAuth";
import { OfficeLoanPage } from "./loans/LoanPage";
import { WorkspacePage } from "./office/WorkspacePage";
import { PortalHome } from "./portal/PortalHome";

const APP = "/api/v1/applications/app-1";

describe("WorkspacePage", () => {
  function mocks() {
    return mockApi({
      [`GET ${APP}`]: {
        ...APPLICATION,
        available_actions: ["REQUEST_INFORMATION", "RECOMMEND_APPROVAL", "RECOMMEND_REJECTION"],
      },
      "GET /api/v1/products": {
        items: [{ id: "product-1", name: "Personal unsecured loan" }],
        total: 1,
      },
      "GET /api/v1/applicants/applicant-1": {
        id: "applicant-1",
        first_name: "Naledi",
        last_name: "Pillay",
        synthetic_reference: "SYN-0DFFE9B650",
      },
      "GET /api/v1/staff": [
        { id: "user-loan_officer", full_name: "Lindiwe Officer", role: "LOAN_OFFICER" },
      ],
      [`GET ${APP}/affordability-assessments`]: [AFFORDABILITY],
      [`GET ${APP}/risk-assessments`]: [RISK],
      [`GET ${APP}/notes`]: [],
      [`GET ${APP}/information-requests`]: [],
      [`GET ${APP}/documents`]: [],
      [`GET ${APP}/recommendations`]: [],
      [`POST ${APP}/request-information`]: { status: 201, body: {} },
    });
  }

  it("shows the snapshot rail and moves between tabs with the keyboard", async () => {
    mocks();
    const { user, container } = renderWithProviders(<WorkspacePage />, {
      user: makeUser("LOAN_OFFICER"),
      path: "/office/applications/:applicationId",
      route: "/office/applications/app-1",
    });
    expect(
      await screen.findByRole("heading", { name: /CRD-2026-000042 · Naledi Pillay/ }),
    ).toBeInTheDocument();
    const rail = screen.getByRole("complementary");
    expect(within(rail).getByText("Band D")).toBeInTheDocument();
    expect(within(rail).getByText("Lindiwe Officer")).toBeInTheDocument();
    expect(within(rail).getByText(/1.290,79/)).toBeInTheDocument();

    const overview = screen.getByRole("tab", { name: "Overview" });
    expect(overview).toHaveAttribute("aria-selected", "true");
    await user.click(overview);
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(document.activeElement).toHaveAccessibleName("Affordability");
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Affordability" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(await screen.findByRole("table", { name: "Affordability rules" })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("lets an officer ask the applicant for more information", async () => {
    const server = mocks();
    const { user } = renderWithProviders(<WorkspacePage />, {
      user: makeUser("LOAN_OFFICER"),
      path: "/office/applications/:applicationId",
      route: "/office/applications/app-1",
    });
    await user.click(await screen.findByRole("button", { name: "Request information" }));
    await user.type(
      screen.getByLabelText(/Ask the applicant/),
      "Please upload your latest payslip.",
    );
    await user.click(screen.getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(server.calls("POST", `${APP}/request-information`)).toHaveLength(1));
    const [request] = server.calls("POST", `${APP}/request-information`);
    expect(request!.body).toEqual({ message: "Please upload your latest payslip." });
    expect(request!.headers.get("If-Match")).toBe('"7"');
  });

  it("shows a friendly error for an application that doesn't exist", async () => {
    mockApi({ [`GET ${APP}`]: problem(404, "Application not found") });
    renderWithProviders(<WorkspacePage />, {
      path: "/office/applications/:applicationId",
      route: "/office/applications/app-1",
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Application not found");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("OfficeLoanPage", () => {
  it("shows the position, terms, schedule and allocated payments", async () => {
    mockApi({
      "GET /api/v1/loan-accounts/loan-1": LOAN,
      "GET /api/v1/loan-accounts/loan-1/schedule": {
        loan_account_id: "loan-1",
        currency: "ZAR",
        current_version: 1,
        instalments: [
          {
            number: 1,
            due_date: "2026-07-04",
            opening_balance: "6265.00",
            interest_due: "143.57",
            principal_due: "925.93",
            service_fee_due: "60.00",
            total_due: "1129.50",
            closing_balance: "5339.07",
            interest_paid: "143.57",
            principal_paid: "925.93",
            service_fee_paid: "60.00",
            status: "PAID",
            schedule_version: 1,
          },
        ],
      },
      "GET /api/v1/loan-accounts/loan-1/schedules": [
        {
          version: 1,
          reason: "ORIGINATION",
          engine_version: "amortisation-1.0.0",
          first_instalment_number: 1,
          is_current: true,
          transaction_id: null,
          created_at: "2026-06-04T08:00:00Z",
        },
      ],
      "GET /api/v1/loan-accounts/loan-1/transactions": [
        {
          id: "t1",
          loan_account_id: "loan-1",
          transaction_type: "REPAYMENT",
          amount: "1129.50",
          currency: "ZAR",
          value_date: "2026-07-04",
          channel: "SIMULATED",
          reference: "Instalment 1",
          recorded_by: null,
          balance_after: "5339.07",
          created_at: "2026-07-04T08:00:00Z",
          allocations: [
            {
              component: "INTEREST",
              amount: "143.57",
              schedule_instalment_id: null,
              loan_charge_id: null,
            },
            {
              component: "PRINCIPAL",
              amount: "925.93",
              schedule_instalment_id: null,
              loan_charge_id: null,
            },
          ],
        },
      ],
      "GET /api/v1/loan-accounts/loan-1/charges": [],
    });
    const { user } = renderWithProviders(<OfficeLoanPage />, {
      user: makeUser("LOAN_OFFICER"),
      path: "/office/loans/:loanId",
      route: "/office/loans/loan-1",
    });
    expect(await screen.findByText("53 days past due · bucket 31-60")).toBeInTheDocument();
    expect(screen.getByText(/27,5%/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Record repayment" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Schedule" }));
    const schedule = await screen.findByRole("table", { name: "Effective repayment schedule" });
    expect(within(schedule).getByText("Paid")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Payments & charges" }));
    const transactions = await screen.findByRole("table", { name: "Transactions" });
    expect(within(transactions).getByText("Interest")).toBeInTheDocument();
    expect(within(transactions).getByText("Principal")).toBeInTheDocument();
    expect(await screen.findByText("No charges")).toBeInTheDocument();
  });
});

describe("ProfilePage", () => {
  it("creates the applicant profile with validated details", async () => {
    const server = mockApi({
      "GET /api/v1/applicants/me": problem(404, "No applicant profile"),
      "POST /api/v1/applicants": {
        status: 201,
        body: {
          id: "applicant-1",
          synthetic_reference: "SYN-1",
          created_at: "2026-11-26T00:00:00Z",
          first_name: "Thandi",
          last_name: "Mokoena",
          date_of_birth: "1990-05-01",
          email: "",
          phone: "+27 82 555 0101",
          address_line1: "1 Main Road",
          address_line2: null,
          city: "Johannesburg",
          postal_code: "2001",
          country: "ZA",
        },
      },
    });
    const { user, container } = renderWithProviders(<ProfilePage />, {
      user: makeUser("APPLICANT"),
    });
    expect(
      await screen.findByRole("heading", { name: "Create your applicant profile" }),
    ).toBeInTheDocument();
    await expectAccessible(container);
    await user.type(screen.getByLabelText(/First name/), "Thandi");
    await user.type(screen.getByLabelText(/Last name/), "Mokoena");
    await user.type(screen.getByLabelText(/Date of birth/), "1990-05-01");
    await user.type(screen.getByLabelText(/^Phone/), "not a phone");
    await user.type(screen.getByLabelText(/^Address \*/), "1 Main Road");
    await user.type(screen.getByLabelText(/City/), "Johannesburg");
    await user.type(screen.getByLabelText(/Postal code/), "2001");
    await user.click(screen.getByRole("button", { name: "Create my profile" }));
    expect(await screen.findByText(/Enter a phone number/)).toBeInTheDocument();
    const phone = screen.getByLabelText(/^Phone/);
    await user.clear(phone);
    await user.type(phone, "+27 82 555 0101");
    await user.click(screen.getByRole("button", { name: "Create my profile" }));
    await waitFor(() => expect(server.calls("POST", "/api/v1/applicants")).toHaveLength(1));
    expect(server.calls("POST", "/api/v1/applicants")[0]!.body).toMatchObject({
      first_name: "Thandi",
      email: null,
      address_line2: null,
      country: "ZA",
    });
  });
});

describe("PortalHome", () => {
  it("guides a new applicant through the first steps", async () => {
    mockApi({
      "GET /api/v1/applicants/me": problem(404, "No applicant profile"),
      "GET /api/v1/applications": { items: [], total: 0, page: 1, page_size: 50 },
      "GET /api/v1/loan-accounts": { items: [], total: 0, page: 1, page_size: 20 },
      "GET /api/v1/products": { items: [], total: 0 },
    });
    renderWithProviders(<PortalHome />, { user: makeUser("APPLICANT") });
    expect(await screen.findByText("Get started")).toBeInTheDocument();
    expect(await screen.findByText("No loans yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add my details first" })).toHaveAttribute(
      "href",
      "/portal/profile",
    );
  });
});

describe("ProductsPage", () => {
  it("validates product terms before saving", async () => {
    const server = mockApi({ "GET /api/v1/products": { items: [], total: 0 } });
    const { user } = renderWithProviders(<ProductsPage />, { user: makeUser("ADMIN") });
    await user.click(await screen.findByRole("button", { name: "Add product" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a loan product" });
    await user.type(within(dialog).getByLabelText(/^Code/), "bad code");
    await user.type(within(dialog).getByLabelText(/Annual interest rate/), "21.5");
    await user.click(within(dialog).getByRole("button", { name: "Create product" }));
    expect(await within(dialog).findByText(/Use 3–50 capitals/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Interest rate must be a fraction/)).toBeInTheDocument();
    expect(server.calls("POST", "/api/v1/products")).toHaveLength(0);
  });
});

describe("AuditPage", () => {
  it("filters the audit log by entity", async () => {
    const server = mockApi({
      "GET /api/v1/audit-events": {
        items: [
          {
            id: "e1",
            occurred_at: "2026-11-26T09:54:00Z",
            actor_user_id: "u1",
            actor_role: "ADMIN",
            action: "model.activated",
            entity_type: "model_version",
            entity_id: "m1",
            request_id: "abcdef1234",
            ip_address: null,
            changes: { status: ["CANDIDATE", "ACTIVE"] },
            context: null,
          },
        ],
        total: 1,
        page: 1,
        page_size: 25,
      },
    });
    const { user } = renderWithProviders(<AuditPage />, { user: makeUser("ADMIN") });
    expect(await screen.findByText("model.activated")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Entity"), "model_version");
    await waitFor(() =>
      expect(
        server.calls("GET", "/api/v1/audit-events").at(-1)!.url.searchParams.get("entity_type"),
      ).toBe("model_version"),
    );
  });
});

function Probe() {
  const { state } = useAuth();
  return (
    <p>
      {state.status === "authenticated" ? `Signed in as ${state.user.full_name}` : state.status}
    </p>
  );
}

describe("AuthProvider", () => {
  function renderProvider() {
    const client = new QueryClient();
    return render(
      <QueryClientProvider client={client}>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </QueryClientProvider>,
    );
  }

  it("restores the session from the refresh cookie", async () => {
    mockApi({
      "POST /api/v1/auth/refresh": {
        access_token: "a",
        expires_at: "2026-11-26T10:00:00Z",
        user: makeUser("CREDIT_ANALYST", { full_name: "Priya Analyst" }),
      },
    });
    renderProvider();
    expect(await screen.findByText("Signed in as Priya Analyst")).toBeInTheDocument();
  });

  it("falls back to signed out when there is no session", async () => {
    mockApi({ "POST /api/v1/auth/refresh": problem(401, "No refresh token") });
    renderProvider();
    expect(await screen.findByText("anonymous")).toBeInTheDocument();
  });
});
