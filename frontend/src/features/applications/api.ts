import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, type QueryValue } from "@/api/client";
import type {
  AffordabilityAssessment,
  ApplicantDecision,
  Application,
  ApplicationCreate,
  ApplicationDetail,
  ApplicationDocument,
  ApplicationStatus,
  BureauReport,
  Decision,
  DecisionCreate,
  DocumentType,
  InformationRequest,
  LoanAccount,
  Page,
  Product,
  Reason,
  Recommendation,
  RecommendationCreate,
  ReviewNote,
  RiskAssessment,
  SchedulePreview,
  StaffMember,
  StatusHistory,
} from "@/api/types";

export const applicationKeys = {
  list: (filters: Record<string, QueryValue>) => ["applications", "list", filters] as const,
  one: (id: string) => ["application", id] as const,
  part: (id: string, part: string) => ["application", id, part] as const,
};

async function orNull<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

// --- Reference data ----------------------------------------------------------------------

export function useProducts(includeInactive = false) {
  return useQuery({
    queryKey: ["products", { includeInactive }],
    queryFn: () =>
      api<Page<Product>>("/products", {
        query: { include_inactive: includeInactive || undefined, page_size: 100 },
      }),
    staleTime: 5 * 60_000,
  });
}

export function useStaffDirectory(enabled = true) {
  return useQuery({
    queryKey: ["staff"],
    queryFn: () => api<StaffMember[]>("/staff"),
    staleTime: 5 * 60_000,
    enabled,
  });
}

export function useReasonCodes() {
  return useQuery({
    queryKey: ["decision-reason-codes"],
    queryFn: () => api<Reason[]>("/decision-reason-codes"),
    staleTime: Infinity,
  });
}

// --- Applications ------------------------------------------------------------------------

export interface ApplicationFilters {
  status?: ApplicationStatus[];
  loan_product_id?: string;
  assigned_officer_id?: string;
  applicant_id?: string;
  page?: number;
  page_size?: number;
}

export function useApplications(filters: ApplicationFilters = {}) {
  const query = filters as Record<string, QueryValue>;
  return useQuery({
    queryKey: applicationKeys.list(query),
    queryFn: () => api<Page<Application>>("/applications", { query }),
    placeholderData: keepPreviousData,
  });
}

export function useApplication(id: string) {
  return useQuery({
    queryKey: applicationKeys.one(id),
    queryFn: () => api<ApplicationDetail>(`/applications/${id}`),
  });
}

function usePart<T>(id: string, part: string, path: string, enabled = true) {
  return useQuery({
    queryKey: applicationKeys.part(id, part),
    queryFn: () => api<T>(`/applications/${id}${path}`),
    enabled,
  });
}

export const useStatusHistory = (id: string) =>
  usePart<StatusHistory[]>(id, "history", "/status-history");
export const useNotes = (id: string, enabled = true) =>
  usePart<ReviewNote[]>(id, "notes", "/notes", enabled);
export const useInformationRequests = (id: string) =>
  usePart<InformationRequest[]>(id, "information-requests", "/information-requests");
export const useDocuments = (id: string) =>
  usePart<ApplicationDocument[]>(id, "documents", "/documents");
export const useAffordability = (id: string, enabled = true) =>
  usePart<AffordabilityAssessment[]>(id, "affordability", "/affordability-assessments", enabled);
export const useRiskAssessments = (id: string, enabled = true) =>
  usePart<RiskAssessment[]>(id, "risk", "/risk-assessments", enabled);
export const useRecommendations = (id: string, enabled = true) =>
  usePart<Recommendation[]>(id, "recommendations", "/recommendations", enabled);

export function useBureauReport(id: string, enabled = true) {
  return useQuery({
    queryKey: applicationKeys.part(id, "bureau"),
    queryFn: () => orNull(api<BureauReport>(`/applications/${id}/credit-bureau-report`)),
    enabled,
  });
}

export function useDecision<T extends Decision | ApplicantDecision>(id: string, enabled = true) {
  return useQuery({
    queryKey: applicationKeys.part(id, "decision"),
    queryFn: () => orNull(api<T>(`/applications/${id}/decision`)),
    enabled,
  });
}

export function useApplicationLoan(id: string, enabled = true) {
  return useQuery({
    queryKey: applicationKeys.part(id, "loan-account"),
    queryFn: () => orNull(api<LoanAccount>(`/applications/${id}/loan-account`)),
    enabled,
  });
}

export function useApplicationSchedule(id: string) {
  return useQuery({
    queryKey: applicationKeys.part(id, "schedule-preview"),
    queryFn: () => api<SchedulePreview>(`/applications/${id}/repayment-schedule`),
  });
}

