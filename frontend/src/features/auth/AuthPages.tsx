import { zodResolver } from "@hookform/resolvers/zod";
import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router";
import { z } from "zod";

import { api } from "@/api/client";
import { applyServerErrors, usePageTitle } from "@/shared/lib/forms";
import { Button, FormMessage, Notice, TextField } from "@/shared/ui";

import { homePathFor, useAuth } from "./useAuth";

function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col lg:flex-row">
      <aside className="flex flex-col justify-between bg-ink-900 px-8 py-10 text-ink-200 lg:w-[26rem]">
        <div>
          <p className="text-xl font-semibold text-white">Credify</p>
          <p className="mt-3 max-w-xs text-sm leading-relaxed">
            A lending and credit-assessment simulation built for learning.
          </p>
        </div>
        <p className="mt-8 max-w-xs text-xs leading-relaxed text-ink-300">
          Educational project. Every borrower, bureau record and payment is synthetic. Credify does
          not connect to real financial institutions and does not make legally compliant lending
          decisions.
        </p>
      </aside>
      <main id="main" className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <h1 data-page-heading tabIndex={-1} className="mb-6 text-2xl font-semibold outline-none">
            {title}
          </h1>
          {children}
        </div>
      </main>
    </div>
  );
}

const loginSchema = z.object({
  email: z.string().trim().min(1, "Email is required").email("Enter a valid email address"),
  password: z.string().min(1, "Password is required"),
});
type LoginValues = z.infer<typeof loginSchema>;

/** Only same-app paths are honoured, so ?next= can't send anyone off-site. */
function safeNext(next: string | null): string | null {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : null;
}

export function LoginPage() {
  usePageTitle("Sign in");
  const { state, login } = useAuth();
  const [params] = useSearchParams();
  const [error, setError] = useState<unknown>(null);
  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  // Signing in (or arriving already signed in) lands on ?next= when it is a same-app path.
  if (state.status === "authenticated") {
    return <Navigate to={safeNext(params.get("next")) ?? homePathFor(state.user)} replace />;
  }

  const onSubmit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      await login(values.email, values.password);
    } catch (caught) {
      setError(caught);
    }
  });

  const { errors, isSubmitting } = form.formState;
  return (
    <AuthLayout title="Sign in">
      {params.get("expired") && (
        <Notice tone="info" role="status" className="mb-4">
          Your session ended. Sign in again to continue where you left off.
        </Notice>
      )}
      {params.get("registered") && (
        <Notice tone="positive" role="status" className="mb-4">
          Your account is ready. Sign in to continue.
        </Notice>
      )}
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
        <TextField
          label="Email"
          type="email"
          autoComplete="email"
          required
          error={errors.email?.message}
          {...form.register("email")}
        />
        <TextField
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          error={errors.password?.message}
          {...form.register("password")}
        />
        <FormMessage error={error} />
        <Button type="submit" loading={isSubmitting}>
          Sign in
        </Button>
      </form>
      <p className="mt-6 text-sm text-muted">
        New applicant?{" "}
        <Link to="/register" className="font-medium text-accent underline-offset-2 hover:underline">
          Create an account
        </Link>
      </p>
    </AuthLayout>
  );
}

const registerSchema = z
  .object({
    full_name: z.string().trim().min(2, "Enter your full name").max(200, "Too long"),
    email: z.string().trim().min(1, "Email is required").email("Enter a valid email address"),
    password: z
      .string()
      .min(12, "Use at least 12 characters")
      .max(128, "Use at most 128 characters"),
    confirm: z.string(),
  })
  .refine((values) => values.password === values.confirm, {
    path: ["confirm"],
    message: "Passwords don't match",
  });
type RegisterValues = z.infer<typeof registerSchema>;

export function RegisterPage() {
  usePageTitle("Create an account");
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);
  const form = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: { full_name: "", email: "", password: "", confirm: "" },
  });

  const onSubmit = form.handleSubmit(async ({ confirm: _confirm, ...values }) => {
    setError(null);
    try {
      await api("/auth/register", { method: "POST", body: values, auth: false });
      navigate("/login?registered=1", { replace: true });
    } catch (caught) {
      if (!applyServerErrors(caught, form.setError, ["full_name", "email", "password"])) {
        setError(caught);
      }
    }
  });

  const { errors, isSubmitting } = form.formState;
  return (
    <AuthLayout title="Create an applicant account">
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
        <TextField
          label="Full name"
          autoComplete="name"
          required
          error={errors.full_name?.message}
          {...form.register("full_name")}
        />
        <TextField
          label="Email"
          type="email"
          autoComplete="email"
          required
          error={errors.email?.message}
          {...form.register("email")}
        />
        <TextField
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          hint="At least 12 characters. A passphrase works well."
          error={errors.password?.message}
          {...form.register("password")}
        />
        <TextField
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          required
          error={errors.confirm?.message}
          {...form.register("confirm")}
        />
        <FormMessage error={error} />
        <Button type="submit" loading={isSubmitting}>
          Create account
        </Button>
      </form>
      <p className="mt-6 text-sm text-muted">
        Already registered?{" "}
        <Link to="/login" className="font-medium text-accent underline-offset-2 hover:underline">
          Sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
