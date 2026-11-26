import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api, onSessionChange, refreshSession, setSession } from "@/api/client";
import type { TokenResponse } from "@/api/types";

import { AuthContext, type AuthContextValue, type AuthState } from "./useAuth";

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<AuthState>({ status: "loading", user: null });
  const signingOut = useRef(false);
  const signedIn = useRef(false);

  useEffect(() => {
    const unsubscribe = onSessionChange((user) => {
      if (user) {
        signedIn.current = true;
        setState({ status: "authenticated", user });
        return;
      }
      const reason = signingOut.current ? "signed-out" : signedIn.current ? "expired" : undefined;
      signingOut.current = false;
      signedIn.current = false;
      setState({ status: "anonymous", user: null, reason });
      queryClient.clear();
    });
    // Restore the session from the HttpOnly refresh cookie, if there is one.
    refreshSession().catch(() => setState({ status: "anonymous", user: null }));
    return unsubscribe;
  }, [queryClient]);

  const login = useCallback(
    async (email: string, password: string) => {
      const token = await api<TokenResponse>("/auth/login", {
        method: "POST",
        body: { email, password },
        auth: false,
      });
      queryClient.clear();
      setSession(token);
      return token.user;
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    signingOut.current = true;
    try {
      await api<void>("/auth/logout", { method: "POST", auth: false });
    } finally {
      setSession(null);
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      state,
      login,
      logout,
      can: (permission: string) => state.user?.permissions.includes(permission) ?? false,
    }),
    [state, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
