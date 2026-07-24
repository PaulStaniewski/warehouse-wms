import { describe, expect, it } from "vitest";

import { formatQuantity } from "./quantity";

describe("formatQuantity", () => {
  it("removes insignificant decimal zeroes", () => {
    expect(formatQuantity("1.000")).toBe("1");
    expect(formatQuantity("2.000")).toBe("2");
  });

  it("preserves meaningful fractional precision without unsafe rounding", () => {
    expect(formatQuantity("0.500")).toBe("0.5");
    expect(formatQuantity("1.250")).toBe("1.25");
    expect(formatQuantity("2.375")).toBe("2.375");
  });
});