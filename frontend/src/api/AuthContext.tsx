import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { apiClient } from "./client";
import type { AuthSession } from "../types/api";

export const ACTIVE_BRANCH_STORAGE_KEY = "warehouse-wms-active-branch";

type AuthContextValue = {
  isAuthenticated: boolean;
  isLoading: boolean;
  username: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const session = useQuery({
    queryKey: ["auth", "session"],
    queryFn: async () => {
      const response = await apiClient.get<AuthSession>("/auth/session/");
      return response.data;
    },
    retry: false,
  });

  const loginMutation = useMutation({
    mutationFn: async ({ password, username }: { username: string; password: string }) => {
      const response = await apiClient.post<AuthSession>("/auth/login/", { username, password });
      return response.data;
    },
    onSuccess: async (data) => {
      localStorage.removeItem(ACTIVE_BRANCH_STORAGE_KEY);
      queryClient.clear();
      queryClient.setQueryData(["auth", "session"], data);
      navigate("/wms/dashboard", { replace: true });
    },
  });

  const logoutMutation = useMutation({
    mutationFn: async () => {
      await apiClient.post("/auth/logout/", {});
    },
    onSettled: () => {
      localStorage.removeItem(ACTIVE_BRANCH_STORAGE_KEY);
      queryClient.clear();
      queryClient.setQueryData(["auth", "session"], {
        is_authenticated: false,
        username: null,
        is_superuser: false,
      });
      navigate("/login", { replace: true });
    },
  });

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated: Boolean(session.data?.is_authenticated),
        isLoading: session.isLoading,
        username: session.data?.username ?? null,
        login: async (username, password) => {
          await loginMutation.mutateAsync({ username, password });
        },
        logout: async () => {
          await logoutMutation.mutateAsync();
        },
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider.");
  }
  return context;
}