export function useProductSchedulePreview(
  productId: string | undefined,
  amount: string,
  termMonths: number | undefined,
) {
  const ready = Boolean(productId) && /^\d+(\.\d{1,2})?$/.test(amount) && Boolean(termMonths);
  return useQuery({
    queryKey: ["schedule-preview", productId, amount, termMonths],
    queryFn: () =>
      api<SchedulePreview>(`/products/${productId}/repayment-schedule`, {
        query: { amount, term_months: termMonths },
      }),
    enabled: ready,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

// --- Commands ----------------------------------------------------------------------------

/** Every command refreshes the whole application (status, actions and each tab). */
function useApplicationCommand<V, R = ApplicationDetail>(
  id: string,
  run: (variables: V) => Promise<R>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: applicationKeys.one(id) }),
        queryClient.invalidateQueries({ queryKey: ["applications", "list"] }),
      ]);
    },
  });
}

type Versioned = { version: number };

export function useTransition(id: string, action: string) {
  return useApplicationCommand<Versioned & { body?: unknown }>(id, ({ version, body }) =>
    api<ApplicationDetail>(`/applications/${id}/${action}`, {
      method: "POST",
      ifMatch: version,
      body,
    }),
  );
}

export function useCreateApplication() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (values: ApplicationCreate) =>
      api<ApplicationDetail>("/applications", { method: "POST", body: values }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["applications", "list"] }),
  });
}

export function useUpdateApplication(id: string) {
  return useApplicationCommand<Versioned & { values: Partial<ApplicationCreate> }>(
    id,
    ({ version, values }) =>
      api<ApplicationDetail>(`/applications/${id}`, {
        method: "PATCH",
        ifMatch: version,
        body: values,
      }),
  );
}

export function useAssign(id: string) {
  return useApplicationCommand<Versioned & { officerId: string }>(id, ({ version, officerId }) =>
    api<ApplicationDetail>(`/applications/${id}/assign`, {
      method: "POST",
      ifMatch: version,
      body: { officer_id: officerId },
    }),
  );
}

export function useRequestInformation(id: string) {
  return useApplicationCommand<Versioned & { message: string }, InformationRequest>(
    id,
    ({ version, message }) =>
      api<InformationRequest>(`/applications/${id}/request-information`, {
        method: "POST",
        ifMatch: version,
        body: { message },
      }),
  );
}

export function useRespondToRequest(id: string) {
  return useApplicationCommand<{ requestId: string; response: string }, InformationRequest>(
    id,
    ({ requestId, response }) =>
      api<InformationRequest>(`/applications/${id}/information-requests/${requestId}/respond`, {
        method: "POST",
        body: { response_text: response },
      }),
  );
}

export function useAddNote(id: string) {
  return useApplicationCommand<string, ReviewNote>(id, (body) =>
    api<ReviewNote>(`/applications/${id}/notes`, { method: "POST", body: { body } }),
  );
}

export function useAddDocument(id: string) {
  return useApplicationCommand<
    { document_type: DocumentType; file_name: string; content_type: string; size_bytes: number },
    ApplicationDocument
  >(id, (body) =>
    api<ApplicationDocument>(`/applications/${id}/documents`, { method: "POST", body }),
  );
}

export function useReviewDocument(id: string) {
  return useApplicationCommand<
    { documentId: string; status: "ACCEPTED" | "REJECTED" },
    ApplicationDocument
  >(id, ({ documentId, status }) =>
    api<ApplicationDocument>(`/applications/${id}/documents/${documentId}`, {
      method: "PATCH",
      body: { status },
    }),
  );
}

export function useRunAffordability(id: string) {
  return useApplicationCommand<void, AffordabilityAssessment>(id, () =>
    api<AffordabilityAssessment>(`/applications/${id}/affordability-assessments`, {
      method: "POST",
    }),
  );
}

export function useRunRisk(id: string) {
  return useApplicationCommand<void, RiskAssessment>(id, () =>
    api<RiskAssessment>(`/applications/${id}/risk-assessments`, { method: "POST" }),
  );
}

export function useRecommend(id: string) {
  return useApplicationCommand<Versioned & { values: RecommendationCreate }, Recommendation>(
    id,
    ({ version, values }) =>
      api<Recommendation>(`/applications/${id}/recommendation`, {
        method: "POST",
        ifMatch: version,
        body: values,
      }),
  );
}

export function useDecide(id: string) {
  return useApplicationCommand<Versioned & { values: DecisionCreate }, Decision>(
    id,
    ({ version, values }) =>
      api<Decision>(`/applications/${id}/decision`, {
        method: "POST",
        ifMatch: version,
        body: values,
      }),
  );
}

export function useDisburse(id: string) {
  return useApplicationCommand<void, LoanAccount>(id, () =>
    api<LoanAccount>(`/applications/${id}/disburse`, { method: "POST" }),
  );
}
