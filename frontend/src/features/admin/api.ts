import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, type QueryValue } from "@/api/client";
import type {
  AuditEvent,
  ModelVersion,
  Page,
  Portfolio,
  Product,
  ProductCreate,
  ProductUpdate,
  Role,
  User,
  UserCreate,
  UserUpdate,
} from "@/api/types";

// --- Users -------------------------------------------------------------------------------

export function useUsers(role: Role | undefined, page: number) {
  return useQuery({
    queryKey: ["users", { role, page }],
    queryFn: () => api<Page<User>>("/users", { query: { role, page, page_size: 25 } }),
    placeholderData: keepPreviousData,
  });
}

export function useSaveUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, values }: { id?: string; values: UserCreate | UserUpdate }) =>
      id
        ? api<User>(`/users/${id}`, { method: "PATCH", body: values })
        : api<User>("/users", { method: "POST", body: values }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["users"] });
      void queryClient.invalidateQueries({ queryKey: ["staff"] });
    },
  });
}

// --- Products ----------------------------------------------------------------------------

export function useSaveProduct() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, values }: { id?: string; values: ProductCreate | ProductUpdate }) =>
      id
        ? api<Product>(`/products/${id}`, { method: "PATCH", body: values })
        : api<Product>("/products", { method: "POST", body: values }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });
}

// --- Audit -------------------------------------------------------------------------------

export interface AuditFilters {
  entity_type?: string;
  entity_id?: string;
  actor_user_id?: string;
  action?: string;
  page?: number;
  page_size?: number;
}

export function useAuditEvents(filters: AuditFilters, enabled = true) {
  const query = filters as Record<string, QueryValue>;
  return useQuery({
    queryKey: ["audit-events", query],
    queryFn: () => api<Page<AuditEvent>>("/audit-events", { query }),
    placeholderData: keepPreviousData,
    enabled,
  });
}

// --- Models ------------------------------------------------------------------------------

export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: () => api<ModelVersion[]>("/models") });
}

export function useActivateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<ModelVersion>(`/models/${id}/activate`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["models"] }),
  });
}

// --- Portfolio analytics -----------------------------------------------------------------

export function usePortfolio(dateFrom: string, dateTo: string) {
  return useQuery({
    queryKey: ["analytics", "portfolio", dateFrom, dateTo],
    queryFn: () =>
      api<Portfolio>("/analytics/portfolio", { query: { date_from: dateFrom, date_to: dateTo } }),
    placeholderData: keepPreviousData,
  });
}

/** One portfolio summary per calendar month, oldest first, for trend lines. */
export function useMonthlyPortfolio(months: Array<{ label: string; from: string; to: string }>) {
  return useQueries({
    queries: months.map((month) => ({
      queryKey: ["analytics", "portfolio", month.from, month.to],
      queryFn: () =>
        api<Portfolio>("/analytics/portfolio", {
          query: { date_from: month.from, date_to: month.to },
        }),
      staleTime: 60_000,
    })),
  });
}
