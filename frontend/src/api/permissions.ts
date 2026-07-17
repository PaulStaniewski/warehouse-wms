import type { BranchMembership } from "../types/api";

export function canCreateStockAdjustment(membership: BranchMembership | null) {
  return membership?.role === "leader";
}

export function canManageCycleCounts(membership: BranchMembership | null) {
  return membership?.role === "leader";
}
