import { z } from "zod";

/** Money typed by a person: digits with up to two decimals. Sent to the API as a string. */
export const moneyInput = (label = "Amount") =>
  z
    .string()
    .trim()
    .min(1, `${label} is required`)
    .regex(/^\d{1,12}(\.\d{1,2})?$/, `${label} must be a number with up to two decimals`);

export const optionalMoneyInput = (label = "Amount") =>
  z
    .string()
    .trim()
    .regex(/^(\d{1,12}(\.\d{1,2})?)?$/, `${label} must be a number with up to two decimals`);

export const requiredText = (label: string, max = 200) =>
  z.string().trim().min(1, `${label} is required`).max(max, `${label} is too long`);

export const optionalText = (max = 200) => z.string().trim().max(max, "Too long");

export const isoDate = (label: string) =>
  z
    .string()
    .min(1, `${label} is required`)
    .regex(/^\d{4}-\d{2}-\d{2}$/, `${label} must be a date`);

export const wholeNumber = (label: string, min: number, max: number) =>
  z.coerce
    .number({ error: `${label} must be a whole number` })
    .int(`${label} must be a whole number`)
    .min(min, `${label} must be at least ${min}`)
    .max(max, `${label} must be at most ${max}`);

/** Empty strings become null, so optional fields are sent as "not provided". */
export function blankToNull<T extends Record<string, unknown>>(values: T): T {
  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => [key, value === "" ? null : value]),
  ) as T;
}
