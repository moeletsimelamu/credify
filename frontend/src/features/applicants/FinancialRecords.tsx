import { zodResolver } from "@hookform/resolvers/zod";
import { useState, type ReactNode } from "react";
import { useForm, type FieldValues, type Path, type UseFormReturn } from "react-hook-form";
import { z } from "zod";

import { errorMessage } from "@/api/client";
import type {
  Commitment,
  CommitmentCreate,
  Employment,
  EmploymentCreate,
  Expense,
  ExpenseCreate,
  FinancialProfile,
  Income,
  IncomeCreate,
} from "@/api/types";
import { applyServerErrors } from "@/shared/lib/forms";
import { formatDate, humanize } from "@/shared/lib/format";
import {
  COMMITMENT_TYPES,
  EMPLOYMENT_TYPES,
  EXPENSE_CATEGORIES,
  INCOME_FREQUENCIES,
  INCOME_SOURCES,
  options,
} from "@/shared/lib/labels";
import { moneyInput, optionalMoneyInput, optionalText } from "@/shared/lib/validation";
import {
  Button,
  Card,
  CheckboxField,
  ConfirmDialog,
  DataTable,
  Dialog,
  EmptyState,
  FormMessage,
  MoneyText,
  Notice,
  SelectField,
  StatusBadge,
  TextField,
  type Column,
} from "@/shared/ui";

import { useFinancialRecord, type RecordKind, type RecordTypes } from "./api";

// --- Forms -------------------------------------------------------------------------------

interface FormProps<R, W> {
  record?: R;
  onSave: (values: W) => Promise<unknown>;
  onCancel: () => void;
}

function FormShell<V extends FieldValues>({
  form,
  onSubmit,
  onCancel,
  children,
  fields,
}: {
  form: UseFormReturn<V>;
  onSubmit: (values: V) => Promise<unknown>;
  onCancel: () => void;
  children: ReactNode;
  fields: ReadonlyArray<Path<V>>;
}) {
  const [error, setError] = useState<unknown>(null);
  const submit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      await onSubmit(values);
    } catch (caught) {
      if (!applyServerErrors(caught, form.setError, fields)) setError(caught);
    }
  });
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
      <FormMessage error={error} />
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" loading={form.formState.isSubmitting}>
          Save
        </Button>
      </div>
    </form>
  );
}

const employmentSchema = z
  .object({
    employment_type: z.enum(EMPLOYMENT_TYPES as [string, ...string[]], "Choose an employment type"),
    employer_name: optionalText(200),
    job_title: optionalText(120),
    start_date: z.string(),
    end_date: z.string(),
    is_current: z.boolean(),
  })
  .refine((v) => !v.start_date || !v.end_date || v.end_date >= v.start_date, {
    path: ["end_date"],
    message: "The end date can't be before the start date",
  })
  .refine((v) => !(v.is_current && v.end_date), {
    path: ["end_date"],
    message: "Current employment has no end date",
  });

export function EmploymentForm({
  record,
  onSave,
  onCancel,
}: FormProps<Employment, EmploymentCreate>) {
  const form = useForm<z.infer<typeof employmentSchema>>({
    resolver: zodResolver(employmentSchema),
    defaultValues: {
      employment_type: record?.employment_type ?? "",
      employer_name: record?.employer_name ?? "",
      job_title: record?.job_title ?? "",
      start_date: record?.start_date ?? "",
      end_date: record?.end_date ?? "",
      is_current: record?.is_current ?? true,
    },
  });
  const { errors } = form.formState;
  return (
    <FormShell
      form={form}
      onCancel={onCancel}
      fields={[
        "employment_type",
        "employer_name",
        "job_title",
        "start_date",
        "end_date",
        "is_current",
      ]}
      onSubmit={(v) =>
        onSave({
          employment_type: v.employment_type as EmploymentCreate["employment_type"],
          employer_name: v.employer_name || null,
          job_title: v.job_title || null,
          start_date: v.start_date || null,
          end_date: v.end_date || null,
          is_current: v.is_current,
        })
      }
    >
      <SelectField
        label="Employment type"
        required
        placeholder="Choose…"
        options={options(EMPLOYMENT_TYPES)}
        error={errors.employment_type?.message}
        {...form.register("employment_type")}
      />
      <TextField
        label="Employer"
        error={errors.employer_name?.message}
        {...form.register("employer_name")}
      />
      <TextField
        label="Job title"
        error={errors.job_title?.message}
        {...form.register("job_title")}
      />
      <TextField
        label="Start date"
        type="date"
        error={errors.start_date?.message}
        {...form.register("start_date")}
      />
      <TextField
        label="End date"
        type="date"
        error={errors.end_date?.message}
        {...form.register("end_date")}
      />
      <CheckboxField
        label="This is my current job"
        containerClassName="sm:col-span-2"
        {...form.register("is_current")}
      />
    </FormShell>
  );
}

