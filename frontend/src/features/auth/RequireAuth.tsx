import { Navigate, Outlet, useLocation } from "react-router";

import { LoadingState } from "@/shared/ui";

import { homePathFor, useAuth } from "./useAuth";

/** Route guard. The server enforces every permission; this only routes people sensibly. */
export function RequireAuth({ area }: { area: "portal" | "office" }) {
  const { state } = useAuth();
  const location = useLocation();

  if (state.status === "loading") {
    return (
      <div className="mx-auto max-w-md p-10">
        <LoadingState label="Checking your session" />
      </div>
    );
  }
  if (state.status === "anonymous") {
    // After an explicit sign-out nobody should be sent back to the last person's page.
    if (state.reason === "signed-out") return <Navigate to="/login" replace />;
    const next = encodeURIComponent(location.pathname + location.search);
    const expired = state.reason === "expired" ? "&expired=1" : "";
    return <Navigate to={`/login?next=${next}${expired}`} replace />;
  }
  const isApplicant = state.user.role === "APPLICANT";
  if ((area === "portal") !== isApplicant) {
    return <Navigate to={homePathFor(state.user)} replace />;
  }
  return <Outlet />;
}

export function HomeRedirect() {
  const { state } = useAuth();
  if (state.status === "loading") return <LoadingState label="Checking your session" />;
  if (state.status === "anonymous") return <Navigate to="/login" replace />;
  return <Navigate to={homePathFor(state.user)} replace />;
}
