import { describe, expect, it } from "vitest";

import { canCreateStockAdjustment, canManageCycleCounts } from "./permissions";
import { branchMembership } from "../test/fixtures";

describe("frontend permission helpers", () => {
  it("allows leader-only stock adjustments and Cycle Count management", () => {
    const leader = branchMembership("leader");

    expect(canCreateStockAdjustment(leader)).toBe(true);
    expect(canManageCycleCounts(leader)).toBe(true);
  });

  it("does not grant leader actions to workers or missing membership", () => {
    const worker = branchMembership("worker");

    expect(canCreateStockAdjustment(worker)).toBe(false);
    expect(canManageCycleCounts(worker)).toBe(false);
    expect(canCreateStockAdjustment(null)).toBe(false);
    expect(canManageCycleCounts(null)).toBe(false);
  });
});