const incomeSchema = z
  .object({
    source_type: z.enum(INCOME_SOURCES as [string, ...string[]], "Choose an income source"),
    frequency: z.enum(INCOME_FREQUENCIES as [string, ...string[]], "Choose how often it is paid"),
    gross_amount: moneyInput("Gross amount"),
    net_amount: moneyInput("Net amount"),
  })
  .refine((v) => Number(v.net_amount) <= Number(v.gross_amount), {
    path: ["net_amount"],
    message: "Net income can't be more than gross income",
  });

export function IncomeForm({ record, onSave, onCancel }: FormProps<Income, IncomeCreate>) {
  const form = useForm<z.infer<typeof incomeSchema>>({
    resolver: zodResolver(incomeSchema),
    defaultValues: {
      source_type: record?.source_type ?? "",
      frequency: record?.frequency ?? "MONTHLY",
      gross_amount: record?.gross_amount ?? "",
      net_amount: record?.net_amount ?? "",
    },
  });
  const { errors } = form.formState;
  return (
    <FormShell
      form={form}
      onCancel={onCancel}
      fields={["source_type", "frequency", "gross_amount", "net_amount"]}
      onSubmit={(v) =>
        onSave({
          source_type: v.source_type as IncomeCreate["source_type"],
          frequency: v.frequency as IncomeCreate["frequency"],
          gross_amount: v.gross_amount,
          net_amount: v.net_amount,
        })
      }
    >
      <SelectField
        label="Source"
        required
        placeholder="Choose…"
        options={options(INCOME_SOURCES)}
        error={errors.source_type?.message}
        {...form.register("source_type")}
      />
      <SelectField
        label="Paid"
        required
        options={options(INCOME_FREQUENCIES)}
        error={errors.frequency?.message}
        {...form.register("frequency")}
      />
      <TextField
        label="Gross amount (before deductions)"
        inputMode="decimal"
        prefix="R"
        required
        error={errors.gross_amount?.message}
        {...form.register("gross_amount")}
      />
      <TextField
        label="Net amount (take-home)"
        inputMode="decimal"
        prefix="R"
        required
        error={errors.net_amount?.message}
        {...form.register("net_amount")}
      />
    </FormShell>
  );
}

const expenseSchema = z.object({
  category: z.enum(EXPENSE_CATEGORIES as [string, ...string[]], "Choose a category"),
  monthly_amount: moneyInput("Monthly amount"),
});

export function ExpenseForm({ record, onSave, onCancel }: FormProps<Expense, ExpenseCreate>) {
  const form = useForm<z.infer<typeof expenseSchema>>({
    resolver: zodResolver(expenseSchema),
    defaultValues: {
      category: record?.category ?? "",
      monthly_amount: record?.monthly_amount ?? "",
    },
  });
  const { errors } = form.formState;
  return (
    <FormShell
      form={form}
      onCancel={onCancel}
      fields={["category", "monthly_amount"]}
      onSubmit={(v) =>
        onSave({
          category: v.category as ExpenseCreate["category"],
          monthly_amount: v.monthly_amount,
        })
      }
    >
      <SelectField
        label="Category"
        required
        placeholder="Choose…"
        options={options(EXPENSE_CATEGORIES)}
        error={errors.category?.message}
        {...form.register("category")}
      />
      <TextField
        label="Monthly amount"
        inputMode="decimal"
        prefix="R"
        required
        error={errors.monthly_amount?.message}
        {...form.register("monthly_amount")}
      />
    </FormShell>
  );
}

const commitmentSchema = z.object({
  commitment_type: z.enum(COMMITMENT_TYPES as [string, ...string[]], "Choose a type"),
  lender_name: z.string().trim().min(1, "Lender is required").max(200, "Too long"),
  monthly_instalment: moneyInput("Monthly instalment"),
  outstanding_balance: moneyInput("Outstanding balance"),
  credit_limit: optionalMoneyInput("Credit limit"),
});

