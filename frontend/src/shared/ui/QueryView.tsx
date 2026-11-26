import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { EmptyState, ErrorState, LoadingState } from "./Feedback";

interface QueryViewProps<T> {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  /** Shown instead of children when isEmpty(data) is true. */
  empty?: ReactNode;
  isEmpty?: (data: T) => boolean;
  loadingLabel?: string;
  errorTitle?: string;
  /** Rendered instead of the error state for expected errors (e.g. a 404 meaning "none yet"). */
  renderError?: (error: unknown) => ReactNode | undefined;
}

/** Every data view gets the same loading, error (with retry) and empty states. */
export function QueryView<T>({
  query,
  children,
  empty,
  isEmpty,
  loadingLabel,
  errorTitle,
  renderError,
}: QueryViewProps<T>) {
  if (query.isPending) return <LoadingState label={loadingLabel} />;
  if (query.isError) {
    const custom = renderError?.(query.error);
    if (custom !== undefined) return <>{custom}</>;
    return (
      <ErrorState error={query.error} title={errorTitle} onRetry={() => void query.refetch()} />
    );
  }
  if (isEmpty?.(query.data)) {
    return <>{empty ?? <EmptyState title="Nothing here yet" />}</>;
  }
  return <>{children(query.data)}</>;
}
