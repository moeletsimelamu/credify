import { AFFORDABILITY, EXPLANATION } from "@/test/fixtures";

import { waterfallSteps } from "./affordability/waterfall";
import { workflowSteps } from "./applications/workflow";
import { filtersFor } from "./office/queueFilters";
import { bandRanges } from "./reporting/bands";
import { rankedContributions } from "./risk/contributions";

describe("workflowSteps", () => {
  it("marks earlier steps done and the current one current", () => {
    const steps = workflowSteps("UNDER_REVIEW");
    expect(steps.map((s) => s.state)).toEqual(["done", "done", "current", "upcoming", "upcoming"]);
  });

  it("adds the loan step after approval", () => {
    const steps = workflowSteps("APPROVED", true);
    expect(steps.at(-2)).toMatchObject({ label: "Approved", state: "done" });
    expect(steps.at(-1)).toMatchObject({ label: "Loan opened", state: "done" });
  });

  it("shows a decline and a withdrawal as stopped", () => {
    expect(workflowSteps("DECLINED").at(-1)).toMatchObject({ label: "Declined", state: "stopped" });
    expect(workflowSteps("WITHDRAWN")[0]).toMatchObject({ label: "Withdrawn", state: "stopped" });
  });
});

describe("waterfallSteps", () => {
  it("keeps the server's exact amounts as labels", () => {
    const steps = waterfallSteps(AFFORDABILITY);
    expect(steps.map((s) => s.amount)).toEqual([
      "24500.10",
      "9000.00",
      "1500.05",
      "1290.79",
      "12709.26",
    ]);
  });

  it("stacks each deduction down from the running total", () => {
    const [net, living, debt, instalment, residual] = waterfallSteps(AFFORDABILITY);
    expect(net!.base + net!.value).toBeCloseTo(24500.1);
    expect(living!.base + living!.value).toBeCloseTo(24500.1);
    expect(debt!.base + debt!.value).toBeCloseTo(living!.base);
    expect(instalment!.base + instalment!.value).toBeCloseTo(debt!.base);
    expect(residual!.value).toBeCloseTo(12709.26);
  });
});

describe("rankedContributions", () => {
  it("orders factors by the size of their effect", () => {
    expect(rankedContributions(EXPLANATION).map((c) => c.feature)).toEqual([
      "loan_to_income",
      "term_months",
      "loan_purpose",
    ]);
  });
});

describe("bandRanges", () => {
  it("turns cut-offs into readable ranges", () => {
    const ranges = bandRanges({
      bands: ["A", "B", "C"],
      cuts: [0.04, 0.13],
      guidance: { C: "Review" },
    });
    expect(ranges.map((r) => r.range.replace(/[\u00a0\u202f]/g, " "))).toEqual([
      "below 4,0%",
      "4,0% – 13,0%",
      "13,0% and above",
    ]);
    expect(ranges[2]!.guidance).toBe("Review");
  });
});

describe("filtersFor", () => {
  it("scopes 'Assigned to me' to open applications for the current officer", () => {
    expect(filtersFor("mine", "u1", "", "", 1)).toMatchObject({
      assigned_officer_id: "u1",
      status: expect.arrayContaining(["UNDER_REVIEW"]),
    });
  });

  it("lets an explicit status filter override the view's statuses", () => {
    expect(filtersFor("open", "u1", "APPROVED", "p1", 2)).toMatchObject({
      status: ["APPROVED"],
      loan_product_id: "p1",
      page: 2,
    });
  });

  it("never lists other people's drafts", () => {
    expect(filtersFor("all", "u1", "", "", 1).status).not.toContain("DRAFT");
  });
});
