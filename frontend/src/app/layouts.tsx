import { useEffect, useRef, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useRouteError } from "react-router";

import { useAuth, useCurrentUser } from "@/features/auth/useAuth";
import { cx } from "@/shared/lib/cx";
import { ROLE_LABELS } from "@/shared/lib/labels";
import { ButtonLink, ErrorState } from "@/shared/ui";

/** After client-side navigation, move focus to the new page's heading. */
function useRouteFocus() {
  const location = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const frame = requestAnimationFrame(() => {
      document.querySelector<HTMLElement>("[data-page-heading]")?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [location.pathname]);
}

function SkipLink() {
  return (
    <a
      href="#main"
      className="sr-only z-50 rounded-md bg-surface px-3 py-2 text-sm font-medium text-fg focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
    >
      Skip to main content
    </a>
  );
}

function SimulationBanner({ dark = false }: { dark?: boolean }) {
  return (
    <p
      className={cx(
        "px-4 py-1.5 text-center text-xs",
        dark ? "bg-ink-950 text-ink-300" : "bg-sunken text-muted",
      )}
    >
      Educational simulation · synthetic data only · no real lending decisions
    </p>
  );
}

function useSignOut() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  return async () => {
    await logout();
    navigate("/login", { replace: true });
  };
}

interface NavItem {
  to: string;
  label: string;
  end?: boolean;
  permission?: string;
}

const OFFICE_NAV: Array<{ heading?: string; items: NavItem[] }> = [
  {
    items: [
      { to: "/office", label: "Work queue", end: true, permission: "application:read_any" },
      { to: "/office/loans", label: "Loan book", permission: "loan:read_any" },
    ],
  },
  {
    heading: "Reporting",
    items: [
      { to: "/office/portfolio", label: "Portfolio", permission: "analytics:portfolio" },
      { to: "/office/models", label: "Risk models", permission: "analytics:model" },
    ],
  },
  {
    heading: "Administration",
    items: [
      { to: "/office/admin/users", label: "Users", permission: "user:manage" },
      { to: "/office/admin/products", label: "Products", permission: "product:manage" },
      { to: "/office/admin/audit", label: "Audit log", permission: "audit:read" },
    ],
  },
];

export function OfficeLayout() {
  useRouteFocus();
  const user = useCurrentUser();
  const { can } = useAuth();
  const signOut = useSignOut();

  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink />
      <SimulationBanner dark />
      <div className="flex flex-1 flex-col md:flex-row">
        <aside className="flex shrink-0 flex-col bg-ink-900 text-ink-200 md:w-60">
          <div className="px-5 pt-5 pb-4">
            <p className="text-lg font-semibold text-white">Credify</p>
            <p className="text-xs text-ink-300">Back office</p>
          </div>
          <nav aria-label="Back office" className="flex-1 px-3 pb-4">
            {OFFICE_NAV.map((group, index) => {
              const items = group.items.filter((item) => !item.permission || can(item.permission));
              if (items.length === 0) return null;
              return (
                <div key={index} className="mb-4">
                  {group.heading && (
                    <p className="px-2 pb-1 text-[0.7rem] font-semibold tracking-wider text-ink-300 uppercase">
                      {group.heading}
                    </p>
                  )}
                  <ul className="flex flex-col gap-0.5">
                    {items.map((item) => (
                      <li key={item.to}>
                        <NavLink
                          to={item.to}
                          end={item.end}
                          className={({ isActive }) =>
                            cx(
                              "block rounded-md px-2.5 py-1.5 text-sm",
                              isActive
                                ? "bg-ink-700 font-medium text-white"
                                : "text-ink-200 hover:bg-ink-800 hover:text-white",
                            )
                          }
                        >
                          {item.label}
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </nav>
          <div className="border-t border-ink-700 px-5 py-4">
            <p className="text-sm font-medium text-white">{user.full_name}</p>
            <p className="text-xs text-ink-300">{ROLE_LABELS[user.role]}</p>
            <button
              type="button"
              onClick={() => void signOut()}
              className="mt-3 text-sm text-ink-200 underline-offset-2 hover:text-white hover:underline"
            >
              Sign out
            </button>
          </div>
        </aside>
        <main id="main" className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

const PORTAL_NAV: NavItem[] = [
  { to: "/portal", label: "Overview", end: true },
  { to: "/portal/profile", label: "My details" },
  { to: "/portal/finances", label: "My finances" },
];

export function PortalLayout() {
  useRouteFocus();
  const user = useCurrentUser();
  const signOut = useSignOut();

  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink />
      <SimulationBanner />
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 md:px-6">
          <p className="text-lg font-semibold text-ink-900">Credify</p>
          <nav aria-label="Applicant portal">
            <ul className="flex flex-wrap gap-1">
              {PORTAL_NAV.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) =>
                      cx(
                        "rounded-md px-3 py-1.5 text-sm",
                        isActive
                          ? "bg-accent-soft font-medium text-accent-strong"
                          : "text-muted hover:text-fg",
                      )
                    }
                  >
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-muted">{user.full_name}</span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="text-accent underline-offset-2 hover:underline"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 md:px-6">
        <Outlet />
      </main>
    </div>
  );
}

export function PageFrame({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-md p-10">{children}</div>;
}

export function RouteError() {
  const error = useRouteError();
  return (
    <PageFrame>
      <h1 className="mb-4 text-xl font-semibold">Something went wrong</h1>
      <ErrorState error={error} title="This page failed to load" />
      <div className="mt-4">
        <ButtonLink to="/">Go to the start page</ButtonLink>
      </div>
    </PageFrame>
  );
}

export function NotFound() {
  return (
    <PageFrame>
      <h1 data-page-heading tabIndex={-1} className="mb-2 text-xl font-semibold outline-none">
        Page not found
      </h1>
      <p className="mb-4 text-sm text-muted">The address may be mistyped, or the page has moved.</p>
      <ButtonLink to="/">Go to the start page</ButtonLink>
    </PageFrame>
  );
}
