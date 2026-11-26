import type { ApplicationStatus } from "@/api/types";
import type { ApplicationFilters } from "@/features/applications/api";
import { DECIDED_STATUSES, OPEN_STATUSES } from "@/features/applications/workflow";
import { APPLICATION_STATUSES } from "@/shared/lib/labels";

export const VIEWS = [
  { key: "open", label: "Open" },
  { key: "mine", label: "Assigned to me" },
  { key: "decision", label: "Awaiting decision" },
  { key: "decided", label: "Closed" },
  { key: "all", label: "All" },
] as const;
export type View = (typeof VIEWS)[number]["key"];

export const PAGE_SIZE = 25;

export function filtersFor(
  view: View,
  userId: string,
  status: string,
  productId: string,
  page: number,
): ApplicationFilters {
  const base: ApplicationFilters = {
    page,
    page_size: PAGE_SIZE,
    loan_product_id: productId || undefined,
  };
  if (status)
    return {
      ...base,
      status: [status as ApplicationStatus],
      assigned_officer_id: view === "mine" ? userId : undefined,
    };
  switch (view) {
    case "open":
      return { ...base, status: OPEN_STATUSES };
    case "mine":
      return { ...base, status: OPEN_STATUSES, assigned_officer_id: userId };
    case "decision":
      return { ...base, status: ["RECOMMENDED_APPROVAL", "RECOMMENDED_REJECTION"] };
    case "decided":
      return { ...base, status: DECIDED_STATUSES };
    default:
      return { ...base, status: APPLICATION_STATUSES.filter((s) => s !== "DRAFT") };
  }
}
