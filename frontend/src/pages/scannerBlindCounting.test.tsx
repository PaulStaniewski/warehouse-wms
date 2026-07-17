import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ScannerCycleCountDetailPage } from "./ScannerCycleCountDetailPage";
import { ScannerCycleCountRecountDetailPage } from "./ScannerCycleCountRecountDetailPage";
import { mockApiClient } from "../test/apiClientMock";
import { renderWithProviders } from "../test/render";

describe("scanner blind counting", () => {
  it("does not expose expected quantity or variance during normal cycle count entry", async () => {
    const user = userEvent.setup();
    mockApiClient.get.mockImplementation(async (path: string) => {
      if (path === "/scanner/cycle-counts/10/") {
        return {
          data: {
            locations: [
              {
                counted_lines_count: 0,
                expected_lines_count: 7,
                id: 100,
                is_secret_marker: "EXPECTED-999",
                lines: [],
                location: 1,
                location_code: "A-01-01",
                location_name: "Storage",
                status: "open",
                uncounted_expected_count: 7,
                variance_secret: "VARIANCE-123",
              },
            ],
            session: {
              branch: 1,
              branch_code: "GDY",
              created_at: null,
              id: 10,
              locations_count: 1,
              name: "Blind count",
              opened_at: null,
              reference: "CC-10",
              snapshot_at: null,
              status: "open",
              submitted_locations_count: 0,
            },
          },
        };
      }
      return { data: {} };
    });

    renderWithProviders(
      <Routes>
        <Route path="/scanner/cycle-counts/:id" element={<ScannerCycleCountDetailPage />} />
      </Routes>,
      { route: "/scanner/cycle-counts/10" },
    );

    expect(await screen.findByRole("heading", { name: "Blind location count" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /A-01-01/i }));

    expect(screen.getByText("Product SKU or barcode")).toBeInTheDocument();
    expect(screen.queryByText("EXPECTED-999")).not.toBeInTheDocument();
    expect(screen.queryByText("VARIANCE-123")).not.toBeInTheDocument();
    expect(screen.queryByText(/expected quantity/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/variance/i)).not.toBeInTheDocument();
  });

  it("does not expose original or baseline quantities during recount entry", async () => {
    mockApiClient.get.mockImplementation(async (path: string) => {
      if (path === "/scanner/cycle-count-recounts/5/") {
        return {
          data: {
            baseline_quantity: "BASELINE-555",
            branch: 1,
            branch_code: "GDY",
            counted_quantity: null,
            id: 5,
            is_executable: true,
            location: 1,
            location_code: "A-01-01",
            location_name: "Storage",
            original_counted_quantity: "ORIGINAL-111",
            original_expected_quantity: "EXPECTED-222",
            original_variance: "VARIANCE-333",
            product: 1,
            product_name: "Filtr oleju demo",
            product_sku: "FILTR-001",
            reason: "Leader requested recount",
            reference: "REC-5",
            requested_at: null,
            requested_by_username: "GDY_LEADER",
            session: 10,
            session_reference: "CC-10",
            started_at: null,
            status: "requested",
            status_label: "Requested",
          },
        };
      }
      return { data: {} };
    });

    renderWithProviders(
      <Routes>
        <Route path="/scanner/cycle-count-recounts/:id" element={<ScannerCycleCountRecountDetailPage />} />
      </Routes>,
      { route: "/scanner/cycle-count-recounts/5" },
    );

    expect(await screen.findByRole("heading", { name: /A-01-01 \/ FILTR-001/i })).toBeInTheDocument();
    expect(screen.getByText("Scan location")).toBeInTheDocument();
    expect(screen.getByText("Scan product barcode or SKU")).toBeInTheDocument();
    expect(screen.queryByText("BASELINE-555")).not.toBeInTheDocument();
    expect(screen.queryByText("ORIGINAL-111")).not.toBeInTheDocument();
    expect(screen.queryByText("EXPECTED-222")).not.toBeInTheDocument();
    expect(screen.queryByText("VARIANCE-333")).not.toBeInTheDocument();
  });
});
