import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import type { Product, ProductCreate } from "@/api/types";
import { useProducts } from "@/features/applications/api";
import { applyServerErrors, usePageTitle } from "@/shared/lib/forms";
import { formatPercent } from "@/shared/lib/format";
import { moneyInput } from "@/shared/lib/validation";
import {
  Badge,
  Button,
  Card,
  CheckboxField,
  DataTable,
  Dialog,
  EmptyState,
  FormMessage,
  MoneyText,
  PageHeader,
  QueryView,
  TextAreaField,
  TextField,
} from "@/shared/ui";

import { useSaveProduct } from "./api";

/** Rates and ratios are fractions, exactly as the API stores them (0.215 = 21.5 %). */
const fraction = (label: string, inclusiveOne = false) =>
  z
    .string()
    .trim()
    .regex(
      inclusiveOne ? /^(0(\.\d{1,4})?|1(\.0{1,4})?)$/ : /^0(\.\d{1,6})?$/,
      `${label} must be a fraction such as 0.215`,
    );

const term = (label: string) => z.string().regex(/^\d{1,3}$/, `${label} must be whole months`);

const schema = z.object({
  code: z
    .string()
    .trim()
    .regex(
      /^[A-Z][A-Z0-9_]{2,49}$/,
      "Use 3–50 capitals, digits or underscores, starting with a letter",
    ),
  name: z.string().trim().min(3, "Use at least 3 characters").max(120, "Too long"),
  description: z.string().trim().max(2000, "Too long"),
  min_amount: moneyInput("Minimum amount"),
  max_amount: moneyInput("Maximum amount"),
  min_term_months: term("Minimum term"),
  max_term_months: term("Maximum term"),
  annual_interest_rate: fraction("Interest rate"),
  initiation_fee: moneyInput("Initiation fee"),
  monthly_service_fee: moneyInput("Service fee"),
  late_fee: moneyInput("Late fee"),
  grace_period_days: z.string().regex(/^\d{1,2}$/, "Use 0–90 days"),
  early_settlement_fee_rate: fraction("Early settlement fee rate"),
  max_dti: fraction("Maximum DTI", true),
  warn_dti: fraction("Warning DTI", true),
  min_residual_amount: moneyInput("Minimum residual"),
  min_residual_rate: fraction("Minimum residual rate", true),
  is_active: z.boolean(),
});
type Values = z.infer<typeof schema>;
const FIELDS = Object.keys(schema.shape) as Array<keyof Values>;

function defaults(product?: Product): Values {
  return {
    code: product?.code ?? "",
    name: product?.name ?? "",
    description: product?.description ?? "",
    min_amount: product?.min_amount ?? "",
    max_amount: product?.max_amount ?? "",
    min_term_months: product ? String(product.min_term_months) : "",
    max_term_months: product ? String(product.max_term_months) : "",
    annual_interest_rate: product?.annual_interest_rate ?? "",
    initiation_fee: product?.initiation_fee ?? "0.00",
    monthly_service_fee: product?.monthly_service_fee ?? "0.00",
    late_fee: product?.late_fee ?? "0.00",
    grace_period_days: product ? String(product.grace_period_days) : "5",
    early_settlement_fee_rate: product?.early_settlement_fee_rate ?? "0",
    max_dti: product?.max_dti ?? "",
    warn_dti: product?.warn_dti ?? "",
    min_residual_amount: product?.min_residual_amount ?? "",
    min_residual_rate: product?.min_residual_rate ?? "",
    is_active: product?.is_active ?? true,
  };
}

