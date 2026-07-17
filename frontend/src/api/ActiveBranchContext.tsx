import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { ACTIVE_BRANCH_STORAGE_KEY, useAuth } from "./AuthContext";
import { useBranchMemberships } from "./queries";
import type { Branch, BranchMembership } from "../types/api";

type ActiveBranchContextValue = {
  activeBranch: Branch | null;
  activeMembership: BranchMembership | null;
  activeBranchCode: string;
  branches: Branch[];
  memberships: BranchMembership[];
  isLoading: boolean;
  isError: boolean;
  setActiveBranchCode: (code: string) => void;
};

const ActiveBranchContext = createContext<ActiveBranchContextValue | null>(null);

export function ActiveBranchProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const membershipsQuery = useBranchMemberships(auth.isAuthenticated);
  const memberships = membershipsQuery.data ?? [];
  const branches = memberships.map((membership) => ({
    id: membership.branch_id,
    code: membership.branch_code,
    name: membership.branch_name,
    city: membership.branch_city,
    country: membership.branch_country,
    is_active: true,
  }));
  const [activeBranchCode, setActiveBranchCodeState] = useState(() => localStorage.getItem(ACTIVE_BRANCH_STORAGE_KEY) ?? "");

  useEffect(() => {
    if (!auth.isAuthenticated) {
      if (activeBranchCode) {
        setActiveBranchCodeState("");
      }
      localStorage.removeItem(ACTIVE_BRANCH_STORAGE_KEY);
      return;
    }
    if (memberships.length === 0) {
      if (activeBranchCode) {
        setActiveBranchCodeState("");
      }
      return;
    }
    const storedStillAllowed = memberships.some((membership) => membership.branch_code === activeBranchCode);
    const nextCode = storedStillAllowed
      ? activeBranchCode
      : memberships.find((membership) => membership.branch_code === "GDY")?.branch_code ?? memberships[0].branch_code;
    if (nextCode !== activeBranchCode) {
      setActiveBranchCodeState(nextCode);
      localStorage.setItem(ACTIVE_BRANCH_STORAGE_KEY, nextCode);
    }
  }, [activeBranchCode, auth.isAuthenticated, memberships]);

  const value = useMemo<ActiveBranchContextValue>(
    () => ({
      activeBranch: branches.find((branch) => branch.code === activeBranchCode) ?? null,
      activeMembership: memberships.find((membership) => membership.branch_code === activeBranchCode) ?? null,
      activeBranchCode,
      branches,
      memberships,
      isLoading: auth.isAuthenticated && membershipsQuery.isLoading,
      isError: auth.isAuthenticated && membershipsQuery.isError,
      setActiveBranchCode: (code: string) => {
        if (memberships.some((membership) => membership.branch_code === code)) {
          setActiveBranchCodeState(code);
          localStorage.setItem(ACTIVE_BRANCH_STORAGE_KEY, code);
        }
      },
    }),
    [activeBranchCode, auth.isAuthenticated, branches, memberships, membershipsQuery.isError, membershipsQuery.isLoading],
  );

  return <ActiveBranchContext.Provider value={value}>{children}</ActiveBranchContext.Provider>;
}

export function useActiveBranch() {
  const context = useContext(ActiveBranchContext);
  if (!context) {
    throw new Error("useActiveBranch must be used inside ActiveBranchProvider.");
  }
  return context;
}
