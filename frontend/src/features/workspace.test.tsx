/** The credit workspace: affordability, the advisory risk panel and the human decision. */
import { screen, waitFor, within } from "@testing-library/react";

import type { ApplicationDetail, Recommendation } from "@/api/types";
import { mockApi, problem } from "@/test/api";
import { AFFORDABILITY, APPLICATION, REASONS, RISK } from "@/test/fixtures";
import { expectAccessible, makeUser, renderWithProviders } from "@/test/render";

import { AffordabilityPanel } from "./affordability/AffordabilityPanel";
import { DecisionPanel } from "./decisions/DecisionPanel";
import { RiskPanel } from "./risk/RiskPanel";

const BASE = "/api/v1/applications/app-1";
const plain = (text: string | null) => (text ?? "").replace(/[\u00a0\u202f]/g, " ");

describe("AffordabilityPanel", () => {
  it("shows the waterfall breakdown with the server's exact figures and the rule results", async () => {
    mockApi({ [`GET ${BASE}/affordability-assessments`]: [AFFORDABILITY] });
    const { container } = renderWithProviders(
      <AffordabilityPanel applicationId="app-1" status="UNDER_REVIEW" />,
      {
        user: makeUser("LOAN_OFFICER"),
      },
    );
    const breakdown = await screen.findByRole("table", { name: "Monthly affordability breakdown" });
    const rows = within(breakdown)
      .getAllByRole("row")
      .map((row) => plain(row.textContent));
    expect(rows).toEqual([
      "Net incomeR 24 500,10",
      "less Living expenses−R 9 000,00",
      "less Existing debt−R 1 500,05",
      "less New instalment−R 1 290,79",
      "ResidualR 12 709,26",
      "Required bufferR 2 450,01",
    ]);
    const rules = screen.getByRole("table", { name: "Affordability rules" });
    expect(within(rules).getByText("Debt-to-income ceiling").closest("tr")).toHaveTextContent(
      "Failed",
    );
    expect(screen.getByRole("img", { name: /Affordability waterfall/ })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("offers a re-run only while the application is in review", async () => {
    mockApi({ [`GET ${BASE}/affordability-assessments`]: [AFFORDABILITY] });
    renderWithProviders(
      <AffordabilityPanel applicationId="app-1" status="RECOMMENDED_APPROVAL" />,
      {
        user: makeUser("LOAN_OFFICER"),
      },
    );
    await screen.findByRole("table", { name: "Affordability rules" });
    expect(screen.queryByRole("button", { name: /Run a new assessment/ })).not.toBeInTheDocument();
  });

  it("runs a new version on request", async () => {
    const server = mockApi({
      [`GET ${BASE}/affordability-assessments`]: [AFFORDABILITY],
      [`POST ${BASE}/affordability-assessments`]: {
        status: 201,
        body: { ...AFFORDABILITY, version: 3 },
      },
    });
    const { user } = renderWithProviders(
      <AffordabilityPanel applicationId="app-1" status="UNDER_REVIEW" />,
      {
        user: makeUser("LOAN_OFFICER"),
      },
    );
    await user.click(await screen.findByRole("button", { name: "Run a new assessment" }));
    await waitFor(() =>
      expect(server.calls("POST", `${BASE}/affordability-assessments`)).toHaveLength(1),
    );
  });
});

describe("RiskPanel", () => {
  it("labels the score as experimental and advisory and explains it factor by factor", async () => {
    mockApi({
      [`GET ${BASE}/risk-assessments`]: [RISK],
      [`GET ${BASE}/credit-bureau-report`]: {
        id: "b1",
        application_id: "app-1",
        provider: "SIMULATED",
        simulator_version: "bureau-sim-1.0.0",
        missed_payments_12m: 1,
        months_since_delinquency: 7,
        created_at: "2026-11-20T08:00:00Z",
      },
    });
    const { container } = renderWithProviders(
      <RiskPanel applicationId="app-1" status="UNDER_REVIEW" />,
      {
        user: makeUser("CREDIT_ANALYST"),
      },
    );
    expect(
      await screen.findByText("Experimental model output — advisory only"),
    ).toBeInTheDocument();
    expect(plain((await screen.findByText(/17,23%/)).textContent)).toBe("17,23%");
    expect(screen.getByText("Band D")).toBeInTheDocument();
    expect(screen.getByText(/above — review carefully/)).toBeInTheDocument();
    const factors = screen.getByRole("table", { name: "All factor contributions" });
    const order = within(factors)
      .getAllByRole("row")
      .slice(1)
      .map((row) => (row as HTMLTableRowElement).cells[0]!.textContent);
    expect(order).toEqual(["Loan-to-income", "Term (months)", "Loan purpose"]);
    expect(within(factors).getByText("+0.42")).toBeInTheDocument();
    expect(await screen.findByText("bureau-sim-1.0.0")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("explains a failed scoring attempt without blocking review", async () => {
    mockApi({
      [`GET ${BASE}/risk-assessments`]: [
        {
          ...RISK,
          status: "FAILED",
          probability_of_default: null,
          risk_band: null,
          explanation: null,
          failure_reason: "no credit-risk model is active",
        },
      ],
      [`GET ${BASE}/credit-bureau-report`]: problem(404, "Not found"),
    });
    renderWithProviders(<RiskPanel applicationId="app-1" status="UNDER_REVIEW" />, {
      user: makeUser("LOAN_OFFICER"),
    });
    expect(
      await screen.findByText("The model could not score this application"),
    ).toBeInTheDocument();
    expect(screen.getByText(/no credit-risk model is active/)).toBeInTheDocument();
    // Officers don't hold risk:run.
    expect(screen.queryByRole("button", { name: "Score again" })).not.toBeInTheDocument();
    expect(await screen.findByText("No bureau report yet")).toBeInTheDocument();
  });
});

const RECOMMENDATION: Recommendation = {
  id: "rec-1",
  application_id: "app-1",
  recommended_by: "user-loan_officer",
  recommendation: "APPROVE",
  rationale: "Affordable with a healthy buffer.",
  affordability_assessment_id: "aff-2",
  credit_risk_assessment_id: "risk-3",
  created_at: "2026-11-21T10:00:00Z",
};

const AWAITING: ApplicationDetail = {
  ...APPLICATION,
  status: "RECOMMENDED_APPROVAL",
  available_actions: ["APPROVE", "DECLINE", "SEND_BACK"],
};

function renderDecision(user = makeUser("CREDIT_ANALYST")) {
  return renderWithProviders(
    <DecisionPanel
      application={AWAITING}
      evidence={{ affordability: AFFORDABILITY, risk: RISK }}
      staff={[]}
    />,
    { user },
  );
}

describe("DecisionPanel", () => {
  it("shows only the reasons that belong to the chosen outcome", async () => {
    mockApi({
      [`GET ${BASE}/recommendations`]: [RECOMMENDATION],
      "GET /api/v1/decision-reason-codes": REASONS,
    });
    const { user } = renderDecision();
    await screen.findByRole("button", { name: "Record decision" });
    expect(screen.queryByLabelText("Meets lending criteria")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Decline"));
    expect(screen.getByLabelText("Debt-to-income too high")).toBeInTheDocument();
    expect(screen.queryByLabelText("Meets lending criteria")).not.toBeInTheDocument();
  });

  it("requires a reason, an explanation and the advisory acknowledgement", async () => {
    const server = mockApi({
      [`GET ${BASE}/recommendations`]: [RECOMMENDATION],
      "GET /api/v1/decision-reason-codes": REASONS,
    });
    const { user, container } = renderDecision();
    await user.click(await screen.findByRole("button", { name: "Record decision" }));
    expect(await screen.findByText("Choose approve or decline")).toBeInTheDocument();
    expect(screen.getByText("Choose at least one reason")).toBeInTheDocument();
    expect(screen.getByText("Explain the decision")).toBeInTheDocument();
    expect(
      screen.getByText(/Confirm that you treated the model output as advisory/),
    ).toBeInTheDocument();
    expect(server.calls("POST", `${BASE}/decision`)).toHaveLength(0);
    await expectAccessible(container);
  });

  it("records the decision citing the latest evidence, with If-Match", async () => {
    const server = mockApi({
      [`GET ${BASE}/recommendations`]: [RECOMMENDATION],
      "GET /api/v1/decision-reason-codes": REASONS,
      [`POST ${BASE}/decision`]: { status: 201, body: {} },
    });
    const { user } = renderDecision();
    await user.click(await screen.findByLabelText("Approve"));
    await user.click(screen.getByLabelText("Meets lending criteria"));
    await user.type(screen.getByLabelText(/Explanation/), "Within policy on every rule.");
    await user.click(screen.getByLabelText(/treated the experimental model output as advisory/));
    await user.click(screen.getByRole("button", { name: "Record decision" }));
    await waitFor(() => expect(server.calls("POST", `${BASE}/decision`)).toHaveLength(1));
    const [request] = server.calls("POST", `${BASE}/decision`);
    expect(request!.headers.get("If-Match")).toBe('"7"');
    expect(request!.body).toEqual({
      decision: "APPROVED",
      reason_codes: ["MEETS_CRITERIA"],
      explanation: "Within policy on every rule.",
      model_output_acknowledged: true,
      affordability_assessment_id: "aff-2",
      credit_risk_assessment_id: "risk-3",
      approved_amount: null,
      approved_term_months: null,
    });
  });

  it("keeps the recommender from deciding (maker–checker)", async () => {
    mockApi({
      [`GET ${BASE}/recommendations`]: [{ ...RECOMMENDATION, recommended_by: "user-admin" }],
      "GET /api/v1/decision-reason-codes": REASONS,
    });
    renderDecision(makeUser("ADMIN"));
    expect(
      await screen.findByText(/a different analyst or administrator must decide/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record decision" })).not.toBeInTheDocument();
  });
});