function ProductForm({ product, onDone }: { product?: Product; onDone: () => void }) {
  const save = useSaveProduct();
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults(product) });
  const { errors, isSubmitting } = form.formState;
  const submit = form.handleSubmit(async ({ code, is_active, ...values }) => {
    const terms = {
      ...values,
      description: values.description || null,
      min_term_months: Number(values.min_term_months),
      max_term_months: Number(values.max_term_months),
      grace_period_days: Number(values.grace_period_days),
    };
    try {
      if (product) await save.mutateAsync({ id: product.id, values: { ...terms, is_active } });
      else await save.mutateAsync({ values: { ...terms, code } satisfies ProductCreate });
      onDone();
    } catch (error) {
      applyServerErrors(error, form.setError, FIELDS);
    }
  });
  const field = (name: keyof Values, label: string, extra: Record<string, unknown> = {}) => (
    <TextField
      label={label}
      required
      error={errors[name]?.message}
      {...extra}
      {...form.register(name)}
    />
  );
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        {field("code", "Code", {
          disabled: Boolean(product),
          hint: product ? "Codes can't change." : "e.g. PERSONAL_36",
        })}
        {field("name", "Name")}
        <TextAreaField
          label="Description"
          rows={2}
          containerClassName="sm:col-span-2"
          error={errors.description?.message}
          {...form.register("description")}
        />
        {field("min_amount", "Minimum amount", { inputMode: "decimal", prefix: "R" })}
        {field("max_amount", "Maximum amount", { inputMode: "decimal", prefix: "R" })}
        {field("min_term_months", "Minimum term (months)", { inputMode: "numeric" })}
        {field("max_term_months", "Maximum term (months)", { inputMode: "numeric" })}
        {field("annual_interest_rate", "Annual interest rate", {
          inputMode: "decimal",
          hint: "Fraction: 0.215 = 21.5%",
        })}
        {field("early_settlement_fee_rate", "Early settlement fee rate", {
          inputMode: "decimal",
          hint: "Fraction of principal settled early",
        })}
        {field("initiation_fee", "Initiation fee", { inputMode: "decimal", prefix: "R" })}
        {field("monthly_service_fee", "Monthly service fee", { inputMode: "decimal", prefix: "R" })}
        {field("late_fee", "Late fee", { inputMode: "decimal", prefix: "R" })}
        {field("grace_period_days", "Grace period (days)", { inputMode: "numeric" })}
        {field("warn_dti", "Warning debt-to-income", { inputMode: "decimal", hint: "e.g. 0.35" })}
        {field("max_dti", "Maximum debt-to-income", { inputMode: "decimal", hint: "e.g. 0.45" })}
        {field("min_residual_amount", "Minimum residual income", {
          inputMode: "decimal",
          prefix: "R",
        })}
        {field("min_residual_rate", "Minimum residual rate", {
          inputMode: "decimal",
          hint: "Share of net income, e.g. 0.10",
        })}
      </div>
      {product && (
        <CheckboxField label="Active (offered to applicants)" {...form.register("is_active")} />
      )}
      <FormMessage error={save.error && !Object.keys(errors).length ? save.error : undefined} />
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" loading={isSubmitting}>
          {product ? "Save product" : "Create product"}
        </Button>
      </div>
    </form>
  );
}

export function ProductsPage() {
  usePageTitle("Products");
  const products = useProducts(true);
  const [editing, setEditing] = useState<Product | "new" | null>(null);
  return (
    <>
      <PageHeader
        title="Loan products"
        description="Terms are frozen onto each application when it is submitted, so changes apply to new submissions only."
        actions={<Button onClick={() => setEditing("new")}>Add product</Button>}
      />
      <Card>
        <QueryView
          query={products}
          isEmpty={(data) => data.items.length === 0}
          empty={<EmptyState title="No products yet" />}
        >
          {(data) => (
            <DataTable<Product>
              caption="Loan products"
              rows={data.items}
              rowKey={(row) => row.id}
              columns={[
                {
                  key: "name",
                  header: "Product",
                  cell: (r) => (
                    <>
                      <span className="font-medium">{r.name}</span>
                      <br />
                      <span className="figure text-xs text-muted">{r.code}</span>
                    </>
                  ),
                },
                {
                  key: "range",
                  header: "Amount",
                  numeric: true,
                  cell: (r) => (
                    <>
                      <MoneyText amount={r.min_amount} currency={r.currency} /> –{" "}
                      <MoneyText amount={r.max_amount} currency={r.currency} />
                    </>
                  ),
                },
                {
                  key: "term",
                  header: "Term",
                  numeric: true,
                  cell: (r) => `${r.min_term_months}–${r.max_term_months} mo`,
                },
                {
                  key: "rate",
                  header: "Rate",
                  numeric: true,
                  cell: (r) => formatPercent(r.annual_interest_rate),
                },
                {
                  key: "dti",
                  header: "Max DTI",
                  numeric: true,
                  cell: (r) => formatPercent(r.max_dti),
                },
                {
                  key: "active",
                  header: "Status",
                  cell: (r) =>
                    r.is_active ? <Badge tone="positive">Active</Badge> : <Badge>Inactive</Badge>,
                },
                {
                  key: "edit",
                  header: <span className="sr-only">Actions</span>,
                  align: "right",
                  cell: (r) => (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditing(r)}
                      aria-label={`Edit ${r.name}`}
                    >
                      Edit
                    </Button>
                  ),
                },
              ]}
            />
          )}
        </QueryView>
      </Card>
      <Dialog
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        title={editing === "new" ? "Add a loan product" : "Edit loan product"}
        wide
      >
        {editing !== null && (
          <ProductForm
            product={editing === "new" ? undefined : editing}
            onDone={() => setEditing(null)}
          />
        )}
      </Dialog>
    </>
  );
}
