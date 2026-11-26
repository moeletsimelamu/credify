import { zodResolver } from "@hookform/resolvers/zod";
import { useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { newIdempotencyKey } from "@/api/client";
import type { LoanAccount, Payment } from "@/api/types";
import { businessToday, formatDate } from "@/shared/lib/format";
import { moneyInput } from "@/shared/lib/validation";
import {
  Button,
  Card,
  ConfirmDialog,
  FormMessage,
  KeyValueList,
  MoneyText,
  Notice,
  QueryView,
  TextField,
} from "@/shared/ui";

import { useRecordRepayment, useSettle, useSettlementQuote } from "./api";

/**
 * One idempotency key per intended payment. It survives a retry of the same amount and date
 * (so a lost response can't double-charge) and is replaced once the payment succeeds or the
 * person changes what they're paying.
 */
function useIntentKey(): [() => string, (signature: string) => void, () => void] {
  const key = useRef<{ signature: string; value: string } | null>(null);
  const current = () => key.current?.value ?? "";
  const ensure = (signature: string) => {
    if (!key.current || key.current.signature !== signature) {
      key.current = { signature, value: newIdempotencyKey() };
    }
  };
  const reset = () => {
    key.current = null;
  };
  return [current, ensure, reset];
}

const repaymentSchema = z.object({
  amount: moneyInput("Amount"),
  value_date: z.string(),
});

export function RepaymentPanel({ account, staff }: { account: LoanAccount; staff: boolean }) {
  const record = useRecordRepayment(account.id);
  const [currentKey, ensureKey, resetKey] = useIntentKey();
  const [result, setResult] = useState<Payment | null>(null);
  const form = useForm<z.infer<typeof repaymentSchema>>({
    resolver: zodResolver(repaymentSchema),
    defaultValues: {
      amount:
        account.arrears_amount !== "0.00" ? account.arrears_amount : account.instalment_amount,
      value_date: "",
    },
  });
  const { errors, isSubmitting } = form.formState;
  const c = account.currency;

  const submit = form.handleSubmit(async (values) => {
    setResult(null);
    ensureKey(`${values.amount}|${values.value_date}`);
    try {
      const payment = await record.mutateAsync({
        amount: values.amount,
        valueDate: staff ? values.value_date : undefined,
        idempotencyKey: currentKey(),
      });
      resetKey();
      setResult(payment);
    } catch {
      // Shown below; the same key is reused if they retry the same payment.
    }
  });

  return (
    <Card
      title={staff ? "Record a repayment" : "Make a payment"}
      description="Simulated: no money moves."
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Notice tone="info">
          Payments settle arrears and fees first, then interest and principal that are due. Money
          paid before an instalment's due date is a <strong>prepayment</strong>: it reduces the
          principal and shortens the term — it doesn't pay the next instalment in advance. To repay
          everything, use the settlement quote.
        </Notice>
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Amount"
            inputMode="decimal"
            prefix="R"
            required
            error={errors.amount?.message}
            {...form.register("amount")}
          />
          {staff && (
            <TextField
              label="Value date"
              type="date"
              max={businessToday()}
              hint="Leave blank for today."
              error={errors.value_date?.message}
              {...form.register("value_date")}
            />
          )}
        </div>
        <FormMessage
          error={record.error}
          success={
            result && (
              <>
                {result.replayed
                  ? "This payment was already recorded; nothing was taken twice. "
                  : "Payment recorded. "}
                Balance after <MoneyText amount={result.transaction.balance_after} currency={c} /> ·
                value date {formatDate(result.transaction.value_date)}.
              </>
            )
          }
        />
        <div>
          <Button
            type="submit"
            loading={isSubmitting}
            disabled={account.status === "SETTLED" || account.status === "CLOSED"}
          >
            {staff ? "Record repayment" : "Pay now"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

export function SettlementPanel({ account, staff }: { account: LoanAccount; staff: boolean }) {
  const today = businessToday();
  const [asOf, setAsOf] = useState(today);
  const [requested, setRequested] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const quote = useSettlementQuote(account.id, asOf, requested);
  const settle = useSettle(account.id);
  const [currentKey, ensureKey, resetKey] = useIntentKey();
  const c = account.currency;
  const closed = account.status === "SETTLED" || account.status === "CLOSED";

  return (
    <Card title="Early settlement" description="What it would cost to repay the loan in full.">
      {closed ? (
        <p className="text-sm text-muted">
          This loan is {account.status.toLowerCase()}
          {account.settled_on ? ` (${formatDate(account.settled_on)})` : ""}.
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-end gap-3">
            {staff && (
              <TextField
                label="Quote as of"
                type="date"
                min={today}
                value={asOf}
                onChange={(event) => setAsOf(event.target.value)}
              />
            )}
            <Button
              variant="secondary"
              onClick={() => setRequested(true)}
              loading={quote.isFetching}
            >
              {requested ? "Refresh quote" : "Get a settlement quote"}
            </Button>
          </div>
          {requested && (
            <QueryView query={quote} loadingLabel="Calculating the settlement amount">
              {(data) => (
                <div className="flex flex-col gap-4">
                  <KeyValueList
                    columns={3}
                    items={[
                      {
                        label: "Principal not yet due",
                        value: <MoneyText amount={data.principal_not_yet_due} currency={c} />,
                      },
                      {
                        label: `Accrued interest (${data.accrued_days} days)`,
                        value: <MoneyText amount={data.accrued_interest} currency={c} />,
                      },
                      {
                        label: "Arrears principal",
                        value: <MoneyText amount={data.arrears_principal} currency={c} />,
                      },
                      {
                        label: "Arrears interest",
                        value: <MoneyText amount={data.arrears_interest} currency={c} />,
                      },
                      {
                        label: "Arrears service fees",
                        value: <MoneyText amount={data.arrears_service_fees} currency={c} />,
                      },
                      {
                        label: "Outstanding charges",
                        value: <MoneyText amount={data.outstanding_charges} currency={c} />,
                      },
                      {
                        label: "Projected late fees",
                        value: <MoneyText amount={data.projected_late_fees} currency={c} />,
                      },
                      {
                        label: "Settlement fee",
                        value: <MoneyText amount={data.settlement_fee} currency={c} />,
                      },
                      {
                        label: `Total to settle on ${formatDate(data.as_of)}`,
                        value: (
                          <MoneyText
                            amount={data.total}
                            currency={c}
                            className="text-lg font-semibold"
                          />
                        ),
                      },
                    ]}
                  />
                  {data.note && <p className="text-xs text-muted">{data.note}</p>}
                  {data.as_of === today && (
                    <div>
                      <Button onClick={() => setConfirming(true)}>Settle the loan</Button>
                    </div>
                  )}
                  <ConfirmDialog
                    open={confirming}
                    onOpenChange={(open) => {
                      setConfirming(open);
                      if (!open) settle.reset();
                    }}
                    title="Settle this loan?"
                    confirmLabel={`Pay ${data.total} and settle`}
                    pending={settle.isPending}
                    error={settle.error ? <FormMessage error={settle.error} /> : undefined}
                    onConfirm={() => {
                      ensureKey(data.total);
                      settle.mutate(
                        { amount: data.total, idempotencyKey: currentKey() },
                        {
                          onSuccess: () => {
                            resetKey();
                            setConfirming(false);
                          },
                        },
                      );
                    }}
                  >
                    A simulated payment of{" "}
                    <MoneyText amount={data.total} currency={c} className="font-semibold" /> closes
                    the account today. This can't be undone.
                  </ConfirmDialog>
                </div>
              )}
            </QueryView>
          )}
          {settle.isSuccess && (
            <Notice tone="positive" role="status">
              The loan is settled.
            </Notice>
          )}
        </div>
      )}
    </Card>
  );
}