export function CommitmentForm({
  record,
  onSave,
  onCancel,
}: FormProps<Commitment, CommitmentCreate>) {
  const form = useForm<z.infer<typeof commitmentSchema>>({
    resolver: zodResolver(commitmentSchema),
    defaultValues: {
      commitment_type: record?.commitment_type ?? "",
      lender_name: record?.lender_name ?? "",
      monthly_instalment: record?.monthly_instalment ?? "",
      outstanding_balance: record?.outstanding_balance ?? "",
      credit_limit: record?.credit_limit ?? "",
    },
  });
  const { errors } = form.formState;
  return (
    <FormShell
      form={form}
      onCancel={onCancel}
      fields={[
        "commitment_type",
        "lender_name",
        "monthly_instalment",
        "outstanding_balance",
        "credit_limit",
      ]}
      onSubmit={(v) =>
        onSave({
          commitment_type: v.commitment_type as CommitmentCreate["commitment_type"],
          lender_name: v.lender_name,
          monthly_instalment: v.monthly_instalment,
          outstanding_balance: v.outstanding_balance,
          credit_limit: v.credit_limit || null,
        })
      }
    >
      <SelectField
        label="Type"
        required
        placeholder="Choose…"
        options={options(COMMITMENT_TYPES)}
        error={errors.commitment_type?.message}
        {...form.register("commitment_type")}
      />
      <TextField
        label="Lender"
        required
        error={errors.lender_name?.message}
        {...form.register("lender_name")}
      />
      <TextField
        label="Monthly instalment"
        inputMode="decimal"
        prefix="R"
        required
        error={errors.monthly_instalment?.message}
        {...form.register("monthly_instalment")}
      />
      <TextField
        label="Outstanding balance"
        inputMode="decimal"
        prefix="R"
        required
        error={errors.outstanding_balance?.message}
        {...form.register("outstanding_balance")}
      />
      <TextField
        label="Credit limit"
        inputMode="decimal"
        prefix="R"
        hint="For credit cards and store accounts."
        error={errors.credit_limit?.message}
        {...form.register("credit_limit")}
      />
    </FormShell>
  );
}

// --- Sections ----------------------------------------------------------------------------

interface SectionProps<K extends RecordKind> {
  applicantId: string;
  kind: K;
  title: string;
  description: string;
  noun: string;
  rows: Array<RecordTypes[K]["read"]>;
  columns: Array<Column<RecordTypes[K]["read"]>>;
  editable: boolean;
  renderForm: (props: FormProps<RecordTypes[K]["read"], RecordTypes[K]["write"]>) => ReactNode;
}

function RecordSection<K extends RecordKind>({
  applicantId,
  kind,
  title,
  description,
  noun,
  rows,
  columns,
  editable,
  renderForm,
}: SectionProps<K>) {
  const { save, remove } = useFinancialRecord(applicantId, kind);
  const [editing, setEditing] = useState<RecordTypes[K]["read"] | "new" | null>(null);
  const [removing, setRemoving] = useState<RecordTypes[K]["read"] | null>(null);
  const active = rows.filter((row) => row.is_active);

  const allColumns: Array<Column<RecordTypes[K]["read"]>> = editable
    ? [
        ...columns,
        {
          key: "actions",
          header: <span className="sr-only">Actions</span>,
          align: "right",
          cell: (row) => (
            <div className="flex justify-end gap-1">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditing(row)}
                aria-label={`Edit ${noun}`}
              >
                Edit
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setRemoving(row)}
                aria-label={`Remove ${noun}`}
              >
                Remove
              </Button>
            </div>
          ),
        },
      ]
    : columns;

  return (
    <Card
      title={title}
      description={description}
      actions={
        editable && (
          <Button size="sm" variant="secondary" onClick={() => setEditing("new")}>
            Add {noun}
          </Button>
        )
      }
    >
      {active.length === 0 ? (
        <EmptyState title={`No ${noun} recorded`}>
          {editable
            ? `Add each ${noun} so the affordability check is complete.`
            : "The applicant hasn't declared any."}
        </EmptyState>
      ) : (
        <DataTable caption={title} columns={allColumns} rows={active} rowKey={(row) => row.id} />
      )}

      <Dialog
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        title={editing === "new" ? `Add ${noun}` : `Edit ${noun}`}
      >
        {editing !== null &&
          renderForm({
            record: editing === "new" ? undefined : editing,
            onCancel: () => setEditing(null),
            onSave: async (values) => {
              await save.mutateAsync({ id: editing === "new" ? undefined : editing.id, values });
              setEditing(null);
            },
          })}
      </Dialog>

      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRemoving(null);
            remove.reset();
          }
        }}
        title={`Remove this ${noun}?`}
        confirmLabel="Remove"
        danger
        pending={remove.isPending}
        error={
          remove.error ? <Notice tone="critical">{errorMessage(remove.error)}</Notice> : undefined
        }
        onConfirm={() => {
          if (!removing) return;
          remove.mutate(removing.id, { onSuccess: () => setRemoving(null) });
        }}
      >
        It will no longer count towards new affordability assessments. Earlier assessments keep the
        figures they used.
      </ConfirmDialog>
    </Card>
  );
}

