import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import type { Applicant, ApplicantCreate } from "@/api/types";
import { applyServerErrors, usePageTitle } from "@/shared/lib/forms";
import { formatDate } from "@/shared/lib/format";
import { optionalText, requiredText } from "@/shared/lib/validation";
import { Button, Card, FormMessage, Notice, PageHeader, QueryView, TextField } from "@/shared/ui";

import { useMyApplicant, useSaveApplicant } from "./api";

const schema = z.object({
  first_name: requiredText("First name", 100),
  last_name: requiredText("Last name", 100),
  date_of_birth: z
    .string()
    .min(1, "Date of birth is required")
    .refine((value) => !Number.isNaN(Date.parse(value)), "Enter a valid date"),
  email: z.union([z.literal(""), z.string().trim().email("Enter a valid email address")]),
  phone: z
    .string()
    .trim()
    .regex(/^\+?[0-9 ()-]{7,20}$/, "Enter a phone number of 7–20 digits, e.g. +27 82 555 0101"),
  address_line1: requiredText("Address", 200),
  address_line2: optionalText(200),
  city: requiredText("City", 100),
  postal_code: z
    .string()
    .trim()
    .regex(/^[A-Za-z0-9][A-Za-z0-9 -]{2,9}$/, "Enter a postal code of 3–10 characters"),
  country: z
    .string()
    .trim()
    .regex(/^[A-Z]{2}$/, "Use a two-letter country code, e.g. ZA"),
});
type Values = z.infer<typeof schema>;
const FIELDS = Object.keys(schema.shape) as Array<keyof Values>;

function defaults(applicant: Applicant | null): Values {
  return {
    first_name: applicant?.first_name ?? "",
    last_name: applicant?.last_name ?? "",
    date_of_birth: applicant?.date_of_birth ?? "",
    email: applicant?.email ?? "",
    phone: applicant?.phone ?? "",
    address_line1: applicant?.address_line1 ?? "",
    address_line2: applicant?.address_line2 ?? "",
    city: applicant?.city ?? "",
    postal_code: applicant?.postal_code ?? "",
    country: applicant?.country ?? "ZA",
  };
}

export function ApplicantForm({
  applicant,
  onSaved,
}: {
  applicant: Applicant | null;
  onSaved?: (applicant: Applicant) => void;
}) {
  const save = useSaveApplicant(applicant);
  const [saved, setSaved] = useState(false);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: defaults(applicant),
  });
  const { errors, isSubmitting, isDirty } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    setSaved(false);
    const body: ApplicantCreate = {
      ...values,
      email: values.email || null,
      address_line2: values.address_line2 || null,
    };
    try {
      const result = await save.mutateAsync(body);
      form.reset(defaults(result));
      setSaved(true);
      onSaved?.(result);
    } catch (error) {
      applyServerErrors(error, form.setError, FIELDS);
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="First name"
          autoComplete="given-name"
          required
          error={errors.first_name?.message}
          {...form.register("first_name")}
        />
        <TextField
          label="Last name"
          autoComplete="family-name"
          required
          error={errors.last_name?.message}
          {...form.register("last_name")}
        />
        <TextField
          label="Date of birth"
          type="date"
          autoComplete="bday"
          required
          error={errors.date_of_birth?.message}
          {...form.register("date_of_birth")}
        />
        <TextField
          label="Phone"
          type="tel"
          autoComplete="tel"
          required
          error={errors.phone?.message}
          {...form.register("phone")}
        />
        <TextField
          label="Contact email"
          type="email"
          autoComplete="email"
          hint="Leave blank to use your sign-in email."
          error={errors.email?.message}
          containerClassName="sm:col-span-2"
          {...form.register("email")}
        />
        <TextField
          label="Address"
          autoComplete="address-line1"
          required
          error={errors.address_line1?.message}
          containerClassName="sm:col-span-2"
          {...form.register("address_line1")}
        />
        <TextField
          label="Address line 2"
          autoComplete="address-line2"
          error={errors.address_line2?.message}
          containerClassName="sm:col-span-2"
          {...form.register("address_line2")}
        />
        <TextField
          label="City"
          autoComplete="address-level2"
          required
          error={errors.city?.message}
          {...form.register("city")}
        />
        <TextField
          label="Postal code"
          autoComplete="postal-code"
          required
          error={errors.postal_code?.message}
          {...form.register("postal_code")}
        />
        <TextField
          label="Country code"
          autoComplete="country"
          required
          maxLength={2}
          error={errors.country?.message}
          {...form.register("country")}
        />
      </div>
      <FormMessage
        error={save.error && !Object.keys(errors).length ? save.error : undefined}
        success={saved && !isDirty ? "Your details are saved." : undefined}
      />
      <div>
        <Button type="submit" loading={isSubmitting}>
          {applicant ? "Save changes" : "Create my profile"}
        </Button>
      </div>
    </form>
  );
}

export function ProfilePage() {
  usePageTitle("My details");
  const me = useMyApplicant();
  return (
    <>
      <PageHeader
        title="My details"
        description="Used for your applications. Use invented details: Credify is a simulation and stores synthetic data only."
      />
      <QueryView query={me} loadingLabel="Loading your details">
        {(applicant) => (
          <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
            <Card title={applicant ? "Personal details" : "Create your applicant profile"}>
              <ApplicantForm applicant={applicant} />
            </Card>
            {applicant && (
              <Card title="Reference" as="aside">
                <p className="figure text-sm">{applicant.synthetic_reference}</p>
                <p className="mt-2 text-xs text-muted">
                  Profile created {formatDate(applicant.created_at)}. Staff use this reference to
                  find your records.
                </p>
                <Notice tone="info" className="mt-4">
                  While an application is under review your details are locked. You can change them
                  again if the lender asks you for more information.
                </Notice>
              </Card>
            )}
          </div>
        )}
      </QueryView>
    </>
  );
}
