import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { useBranches } from "./queries";
import type { Branch } from "../types/api";

type ActiveBranchContextValue = {
  activeBranch: Branch | null;
  activeBranchCode: string;
  branches: Branch[];
  isLoading: boolean;
  setActiveBranchCode: (code: string) => void;
};

const ActiveBranchContext = createContext<ActiveBranchContextValue | null>(null);
const STORAGE_KEY = "warehouse-wms-active-branch";

export function ActiveBranchProvider({ children }: { children: ReactNode }) {
  const branchesQuery = useBranches();
  const branches = branchesQuery.data?.results ?? [];
  const [activeBranchCode, setActiveBranchCodeState] = useState(() => localStorage.getItem(STORAGE_KEY) ?? "");

  useEffect(() => {
    if (branches.length === 0) {
      return;
    }
    const storedStillExists = branches.some((branch) => branch.code === activeBranchCode);
    const nextCode = storedStillExists ? activeBranchCode : branches.find((branch) => branch.code === "GDY")?.code ?? branches[0].code;
    if (nextCode !== activeBranchCode) {
      setActiveBranchCodeState(nextCode);
      localStorage.setItem(STORAGE_KEY, nextCode);
    }
  }, [activeBranchCode, branches]);

  const value = useMemo<ActiveBranchContextValue>(
    () => ({
      activeBranch: branches.find((branch) => branch.code === activeBranchCode) ?? null,
      activeBranchCode,
      branches,
      isLoading: branchesQuery.isLoading,
      setActiveBranchCode: (code: string) => {
        setActiveBranchCodeState(code);
        localStorage.setItem(STORAGE_KEY, code);
      },
    }),
    [activeBranchCode, branches, branchesQuery.isLoading],
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
