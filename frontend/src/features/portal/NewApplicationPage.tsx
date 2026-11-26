import { zodResolver } from "@hookform/resolvers/zod";
import { useDeferredValue, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useNavigate } from "react-router";
import { z } from "zod";

import type { LoanPurpose, Product } from "@/api/types";
import {
  useCreateApplication,
  useProductSchedulePreview,
  useProducts,
} from "@/features/applications/api";
import { SchedulePreviewView } from "@/features/loans/ScheduleTables";
import { applyServerErrors, usePageTitle } from "@/shared/lib/forms";
import { formatMoney, formatPercent } from "@/shared/lib/format";
import { LOAN_PURPOSES, options } from "@/shared/lib/labels";
import { moneyInput, optionalText } from "@/shared/lib/validation";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  FormMessage,
  LoadingState,
  PageHeader,
  QueryView,
  SelectField,
  TextAreaField,
  TextField,
} from "@/shared/ui";

const schema = z.object({
  loan_product_id: z.string().min(1, "Choose a loan product"),
  requested_amount: moneyInput("Amount"),
  requested_term_months: z.string().regex(/^\d{1,3}$/, "Enter the term in whole months"),
  purpose: z.string().min(1, "Choose what the loan is for"),
  purpose_description: optionalText(500),
});
type Values = z.infer<typeof schema>;

/** Range checks against the chosen product's limits (the server checks again). */
function productProblems(
  product: Product | undefined,
  values: Values,
): Partial<Record<keyof Values, string>> {
  if (!product) return {};
  const problems: Partial<Record<keyof Values, string>> = {};
  const term = Number(values.requested_term_months);
  if (term && (term < product.min_term_months || term > product.max_term_months)) {
    problems.requested_term_months = `This product allows ${product.min_term_months}–${product.max_term_months} months`;
  }
  return problems;
}

export function NewApplicationPage() {
  usePageTitle("New application");
  const products = useProducts();
  const create = useCreateApplication();
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      loan_product_id: "",
      requested_amount: "",
      requested_term_months: "",
      purpose: "",
      purpose_description: "",
    },
  });
  const { errors, isSubmitting } = form.formState;
  const watched = useWatch({ control: form.control });
  const product = products.data?.items.find((item) => item.id === watched.loan_product_id);
  const amount = useDeferredValue((watched.requested_amount ?? "").trim());
  const term = useDeferredValue(Number(watched.requested_term_months) || undefined);
  const preview = useProductSchedulePreview(product?.id, amount, term);

  const onSubmit = form.handleSubmit(async (values) => {
    setError(null);
    const problems = productProblems(product, values);
    if (Object.keys(problems).length) {
      for (const [field, message] of Object.entries(problems)) {
        form.setError(field as keyof Values, { message });
      }
      return;
    }
    try {
      const application = await create.mutateAsync({
        loan_product_id: values.loan_product_id,
        requested_amount: values.requested_amount,
        requested_term_months: Number(values.requested_term_months),
        purpose: values.purpose as LoanPurpose,
        purpose_description: values.purpose_description || null,
      });
      navigate(`/portal/applications/${application.id}?created=1`);
    } catch (caught) {
      if (
        !applyServerErrors(caught, form.setError, Object.keys(schema.shape) as Array<keyof Values>)
      ) {
        setError(caught);
      }
    }
  });

  return (
    <>
      <PageHeader
        title="New loan application"
        description="Your application is saved as a draft. You can review it before you submit it."
      />
      <QueryView
        query={products}
        isEmpty={(page) => page.items.length === 0}
        empty={
          <EmptyState title="No loan products are available">Please check back later.</EmptyState>
        }
      >
        {(page) => (
          <div className="grid gap-6 xl:grid-cols-[24rem_1fr]">
            <Card title="Loan details">
              <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
                <SelectField
                  label="Loan product"
                  required
                  placeholder="Choose a product…"
                  options={page.items.map((item) => ({ value: item.id, label: item.name }))}
                  error={errors.loan_product_id?.message}
                  {...form.register("loan_product_id")}
                />
                {product && (
                  <p className="-mt-2 text-xs text-muted">
                    {formatMoney(product.min_amount, product.currency)}–
                    {formatMoney(product.max_amount, product.currency)} over{" "}
                    {product.min_term_months}–{product.max_term_months} months ·{" "}
                    {formatPercent(product.annual_interest_rate)} a year
                  </p>
                )}
                <TextField
                  label="Amount to borrow"
                  inputMode="decimal"
                  prefix="R"
                  required
                  error={errors.requested_amount?.message}
                  {...form.register("requested_amount")}
                />
                <TextField
                  label="Term (months)"
                  inputMode="numeric"
                  required
                  error={errors.requested_term_months?.message}
                  {...form.register("requested_term_months")}
                />
                <SelectField
                  label="Purpose"
                  required
                  placeholder="Choose…"
                  options={options(LOAN_PURPOSES)}
                  error={errors.purpose?.message}
                  {...form.register("purpose")}
                />
                <TextAreaField
                  label="Tell us more (optional)"
                  rows={3}
                  error={errors.purpose_description?.message}
                  {...form.register("purpose_description")}
                />
                <FormMessage error={error} />
                <Button type="submit" loading={isSubmitting}>
                  Save as draft
                </Button>
              </form>
            </Card>
            <Card
              title="What it would cost"
              description="Calculated by the repayment engine from the product's current terms."
            >
              {!product || !amount || !term ? (
                <EmptyState title="Choose a product, amount and term">
                  The monthly instalment and full repayment schedule appear here.
                </EmptyState>
              ) : preview.isPending ? (
                <LoadingState label="Calculating the schedule" />
              ) : preview.isError ? (
                <ErrorState error={preview.error} title="This loan can't be illustrated" />
              ) : (
                <div
                  aria-live="polite"
                  className={preview.isPlaceholderData ? "opacity-60" : undefined}
                >
                  <SchedulePreviewView preview={preview.data} />
                </div>
              )}
            </Card>
          </div>
        )}
      </QueryView>
    </>
  );
}