/** The four parts of a financial profile. Editable for the applicant, read-only for staff. */
export function FinancialRecords({
  applicantId,
  profile,
  editable,
}: {
  applicantId: string;
  profile: FinancialProfile;
  editable: boolean;
}) {
  return (
    <div className="flex flex-col gap-6">
      <RecordSection
        applicantId={applicantId}
        kind="employment"
        title="Employment"
        description="Current and recent jobs."
        noun="employment"
        rows={profile.employment}
        editable={editable}
        renderForm={(props) => <EmploymentForm {...props} />}
        columns={[
          { key: "type", header: "Type", cell: (r) => humanize(r.employment_type) },
          { key: "employer", header: "Employer", cell: (r) => r.employer_name ?? "—" },
          { key: "title", header: "Job title", cell: (r) => r.job_title ?? "—" },
          { key: "start", header: "Started", cell: (r) => formatDate(r.start_date) },
          {
            key: "current",
            header: "Current",
            cell: (r) => (r.is_current ? "Yes" : formatDate(r.end_date)),
          },
        ]}
      />
      <RecordSection
        applicantId={applicantId}
        kind="income"
        title="Income"
        description="As declared. Staff verify income separately."
        noun="income"
        rows={profile.income}
        editable={editable}
        renderForm={(props) => <IncomeForm {...props} />}
        columns={[
          { key: "source", header: "Source", cell: (r) => humanize(r.source_type) },
          { key: "frequency", header: "Paid", cell: (r) => humanize(r.frequency) },
          {
            key: "gross",
            header: "Gross",
            numeric: true,
            cell: (r) => <MoneyText amount={r.gross_amount} currency={r.currency} />,
          },
          {
            key: "net",
            header: "Net",
            numeric: true,
            cell: (r) => <MoneyText amount={r.net_amount} currency={r.currency} />,
          },
          {
            key: "status",
            header: "Verification",
            cell: (r) => <StatusBadge status={r.verification_status} />,
          },
        ]}
      />
      <RecordSection
        applicantId={applicantId}
        kind="expenses"
        title="Living expenses"
        description="Monthly costs. The assessment applies a minimum norm if these are lower."
        noun="expense"
        rows={profile.expenses}
        editable={editable}
        renderForm={(props) => <ExpenseForm {...props} />}
        columns={[
          { key: "category", header: "Category", cell: (r) => humanize(r.category) },
          {
            key: "amount",
            header: "Monthly",
            numeric: true,
            cell: (r) => <MoneyText amount={r.monthly_amount} currency={r.currency} />,
          },
        ]}
      />
      <RecordSection
        applicantId={applicantId}
        kind="commitments"
        title="Existing credit commitments"
        description="Other loans and accounts you repay every month."
        noun="commitment"
        rows={profile.commitments}
        editable={editable}
        renderForm={(props) => <CommitmentForm {...props} />}
        columns={[
          { key: "type", header: "Type", cell: (r) => humanize(r.commitment_type) },
          { key: "lender", header: "Lender", cell: (r) => r.lender_name },
          {
            key: "instalment",
            header: "Monthly",
            numeric: true,
            cell: (r) => <MoneyText amount={r.monthly_instalment} currency={r.currency} />,
          },
          {
            key: "balance",
            header: "Balance",
            numeric: true,
            cell: (r) => <MoneyText amount={r.outstanding_balance} currency={r.currency} />,
          },
          {
            key: "limit",
            header: "Limit",
            numeric: true,
            cell: (r) => <MoneyText amount={r.credit_limit} currency={r.currency} />,
          },
        ]}
      />
    </div>
  );
}
