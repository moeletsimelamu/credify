import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type QueryValue } from "@/api/client";
import type {
  Charge,
  LoanAccount,
  LoanSchedule,
  LoanStatus,
  Page,
  Payment,
  ScheduleVersion,
  ScheduleVersionDetail,
  SettlementQuote,
  Transaction,
} from "@/api/types";

export const loanKeys = {
  list: (filters: Record<string, QueryValue>) => ["loans", "list", filters] as const,
  one: (id: string) => ["loan", id] as const,
  part: (id: string, part: string, ...rest: unknown[]) => ["loan", id, part, ...rest] as const,
};

export interface LoanFilters {
  status?: LoanStatus[];
  applicant_id?: string;
  min_days_past_due?: number;
  page?: number;
  page_size?: number;
}

export function useLoans(filters: LoanFilters = {}) {
  const query = filters as Record<string, QueryValue>;
  return useQuery({
    queryKey: loanKeys.list(query),
    queryFn: () => api<Page<LoanAccount>>("/loan-accounts", { query }),
    placeholderData: keepPreviousData,
  });
}

export function useLoan(id: string) {
  return useQuery({
    queryKey: loanKeys.one(id),
    queryFn: () => api<LoanAccount>(`/loan-accounts/${id}`),
  });
}

export function useLoanSchedule(id: string) {
  return useQuery({
    queryKey: loanKeys.part(id, "schedule"),
    queryFn: () => api<LoanSchedule>(`/loan-accounts/${id}/schedule`),
  });
}

export function useScheduleVersions(id: string) {
  return useQuery({
    queryKey: loanKeys.part(id, "schedules"),
    queryFn: () => api<ScheduleVersion[]>(`/loan-accounts/${id}/schedules`),
  });
}

export function useScheduleVersion(id: string, version: number | null) {
  return useQuery({
    queryKey: loanKeys.part(id, "schedules", version),
    queryFn: () => api<ScheduleVersionDetail>(`/loan-accounts/${id}/schedules/${version}`),
    enabled: version !== null,
  });
}

export function useTransactions(id: string) {
  return useQuery({
    queryKey: loanKeys.part(id, "transactions"),
    queryFn: () => api<Transaction[]>(`/loan-accounts/${id}/transactions`),
  });
}

export function useCharges(id: string) {
  return useQuery({
    queryKey: loanKeys.part(id, "charges"),
    queryFn: () => api<Charge[]>(`/loan-accounts/${id}/charges`),
  });
}

export function useSettlementQuote(id: string, asOf: string, enabled: boolean) {
  return useQuery({
    queryKey: loanKeys.part(id, "settlement-quote", asOf),
    queryFn: () =>
      api<SettlementQuote>(`/loan-accounts/${id}/settlement-quote`, { query: { as_of: asOf } }),
    enabled,
    retry: false,
  });
}

function useLoanPayment<V>(id: string, run: (variables: V) => Promise<Payment>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async (payment) => {
      queryClient.setQueryData(loanKeys.one(id), payment.account);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: loanKeys.one(id) }),
        queryClient.invalidateQueries({ queryKey: ["loans", "list"] }),
        queryClient.invalidateQueries({ queryKey: ["analytics"] }),
      ]);
    },
  });
}

/**
 * The idempotency key is created once per payment the person intends to make and reused if
 * they retry after a network failure, so a repeated click can never take the money twice.
 */
export function useRecordRepayment(id: string) {
  return useLoanPayment<{ amount: string; valueDate?: string; idempotencyKey: string }>(
    id,
    ({ amount, valueDate, idempotencyKey }) =>
      api<Payment>(`/loan-accounts/${id}/repayments`, {
        method: "POST",
        body: { amount, value_date: valueDate || null },
        idempotencyKey,
      }),
  );
}

export function useSettle(id: string) {
  return useLoanPayment<{ amount: string; idempotencyKey: string }>(
    id,
    ({ amount, idempotencyKey }) =>
      api<Payment>(`/loan-accounts/${id}/settlement`, {
        method: "POST",
        body: { amount },
        idempotencyKey,
      }),
  );
}
