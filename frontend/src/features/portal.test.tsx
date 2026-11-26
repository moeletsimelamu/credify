/** Applicant application page, portfolio reporting and the role-aware navigation. */
import { screen, waitFor, within } from "@testing-library/react";

import type { ApplicationDetail, Portfolio } from "@/api/types";
import { OfficeLayout } from "@/app/layouts";
import { mockApi } from "@/test/api";
import { APPLICATION } from "@/test/fixtures";
import { expectAccessible, makeUser, renderWithProviders } from "@/test/render";

import { MyApplicationPage } from "./portal/MyApplicationPage";
import { PortfolioPage } from "./reporting/PortfolioPage";

const BASE = "/api/v1/applications/app-1";

function portalMocks(application: ApplicationDetail, extra: Record<string, unknown> = {}) {
  return mockApi({
    [`GET ${BASE}`]: application,
    "GET /api/v1/products": {
      items: [{ id: "product-1", name: "Personal unsecured loan" }],
      total: 1,
    },
    [`GET ${BASE}/information-requests`]: [],
    [`GET ${BASE}/status-history`]: [
      {
        id: "h1",
        from_status: "SUBMITTED",
        to_status: "PENDING_REVIEW",
        actor_user_id: null,
        reason: "Affordability assessment v1: AFFORDABLE",
        created_at: "2026-11-20T08:00:00Z",
      },
    ],
    [`GET ${BASE}/documents`]: [],
    [`GET ${BASE}/repayment-schedule`]: {
      currency: "ZAR",
      level_instalment: "1290.79",
      first_total_due: "1359.79",
      annual_interest_rate: "0.215000",
      amount: "25000.00",
      initiation_fee: "0.00",
      principal: "25000.00",
      total_interest: "5978.96",
      total_service_fees: "1656.00",
      total_repayable: "32634.96",
      cost_of_credit: "7634.96",
      term_months: 24,
      disbursement_date: "2026-11-26",
      instalments: [],
    },
    ...extra,
  });
}

const route = { path: "/portal/applications/:applicationId", route: "/portal/applications/app-1" };

