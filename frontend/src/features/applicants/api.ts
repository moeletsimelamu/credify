import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/api/client";
import type {
  Applicant,
  ApplicantCreate,
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

export const applicantKeys = {
  me: ["applicants", "me"] as const,
  one: (id: string) => ["applicants", id] as const,
  profile: (id: string) => ["applicants", id, "financial-profile"] as const,
};

/** The signed-in applicant's profile, or null if they haven't created one yet. */
export function useMyApplicant() {
  return useQuery({
    queryKey: applicantKeys.me,
    queryFn: async () => {
      try {
        return await api<Applicant>("/applicants/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });
}

export function useApplicant(id: string | undefined) {
  return useQuery({
    queryKey: applicantKeys.one(id ?? ""),
    queryFn: () => api<Applicant>(`/applicants/${id}`),
    enabled: Boolean(id),
  });
}

export function useFinancialProfile(applicantId: string | undefined) {
  return useQuery({
    queryKey: applicantKeys.profile(applicantId ?? ""),
    queryFn: () => api<FinancialProfile>(`/applicants/${applicantId}/financial-profile`),
    enabled: Boolean(applicantId),
  });
}

export function useSaveApplicant(existing: Applicant | null | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (values: ApplicantCreate) =>
      existing
        ? api<Applicant>(`/applicants/${existing.id}`, { method: "PATCH", body: values })
        : api<Applicant>("/applicants", { method: "POST", body: values }),
    onSuccess: (applicant) => {
      queryClient.setQueryData(applicantKeys.me, applicant);
      void queryClient.invalidateQueries({ queryKey: ["applicants", applicant.id] });
    },
  });
}

export type RecordKind = "employment" | "income" | "expenses" | "commitments";

export interface RecordTypes {
  employment: { read: Employment; write: EmploymentCreate };
  income: { read: Income; write: IncomeCreate };
  expenses: { read: Expense; write: ExpenseCreate };
  commitments: { read: Commitment; write: CommitmentCreate };
}

/** Create, update or remove one financial-profile record. */
export function useFinancialRecord<K extends RecordKind>(applicantId: string, kind: K) {
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: applicantKeys.profile(applicantId) });
  const base = `/applicants/${applicantId}/${kind}`;

  const save = useMutation({
    mutationFn: ({ id, values }: { id?: string; values: RecordTypes[K]["write"] }) =>
      id
        ? api<RecordTypes[K]["read"]>(`${base}/${id}`, { method: "PATCH", body: values })
        : api<RecordTypes[K]["read"]>(base, { method: "POST", body: values }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api<void>(`${base}/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
  return { save, remove };
}
