import { createContext, useContext } from "react";

import type { CurrentUser } from "@/api/types";

export type AuthState =
  | { status: "loading"; user: null }
  /** reason: "signed-out" after an explicit sign-out, "expired" when the session lapsed. */
  | { status: "anonymous"; user: null; reason?: "signed-out" | "expired" }
  | { status: "authenticated"; user: CurrentUser };

export interface AuthContextValue {
  state: AuthState;
  login: (email: string, password: string) => Promise<CurrentUser>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

/** The signed-in user. Only call below a route guard. */
export function useCurrentUser(): CurrentUser {
  const { state } = useAuth();
  if (state.status !== "authenticated") throw new Error("No signed-in user");
  return state.user;
}

/** Whether the signed-in user holds a permission. The server still checks every request. */
export function useCan(permission: string): boolean {
  return useAuth().can(permission);
}

export function homePathFor(user: CurrentUser): string {
  return user.role === "APPLICANT" ? "/portal" : "/office";
}
