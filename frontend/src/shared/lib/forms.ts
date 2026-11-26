import { useEffect } from "react";
import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import { ApiError } from "@/api/client";

/**
 * Copy the server's field-level validation messages onto the form. Returns true when at least
 * one field matched, so the caller can skip a duplicate form-level message.
 */
export function applyServerErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: ReadonlyArray<Path<T>>,
): boolean {
  if (!(error instanceof ApiError)) return false;
  let matched = false;
  for (const [field, message] of Object.entries(error.fieldErrors)) {
    if ((fields as readonly string[]).includes(field)) {
      setError(field as Path<T>, { type: "server", message });
      matched = true;
    }
  }
  return matched;
}

/** Keep the browser tab title in step with the page. */
export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · Credify`;
  }, [title]);
}
