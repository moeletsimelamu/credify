import type { RouteObject } from "react-router";

import { LoginPage, RegisterPage } from "@/features/auth/AuthPages";
import { HomeRedirect, RequireAuth } from "@/features/auth/RequireAuth";

import { NotFound, OfficeLayout, PortalLayout, RouteError } from "./layouts";

/**
 * Applicant pages load with the shell; back-office pages (and their charts) are split into
 * their own chunks so applicants never download them.
 */
export const routes: RouteObject[] = [
  {
    errorElement: <RouteError />,
    children: [
      { path: "/", element: <HomeRedirect /> },
      { path: "/login", element: <LoginPage /> },
      { path: "/register", element: <RegisterPage /> },
      {
        element: <RequireAuth area="portal" />,
        children: [
          {
            path: "/portal",
            element: <PortalLayout />,
            children: [
              {
                index: true,
                lazy: async () => ({
                  Component: (await import("@/features/portal/PortalHome")).PortalHome,
                }),
              },
              {
                path: "profile",
                lazy: async () => ({
                  Component: (await import("@/features/applicants/ProfilePage")).ProfilePage,
                }),
              },
              {
                path: "finances",
                lazy: async () => ({
                  Component: (await import("@/features/applicants/FinancesPage")).FinancesPage,
                }),
              },
              {
                path: "applications/new",
                lazy: async () => ({
                  Component: (await import("@/features/portal/NewApplicationPage"))
                    .NewApplicationPage,
                }),
              },
              {
                path: "applications/:applicationId",
                lazy: async () => ({
                  Component: (await import("@/features/portal/MyApplicationPage"))
                    .MyApplicationPage,
                }),
              },
              {
                path: "loans/:loanId",
                lazy: async () => ({
                  Component: (await import("@/features/loans/LoanPage")).PortalLoanPage,
                }),
              },
            ],
          },
        ],
      },
      {
        element: <RequireAuth area="office" />,
        children: [
          {
            path: "/office",
            element: <OfficeLayout />,
            children: [
              {
                index: true,
                lazy: async () => ({
                  Component: (await import("@/features/office/WorkQueuePage")).WorkQueuePage,
                }),
              },
              {
                path: "applications/:applicationId",
                lazy: async () => ({
                  Component: (await import("@/features/office/WorkspacePage")).WorkspacePage,
                }),
              },
              {
                path: "loans",
                lazy: async () => ({
                  Component: (await import("@/features/loans/LoanBookPage")).LoanBookPage,
                }),
              },
              {
                path: "loans/:loanId",
                lazy: async () => ({
                  Component: (await import("@/features/loans/LoanPage")).OfficeLoanPage,
                }),
              },
              {
                path: "portfolio",
                lazy: async () => ({
                  Component: (await import("@/features/reporting/PortfolioPage")).PortfolioPage,
                }),
              },
              {
                path: "models",
                lazy: async () => ({
                  Component: (await import("@/features/reporting/ModelsPage")).ModelsPage,
                }),
              },
              {
                path: "admin/users",
                lazy: async () => ({
                  Component: (await import("@/features/admin/UsersPage")).UsersPage,
                }),
              },
              {
                path: "admin/products",
                lazy: async () => ({
                  Component: (await import("@/features/admin/ProductsPage")).ProductsPage,
                }),
              },
              {
                path: "admin/audit",
                lazy: async () => ({
                  Component: (await import("@/features/admin/AuditPage")).AuditPage,
                }),
              },
            ],
          },
        ],
      },
      { path: "*", element: <NotFound /> },
    ],
  },
];