describe("MyApplicationPage", () => {
  it("lets the applicant submit a draft, citing the version they saw", async () => {
    const draft = {
      ...APPLICATION,
      status: "DRAFT" as const,
      version: 3,
      available_actions: ["SUBMIT" as const, "WITHDRAW" as const],
    };
    const server = portalMocks(draft, {
      [`POST ${BASE}/submit`]: { ...draft, status: "PENDING_REVIEW" },
    });
    const { user, container } = renderWithProviders(<MyApplicationPage />, {
      user: makeUser("APPLICANT"),
      ...route,
    });
    expect(await screen.findByLabelText(/Amount/)).toHaveValue("25000.00");
    expect(screen.getByRole("list", { name: "Application progress" })).toBeInTheDocument();
    await expectAccessible(container);
    await user.click(screen.getByRole("button", { name: "Submit application" }));
    await waitFor(() => expect(server.calls("POST", `${BASE}/submit`)).toHaveLength(1));
    expect(server.calls("POST", `${BASE}/submit`)[0]!.headers.get("If-Match")).toBe('"3"');
  });

  it("asks before withdrawing", async () => {
    const pending = {
      ...APPLICATION,
      status: "PENDING_REVIEW" as const,
      available_actions: ["WITHDRAW" as const],
    };
    const server = portalMocks(pending, {
      [`POST ${BASE}/withdraw`]: { ...pending, status: "WITHDRAWN" },
    });
    const { user } = renderWithProviders(<MyApplicationPage />, {
      user: makeUser("APPLICANT"),
      ...route,
    });
    await user.click(await screen.findByRole("button", { name: "Withdraw" }));
    const dialog = await screen.findByRole("dialog", { name: "Withdraw this application?" });
    expect(server.calls("POST", `${BASE}/withdraw`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Withdraw application" }));
    await waitFor(() => expect(server.calls("POST", `${BASE}/withdraw`)).toHaveLength(1));
  });

  it("shows the decision with the applicant-facing reasons", async () => {
    const declined = {
      ...APPLICATION,
      status: "DECLINED" as const,
      decided_at: "2026-11-22T08:00:00Z",
    };
    portalMocks(declined, {
      [`GET ${BASE}/decision`]: {
        application_id: "app-1",
        decision: "DECLINED",
        reasons: [
          {
            code: "HIGH_DTI",
            outcome: "DECLINED",
            label: "Debt-to-income too high",
            applicant_text: "Your total monthly debt repayments would be too high.",
          },
        ],
        approved_amount: null,
        approved_term_months: null,
        decided_at: "2026-11-22T08:00:00Z",
      },
    });
    renderWithProviders(<MyApplicationPage />, { user: makeUser("APPLICANT"), ...route });
    expect(await screen.findByText("Your application was declined")).toBeInTheDocument();
    expect(
      screen.getByText("Your total monthly debt repayments would be too high."),
    ).toBeInTheDocument();
    expect(screen.getByText(/made by a person/)).toBeInTheDocument();
  });
});

const PORTFOLIO: Portfolio = {
  date_from: "2025-11-27",
  date_to: "2026-11-26",
  collections_through: "2026-11-26",
  note: "Synthetic, educational data.",
  currencies: [
    {
      currency: "ZAR",
      applications_by_status: { UNDER_REVIEW: 4, APPROVED: 16, DECLINED: 5 },
      decisions_approved: 16,
      decisions_declined: 5,
      approval_rate: "0.7619",
      loans_by_status: { ACTIVE: 9 },
      loans_disbursed: 13,
      principal_disbursed: "435472.50",
      outstanding_principal: "355682.05",
      outstanding_principal_in_arrears: "73119.80",
      arrears_rate: "0.2056",
      repayments_received: "218532.53",
      amount_due: "166510.68",
      amount_collected: "154535.19",
      collection_rate: "0.9281",
      arrears_buckets: [
        { bucket: "CURRENT", accounts: 9, outstanding_principal: "282562.25" },
        { bucket: "90+", accounts: 1, outstanding_principal: "36718.83" },
      ],
    },
  ],
};

describe("PortfolioPage", () => {
  it("shows KPIs, arrears and a monthly trend from the analytics endpoint", async () => {
    mockApi({ "GET /api/v1/analytics/portfolio": PORTFOLIO });
    const { container } = renderWithProviders(<PortfolioPage />, {
      user: makeUser("CREDIT_ANALYST"),
    });
    expect(await screen.findByRole("heading", { name: "ZAR book" })).toBeInTheDocument();
    expect(screen.getByText(/76,2%/)).toBeInTheDocument();
    expect(screen.getByText(/20,6%/)).toHaveClass("text-critical");
    const buckets = screen.getByRole("table", { name: "Arrears buckets" });
    expect(within(buckets).getByText("Current")).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "Monthly trend" })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("asks for a valid custom range", async () => {
    mockApi({ "GET /api/v1/analytics/portfolio": PORTFOLIO });
    const { user } = renderWithProviders(<PortfolioPage />, { user: makeUser("CREDIT_ANALYST") });
    await user.selectOptions(await screen.findByLabelText("Period"), "custom");
    const from = screen.getByLabelText("From");
    await user.clear(from);
    await user.type(from, "2030-01-01");
    expect(
      await screen.findByText("The end date must be on or after the start date"),
    ).toBeInTheDocument();
  });
});

describe("OfficeLayout", () => {
  it("shows each role only the sections it may use", async () => {
    mockApi({});
    renderWithProviders(<OfficeLayout />, { user: makeUser("LOAN_OFFICER"), path: "/" });
    const nav = await screen.findByRole("navigation", { name: "Back office" });
    expect(within(nav).getByRole("link", { name: "Work queue" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Portfolio" })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Risk models" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.getByText(/synthetic data only/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute(
      "href",
      "#main",
    );
  });

  it("gives administrators the admin screens", async () => {
    mockApi({});
    renderWithProviders(<OfficeLayout />, { user: makeUser("ADMIN"), path: "/" });
    const nav = await screen.findByRole("navigation", { name: "Back office" });
    for (const name of ["Users", "Products", "Audit log", "Risk models"]) {
      expect(within(nav).getByRole("link", { name })).toBeInTheDocument();
    }
  });
});
