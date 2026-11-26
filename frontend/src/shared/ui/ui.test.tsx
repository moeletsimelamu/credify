import { useQuery } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";

import { api } from "@/api/client";
import { mockApi, problem } from "@/test/api";
import { expectAccessible, renderWithProviders } from "@/test/render";

import { DataTable, MoneyText, QueryView, StatusBadge, TextField } from "./index";

describe("TextField", () => {
  it("labels the input and links its hint and error", async () => {
    const { container } = renderWithProviders(
      <form>
        <TextField label="Gross amount" hint="Before deductions" required />
        <TextField label="Net amount" error="Net income can't be more than gross income" />
      </form>,
    );
    const gross = await screen.findByLabelText(/Gross amount/);
    expect(gross).toHaveAccessibleDescription("Before deductions");
    expect(gross).toHaveAttribute("aria-required", "true");
    const net = screen.getByLabelText("Net amount");
    expect(net).toBeInvalid();
    expect(net).toHaveAccessibleDescription("Net income can't be more than gross income");
    expect(screen.getByRole("alert")).toHaveTextContent("can't be more than gross");
    await expectAccessible(container);
  });
});

describe("StatusBadge and MoneyText", () => {
  it("always states the status in words, not only colour", async () => {
    renderWithProviders(
      <>
        <StatusBadge status="IN_ARREARS" />
        <MoneyText amount="1500.5" currency="ZAR" />
      </>,
    );
    expect(await screen.findByText("In arrears")).toBeInTheDocument();
    expect(screen.getByText(/1.500,50/)).toHaveClass("figure");
  });
});

describe("DataTable", () => {
  it("renders an accessible table with a caption and column headers", async () => {
    const { container } = renderWithProviders(
      <DataTable
        caption="Loan accounts"
        rows={[{ id: "1", amount: "10.00" }]}
        rowKey={(row) => row.id}
        columns={[
          { key: "id", header: "Account", cell: (row) => row.id },
          { key: "amount", header: "Amount", numeric: true, cell: (row) => row.amount },
        ]}
      />,
    );
    const table = await screen.findByRole("table", { name: "Loan accounts" });
    expect(table).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Amount" })).toHaveClass("text-right");
    await expectAccessible(container);
  });
});

function Things() {
  const query = useQuery({ queryKey: ["things"], queryFn: () => api<string[]>("/things") });
  return (
    <QueryView query={query} isEmpty={(rows) => rows.length === 0} empty={<p>Nothing yet</p>}>
      {(rows) => <p>{rows.join(", ")}</p>}
    </QueryView>
  );
}

describe("QueryView", () => {
  it("shows a labelled loading state, then the data", async () => {
    mockApi({ "GET /api/v1/things": ["a", "b"] });
    renderWithProviders(<Things />);
    expect(await screen.findByRole("status")).toHaveTextContent("Loading");
    expect(await screen.findByText("a, b")).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    mockApi({ "GET /api/v1/things": [] });
    renderWithProviders(<Things />);
    expect(await screen.findByText("Nothing yet")).toBeInTheDocument();
  });

  it("shows the error with a working retry", async () => {
    let fail = true;
    mockApi({
      "GET /api/v1/things": () => (fail ? problem(500, "Database unavailable") : { body: ["ok"] }),
    });
    const { user } = renderWithProviders(<Things />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
  });
});
