/** Repayments, settlement, financial records and the new-application preview. */
import { screen, waitFor, within } from "@testing-library/react";

import type { FinancialProfile, Product, SchedulePreview } from "@/api/types";
import { mockApi, problem } from "@/test/api";
import { APPLICATION, LOAN } from "@/test/fixtures";
import { expectAccessible, makeUser, renderWithProviders } from "@/test/render";

import { FinancialRecords } from "./applicants/FinancialRecords";
import { RepaymentPanel } from "./loans/PaymentPanels";
import { NewApplicationPage } from "./portal/NewApplicationPage";

const REPAY = "/api/v1/loan-accounts/loan-1/repayments";

function payment(balance: string) {
  return {
    status: 201,
    body: {
      replayed: false,
      account: LOAN,
      transaction: {
        id: "t1",
        loan_account_id: "loan-1",
        transaction_type: "REPAYMENT",
        amount: "2499.00",
        currency: "ZAR",
        value_date: "2026-11-26",
        channel: "SIMULATED",
        reference: "Simulated repayment",
        recorded_by: null,
        balance_after: balance,
        created_at: "2026-11-26T10:00:00Z",
      },
    },
  };
}

describe("RepaymentPanel", () => {
  it("explains that early money is a prepayment, and defaults to the arrears", async () => {
    mockApi({});
    const { container } = renderWithProviders(<RepaymentPanel account={LOAN} staff={false} />, {
      user: makeUser("APPLICANT"),
    });
    expect(await screen.findByText(/prepayment/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Amount/)).toHaveValue("2499.00");
    // Applicants can't back-date payments.
    expect(screen.queryByLabelText("Value date")).not.toBeInTheDocument();
    await expectAccessible(container);
  });

  it("reuses the idempotency key when the same payment is retried after a failure", async () => {
    let attempts = 0;
    const server = mockApi({
      [`POST ${REPAY}`]: () => {
        attempts += 1;
        return attempts === 1 ? problem(503, "Service unavailable") : payment("1104.19");
      },
    });
    const { user } = renderWithProviders(<RepaymentPanel account={LOAN} staff />, {
      user: makeUser("LOAN_OFFICER"),
    });
    await user.click(await screen.findByRole("button", { name: "Record repayment" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Service unavailable");
    await user.click(screen.getByRole("button", { name: "Record repayment" }));
    expect(await screen.findByText(/Payment recorded/)).toBeInTheDocument();
    const keys = server.calls("POST", REPAY).map((r) => r.headers.get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("uses a new key for the next payment and for a changed amount", async () => {
    const server = mockApi({ [`POST ${REPAY}`]: payment("1104.19") });
    const { user } = renderWithProviders(<RepaymentPanel account={LOAN} staff />, {
      user: makeUser("LOAN_OFFICER"),
    });
    await user.click(await screen.findByRole("button", { name: "Record repayment" }));
    await screen.findByText(/Payment recorded/);
    const amount = screen.getByLabelText(/Amount/);
    await user.clear(amount);
    await user.type(amount, "100.00");
    await user.click(screen.getByRole("button", { name: "Record repayment" }));
    await waitFor(() => expect(server.calls("POST", REPAY)).toHaveLength(2));
    const [first, second] = server.calls("POST", REPAY);
    expect(second!.headers.get("Idempotency-Key")).not.toBe(first!.headers.get("Idempotency-Key"));
    expect(second!.body).toEqual({ amount: "100.00", value_date: null });
  });

  it("rejects amounts that aren't money", async () => {
    const server = mockApi({});
    const { user } = renderWithProviders(<RepaymentPanel account={LOAN} staff />, {
      user: makeUser("LOAN_OFFICER"),
    });
    const amount = await screen.findByLabelText(/Amount/);
    await user.clear(amount);
    await user.type(amount, "12.345");
    await user.click(screen.getByRole("button", { name: "Record repayment" }));
    expect(await screen.findByText(/up to two decimals/)).toBeInTheDocument();
    expect(server.requests).toHaveLength(0);
  });
});

const PROFILE: FinancialProfile = {
  applicant: {
    id: "applicant-1",
    user_id: "u1",
    synthetic_reference: "SYN-1",
    first_name: "Thandi",
    last_name: "Mokoena",
    date_of_birth: "1990-01-01",
    email: "thandi@example.test",
    phone: "+27 82 555 0101",
    address_line1: "1 Main Road",
    address_line2: null,
    city: "Johannesburg",
    postal_code: "2001",
    country: "ZA",
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
  },
  employment: [],
  income: [
    {
      id: "inc-1",
      applicant_id: "applicant-1",
      source_type: "SALARY",
      frequency: "MONTHLY",
      gross_amount: "32000.00",
      net_amount: "24500.00",
      currency: "ZAR",
      verification_status: "DECLARED",
      is_active: true,
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
    },
  ],
  expenses: [],
  commitments: [],
};

describe("FinancialRecords", () => {
  it("lists declared income and invites the applicant to add the rest", async () => {
    mockApi({});
    const { container } = renderWithProviders(
      <FinancialRecords applicantId="applicant-1" profile={PROFILE} editable />,
      { user: makeUser("APPLICANT") },
    );
    const income = await screen.findByRole("table", { name: "Income" });
    expect(within(income).getByText("Declared")).toBeInTheDocument();
    expect(screen.getByText("No expense recorded")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("validates a new income record and sends money as strings", async () => {
    const server = mockApi({
      "POST /api/v1/applicants/applicant-1/income": { status: 201, body: PROFILE.income[0] },
    });
    const { user } = renderWithProviders(
      <FinancialRecords applicantId="applicant-1" profile={PROFILE} editable />,
      { user: makeUser("APPLICANT") },
    );
    await user.click(await screen.findByRole("button", { name: "Add income" }));
    const dialog = await screen.findByRole("dialog", { name: "Add income" });
    await user.selectOptions(within(dialog).getByLabelText(/Source/), "RENTAL");
    await user.type(within(dialog).getByLabelText(/Gross amount/), "5000");
    await user.type(within(dialog).getByLabelText(/Net amount/), "6000");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(
      await within(dialog).findByText("Net income can't be more than gross income"),
    ).toBeInTheDocument();
    const net = within(dialog).getByLabelText(/Net amount/);
    await user.clear(net);
    await user.type(net, "4500.50");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(server.calls("POST", "/api/v1/applicants/applicant-1/income")).toHaveLength(1),
    );
    expect(server.requests.at(-1)!.body).toEqual({
      source_type: "RENTAL",
      frequency: "MONTHLY",
      gross_amount: "5000",
      net_amount: "4500.50",
    });
  });

  it("is read-only for staff", async () => {
    mockApi({});
    renderWithProviders(
      <FinancialRecords applicantId="applicant-1" profile={PROFILE} editable={false} />,
    );
    await screen.findByRole("table", { name: "Income" });
    expect(screen.queryByRole("button", { name: /Add/ })).not.toBeInTheDocument();
  });
});

const PRODUCT: Product = {
  id: "product-1",
  code: "PERSONAL",
  name: "Personal unsecured loan",
  description: null,
  currency: "ZAR",
  min_amount: "5000.00",
  max_amount: "150000.00",
  min_term_months: 6,
  max_term_months: 60,
  annual_interest_rate: "0.215000",
  initiation_fee: "0.00",
  monthly_service_fee: "69.00",
  late_fee: "100.00",
  grace_period_days: 5,
  early_settlement_fee_rate: "0.000000",
  max_dti: "0.4500",
  warn_dti: "0.3500",
  min_residual_amount: "1500.00",
  min_residual_rate: "0.1000",
  is_active: true,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
};

const PREVIEW: SchedulePreview = {
  engine_version: "amortisation-1.0.0",
  currency: "ZAR",
  amount: "25000.00",
  initiation_fee: "0.00",
  principal: "25000.00",
  annual_interest_rate: "0.215000",
  term_months: 24,
  monthly_service_fee: "69.00",
  disbursement_date: "2026-11-26",
  level_instalment: "1290.79",
  first_total_due: "1359.79",
  total_interest: "5978.96",
  total_service_fees: "1656.00",
  total_repayable: "32634.96",
  cost_of_credit: "7634.96",
  instalments: [
    {
      number: 1,
      due_date: "2026-12-26",
      opening_balance: "25000.00",
      instalment: "1290.79",
      interest: "447.92",
      principal: "842.87",
      service_fee: "69.00",
      total_due: "1359.79",
      closing_balance: "24157.13",
    },
  ],
};

describe("NewApplicationPage", () => {
  it("previews the schedule from the server and saves a draft", async () => {
    const server = mockApi({
      "GET /api/v1/products": { items: [PRODUCT], total: 1, page: 1, page_size: 100 },
      "GET /api/v1/products/product-1/repayment-schedule": PREVIEW,
      "POST /api/v1/applications": {
        status: 201,
        body: { ...APPLICATION, status: "DRAFT", id: "app-9" },
      },
    });
    const { user, router } = renderWithProviders(<NewApplicationPage />, {
      user: makeUser("APPLICANT"),
    });
    await user.selectOptions(await screen.findByLabelText(/Loan product/), "product-1");
    await user.type(screen.getByLabelText(/Amount to borrow/), "25000");
    await user.type(screen.getByLabelText(/Term/), "24");
    expect(await screen.findByText(/1 290,79/)).toBeInTheDocument();
    const previewCall = server
      .calls("GET", "/api/v1/products/product-1/repayment-schedule")
      .at(-1)!;
    expect(previewCall.url.searchParams.get("amount")).toBe("25000");
    expect(previewCall.url.searchParams.get("term_months")).toBe("24");

    await user.selectOptions(screen.getByLabelText(/Purpose/), "HOME_IMPROVEMENT");
    await user.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/portal/applications/app-9"));
    expect(server.calls("POST", "/api/v1/applications")[0]!.body).toMatchObject({
      loan_product_id: "product-1",
      requested_amount: "25000",
      requested_term_months: 24,
      purpose: "HOME_IMPROVEMENT",
    });
  });

  it("checks the term against the product's limits", async () => {
    const server = mockApi({
      "GET /api/v1/products": { items: [PRODUCT], total: 1, page: 1, page_size: 100 },
      "GET /api/v1/products/product-1/repayment-schedule": problem(422, "term out of range"),
    });
    const { user } = renderWithProviders(<NewApplicationPage />, { user: makeUser("APPLICANT") });
    await user.selectOptions(await screen.findByLabelText(/Loan product/), "product-1");
    await user.type(screen.getByLabelText(/Amount to borrow/), "25000");
    await user.type(screen.getByLabelText(/Term/), "90");
    await user.selectOptions(screen.getByLabelText(/Purpose/), "VEHICLE");
    await user.click(screen.getByRole("button", { name: "Save as draft" }));
    expect(await screen.findByText("This product allows 6–60 months")).toBeInTheDocument();
    expect(server.calls("POST", "/api/v1/applications")).toHaveLength(0);
  });
});
