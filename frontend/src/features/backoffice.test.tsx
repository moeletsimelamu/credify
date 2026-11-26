/** Work queue, model registry and admin screens. */
import { screen, waitFor, within } from "@testing-library/react";

import type { ModelVersion } from "@/api/types";
import { mockApi } from "@/test/api";
import { APPLICATION } from "@/test/fixtures";
import { expectAccessible, makeUser, renderWithProviders } from "@/test/render";

import { UsersPage } from "./admin/UsersPage";
import { WorkQueuePage } from "./office/WorkQueuePage";
import { ModelsPage } from "./reporting/ModelsPage";

const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 25 });

describe("WorkQueuePage", () => {
  it("lists open applications with product and officer names", async () => {
    const server = mockApi({
      "GET /api/v1/applications": page([APPLICATION]),
      "GET /api/v1/products": page([{ id: "product-1", name: "Personal unsecured loan" }]),
      "GET /api/v1/staff": [
        { id: "user-loan_officer", full_name: "Lindiwe Officer", role: "LOAN_OFFICER" },
      ],
    });
    const { container } = renderWithProviders(<WorkQueuePage />, { user: makeUser("ADMIN") });
    const table = await screen.findByRole("table", { name: "Applications" });
    const row = within(table).getByRole("link", { name: "CRD-2026-000042" }).closest("tr")!;
    expect(row).toHaveTextContent("Personal unsecured loan");
    expect(row).toHaveTextContent("Lindiwe Officer");
    expect(within(table).getByRole("link", { name: "CRD-2026-000042" })).toHaveAttribute(
      "href",
      "/office/applications/app-1",
    );
    const statuses = server
      .calls("GET", "/api/v1/applications")[0]!
      .url.searchParams.getAll("status");
    expect(statuses).toContain("UNDER_REVIEW");
    expect(statuses).not.toContain("DRAFT");
    await expectAccessible(container);
  });

  it("filters by view and says when a view is empty", async () => {
    const server = mockApi({
      "GET /api/v1/applications": page([]),
      "GET /api/v1/products": page([]),
      "GET /api/v1/staff": [],
    });
    const { user } = renderWithProviders(<WorkQueuePage />, { user: makeUser("LOAN_OFFICER") });
    await user.click(await screen.findByRole("button", { name: "Assigned to me" }));
    expect(await screen.findByText("Nothing in this view")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        server
          .calls("GET", "/api/v1/applications")
          .at(-1)!
          .url.searchParams.get("assigned_officer_id"),
      ).toBe("user-loan_officer"),
    );
    expect(screen.getByRole("button", { name: "Assigned to me" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});

const MODEL: ModelVersion = {
  id: "model-1",
  name: "credify-pd",
  version: "1.0.0",
  algorithm: "logistic_regression",
  status: "CANDIDATE",
  artefact_sha256: "48d0310a".padEnd(64, "0"),
  training_dataset_sha256: "ab".padEnd(64, "0"),
  dataset_generator_version: "synthgen-1.0.0",
  random_seed: 20261118,
  decision_threshold: "0.130000",
  band_cutoffs: { bands: ["A", "B", "C", "D", "E"], cuts: [0.04, 0.07, 0.13, 0.25], guidance: {} },
  metrics: {
    test: { roc_auc: 0.694923, pr_auc: 0.272503, brier: 0.096046 },
    test_at_threshold: { recall: 0.5409, flagged_rate: 0.2873 },
  },
  feature_schema: {
    features: [
      { name: "gross_monthly_income", description: "Gross monthly income", kind: "money" },
    ],
  },
  libraries: { "scikit-learn": "1.9.1" },
  git_commit: null,
  trained_at: "2026-11-25T12:05:00Z",
  activated_at: null,
  retired_at: null,
  created_at: "2026-11-26T09:00:00Z",
};

describe("ModelsPage", () => {
  it("warns when no model is active and lets an administrator activate a candidate", async () => {
    const server = mockApi({
      "GET /api/v1/models": [MODEL],
      "POST /api/v1/models/model-1/activate": { ...MODEL, status: "ACTIVE" },
    });
    const { user } = renderWithProviders(<ModelsPage />, { user: makeUser("ADMIN") });
    expect(await screen.findByText("No active model")).toBeInTheDocument();
    expect(screen.getByText("0.695")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Activate" }));
    const dialog = await screen.findByRole("dialog", { name: /Activate credify-pd 1.0.0/ });
    expect(dialog).toHaveTextContent(/verified against its registered SHA-256/);
    await user.click(within(dialog).getByRole("button", { name: "Activate model" }));
    await waitFor(() =>
      expect(server.calls("POST", "/api/v1/models/model-1/activate")).toHaveLength(1),
    );
  });

  it("never offers activation without model:manage", async () => {
    mockApi({ "GET /api/v1/models": [MODEL] });
    renderWithProviders(<ModelsPage />, { user: makeUser("CREDIT_ANALYST") });
    await screen.findByText("No active model");
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  });
});

describe("UsersPage", () => {
  it("creates staff users with a role and an initial password", async () => {
    const server = mockApi({
      "GET /api/v1/users": page([]),
      "POST /api/v1/users": { status: 201, body: {} },
    });
    const { user } = renderWithProviders(<UsersPage />, { user: makeUser("ADMIN") });
    await user.click(await screen.findByRole("button", { name: "Add staff user" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a staff user" });
    await user.type(within(dialog).getByLabelText(/Full name/), "Sizwe Analyst");
    await user.type(within(dialog).getByLabelText(/Email/), "sizwe@example.test");
    await user.selectOptions(within(dialog).getByLabelText(/Role/), "CREDIT_ANALYST");
    await user.type(within(dialog).getByLabelText(/Initial password/), "a long passphrase");
    await expectAccessible(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create user" }));
    await waitFor(() => expect(server.calls("POST", "/api/v1/users")).toHaveLength(1));
    expect(server.calls("POST", "/api/v1/users")[0]!.body).toEqual({
      full_name: "Sizwe Analyst",
      email: "sizwe@example.test",
      role: "CREDIT_ANALYST",
      password: "a long passphrase",
    });
  });
});
