import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import App from "../App";
import { mockApiClient } from "../test/apiClientMock";
import { authSession, branchMembership, inventoryExceptionSummary, paginated, transportOverview } from "../test/fixtures";
import { renderWithProviders, setViewport } from "../test/render";
import type { Shipment } from "../types/api";

const baseLine = {
  id: 101,
  shipment: 1,
  order_line: 201,
  line_number: 1,
  product: 301,
  product_sku: "FILTR-001",
  product_name: "Oil filter demo",
  ordered_quantity: "3.000",
  original_ordered_quantity: "3.000",
  effective_quantity: "3.000",
  removed_quantity: "0.000",
  picked_quantity: "0.000",
  controlled_quantity: "0.000",
  prepared_quantity: "0.000",
  shortage_quantity: "0.000",
  maximum_removable_quantity: "3.000",
  can_remove_quantity: true,
  remove_blocked_reason: "",
  cancelled_quantity: "0.000",
  service_status: "not_started",
  source_location_code: "A-01-01",
  source_location_name: "A-01-01",
  delivery_date: "2026-07-20",
  picking_pallet: null,
  external_line_reference: "SHP-GDY-0001-L001",
  quantity_adjustments: [],
  created_at: "2026-07-20T08:00:00Z",
  updated_at: "2026-07-20T08:00:00Z",
};

function shipment(overrides: Partial<Shipment> = {}): Shipment {
  return {
    id: 1,
    reference: "SHP-GDY-0001",
    branch: 1,
    branch_code: "GDY",
    order: 10,
    order_reference: "AX-ORDER-0001",
    route_run: 20,
    route_code: "ROUTE-01",
    route_name: "Gdynia Morning",
    route_time: "10:00:00",
    cutoff_time: "08:00:00",
    route_status: "open",
    inter_branch_transfer: null,
    transfer_reference: null,
    destination_branch_code: null,
    shipment_type: "customer_delivery",
    status: "active",
    picking_status: "not_started",
    control_status: "not_started",
    document_status: "available",
    source_system: "AX",
    external_reference: "AX-SHP-GDY-0001",
    external_order_reference: "AX-ORDER-0001",
    external_status: "imported",
    external_customer_account: "CUST-1",
    external_delivery_reference: "DLV-1",
    external_notes: "Leave at dock 2.",
    customer_name: "Demo Client One",
    customer_alias: "DEMO-ONE",
    recipient_account: "REC-1",
    delivery_name: "Demo Client One",
    delivery_address: "Demo address",
    delivery_date: "2026-07-20",
    payment_method: "Account",
    line_count: 1,
    ordered_quantity: "3.000",
    picked_quantity: "0.000",
    prepared_quantity: "0.000",
    shortage_quantity: "0.000",
    progress_percent: 0,
    activated_at: null,
    activated_by_username: null,
    picking_lists_posted_at: null,
    prepared_at: null,
    prepared_by_username: null,
    cancelled_at: null,
    cancelled_by_username: null,
    cancellation_reason: "",
    documents_printed_at: null,
    documents_printed_by_username: null,
    document_print_count: 0,
    documents_posted_at: null,
    documents_posted_by_username: null,
    picking_route_confirmed_at: null,
    external_created_at: "2026-07-20T08:00:00Z",
    external_updated_at: "2026-07-20T08:30:00Z",
    lines: [baseLine],
    route_assignments: [],
    status_history: [],
    command_eligibility: {
      activate: { enabled: false, reason: "Only pending shipments can be activated." },
      post_picking_lists: { enabled: true, reason: "" },
      prepare: { enabled: false, reason: "Picking and control must be completed first." },
      cancel: { enabled: true, reason: "" },
      post_documents: { enabled: false, reason: "Inter-branch shipment must be prepared and not already document-posted." },
      confirm_picking_route: { enabled: true, reason: "" },
      close_route: { enabled: false, reason: "Route must be ready to close." },
      change_route: { enabled: true, reason: "" },
      change_status: { enabled: true, reason: "" },
      proforma: { enabled: true, reason: "" },
      print_documents: { enabled: true, reason: "" },
    },
    created_at: "2026-07-20T08:00:00Z",
    updated_at: "2026-07-20T08:00:00Z",
    ...overrides,
  };
}

function mockShipmentsApi(rows = [shipment()]) {
  mockApiClient.get.mockImplementation(async (path: string) => {
    if (path === "/auth/session/") return { data: authSession("GDY_LEADER") };
    if (path === "/me/branch-memberships/") return { data: [branchMembership("leader", "GDY")] };
    if (path.startsWith("/inventory-exceptions/summary/")) return { data: inventoryExceptionSummary() };
    if (path.startsWith("/transport-overview/")) return { data: transportOverview() };
    if (path.startsWith("/shipments/route-targets/")) {
      return {
        data: {
          results: [
            {
              id: 99,
              label: "ROUTE-02 / 2026-07-20 / run 1 / 12:00:00",
              operational_identifier: "ROUTE-02",
              branch_code: "GDY",
              route_code: "ROUTE-02",
              route_name: "Gdynia Noon",
              service_date: "2026-07-20",
              weekday: "Monday",
              departure_time: "12:00:00",
              status: "open",
              shipment_count: 1,
            },
            {
              id: 100,
              label: "ROUTE-05 / 2026-07-22 / run 1 / 15:00:00",
              operational_identifier: "ROUTE-05",
              branch_code: "GDY",
              route_code: "ROUTE-05",
              route_name: "Gdynia Week Route",
              service_date: "2026-07-22",
              weekday: "Wednesday",
              departure_time: "15:00:00",
              status: "open",
              shipment_count: 0,
            },
          ],
        },
      };
    }
    if (path.startsWith("/shipments/1/")) return { data: rows[0] };
    if (path.startsWith("/shipments/")) return { data: paginated(rows) };
    return { data: paginated([]) };
  });
}

describe("ShipmentsPage", () => {
  beforeEach(() => {
    setViewport(false);
    mockApiClient.post.mockResolvedValue({ data: { message: "Action completed.", shipment: shipment(), line_id: 101, adjustment_id: 1 } });
  });

  it("renders the list route with compact command panel, table and inline detail", async () => {
    mockShipmentsApi();

    renderWithProviders(<App />, { route: "/wms/shipments" });

    expect((await screen.findAllByRole("heading", { name: "Shipments" })).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("SHP-GDY-0001")).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Post Picking Lists/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Activation/i })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "Shipment Lines" })).toBeInTheDocument();
    expect(screen.getByText("FILTR-001")).toBeInTheDocument();
    expect(screen.getByLabelText("Shipment commands")).toHaveClass("shipment-command-panel");
  });

  it("loads the direct detail route without requiring a query-string selection", async () => {
    mockShipmentsApi();

    renderWithProviders(<App />, { route: "/wms/shipments/1" });

    expect((await screen.findAllByRole("heading", { name: "Shipments" })).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("SHP-GDY-0001")).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Shipment Summary" })).toBeInTheDocument();
    await waitFor(() =>
      expect(mockApiClient.get).toHaveBeenCalledWith(expect.stringMatching(/^\/shipments\/1\/\?branch=GDY$/)),
    );
  });

  it("shows a friendly state for a missing direct shipment detail", async () => {
    mockApiClient.get.mockImplementation(async (path: string) => {
      if (path === "/auth/session/") return { data: authSession("GDY_LEADER") };
      if (path === "/me/branch-memberships/") return { data: [branchMembership("leader", "GDY")] };
      if (path.startsWith("/inventory-exceptions/summary/")) return { data: inventoryExceptionSummary() };
      if (path.startsWith("/transport-overview/")) return { data: transportOverview() };
      if (path.startsWith("/shipments/404/")) {
        const error = new Error("Not found") as Error & { response: { status: number } };
        error.response = { status: 404 };
        throw error;
      }
      if (path.startsWith("/shipments/")) return { data: paginated([]) };
      return { data: paginated([]) };
    });

    renderWithProviders(<App />, { route: "/wms/shipments/404" });

    expect(await screen.findByText("Shipment was not found or is not available for this branch.")).toBeInTheDocument();
  });

  it("requests weekly route targets when Today only is disabled", async () => {
    const user = userEvent.setup();
    mockShipmentsApi();

    renderWithProviders(<App />, { route: "/wms/shipments" });

    await user.click(await screen.findByRole("button", { name: /Change Route/i }));
    expect(screen.getByText(/ROUTE-02 \/ Monday 2026-07-20/i)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Today only"));
    await user.type(screen.getByLabelText("Route search"), "ROUTE-05");

    await waitFor(() =>
      expect(mockApiClient.get).toHaveBeenCalledWith(expect.stringContaining("scope=week")),
    );
    await waitFor(() =>
      expect(mockApiClient.get).toHaveBeenCalledWith(expect.stringContaining("search=ROUTE-05")),
    );
  });

  it("opens action dialogs and prevents duplicate mutation while pending", async () => {
    const user = userEvent.setup();
    mockShipmentsApi();
    mockApiClient.post.mockImplementation(() => new Promise(() => undefined));

    renderWithProviders(<App />, { route: "/wms/shipments" });

    await user.click(await screen.findByRole("button", { name: /Change Route/i }));
    expect(screen.getByRole("heading", { name: /Change route for SHP-GDY-0001/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Today only")).toBeChecked();
    expect(screen.queryByLabelText("Reason")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Target route"), "99");
    await user.click(screen.getByRole("button", { name: "Change Route" }));

    expect(screen.getByRole("button", { name: "Working..." })).toBeDisabled();
    expect(mockApiClient.post).toHaveBeenCalledTimes(1);
    expect(mockApiClient.post).toHaveBeenCalledWith("/shipments/1/change-route/", expect.not.objectContaining({ reason: expect.anything() }));
  });

  it("selects shipment lines and removes unpicked quantity through the dialog", async () => {
    const user = userEvent.setup();
    mockShipmentsApi();

    renderWithProviders(<App />, { route: "/wms/shipments" });

    await user.click(await screen.findByText("FILTR-001"));
    expect(screen.getByText(/Selected line 1/i)).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Remove Quantity" })[0]);
    expect(screen.getByText("Max removable")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Quantity to remove"));
    await user.type(screen.getByLabelText("Quantity to remove"), "1");
    await user.type(screen.getByLabelText("Reason"), "Customer requested fewer units");
    expect(screen.getByText(/does not create a return, stock movement, or sales correction/i)).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Remove Quantity" })[1]);

    await waitFor(() => expect(mockApiClient.post).toHaveBeenCalledWith(
      "/shipments/1/lines/101/remove-quantity/",
      expect.objectContaining({ quantity: "1", reason: "Customer requested fewer units" }),
    ));
  });

  it("supports exact maximum removal and shows zero-effective line history", async () => {
    const user = userEvent.setup();
    mockShipmentsApi([
      shipment({
        lines: [
          {
            ...baseLine,
            effective_quantity: "0.000",
            removed_quantity: "3.000",
            maximum_removable_quantity: "0.000",
            can_remove_quantity: false,
            remove_blocked_reason: "No unpicked quantity remains removable.",
            service_status: "cancelled",
            quantity_adjustments: [
              {
                id: 1,
                shipment: 1,
                shipment_line: 101,
                quantity_removed: "3.000",
                previous_effective_quantity: "3.000",
                new_effective_quantity: "0.000",
                adjusted_by: 7,
                adjusted_by_username: "GDY_LEADER",
                reason: "Customer removed remaining units.",
                created_at: "2026-07-20T09:00:00Z",
              },
            ],
          },
        ],
      }),
    ]);

    renderWithProviders(<App />, { route: "/wms/shipments" });

    await user.click(await screen.findByText("FILTR-001"));
    expect(screen.getAllByText("0.000").length).toBeGreaterThan(0);
    expect(screen.getByText("No unpicked quantity remains removable.")).toBeInTheDocument();
    expect(screen.getByText("Removal history")).toBeInTheDocument();
    expect(screen.getByText(/3.000 removed by GDY_LEADER/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Quantity" })).toBeDisabled();
  });

  it("shows readable backend errors inside action dialogs", async () => {
    const user = userEvent.setup();
    mockShipmentsApi();
    mockApiClient.post.mockRejectedValue({ response: { data: { detail: "Target route is not eligible." } } });

    renderWithProviders(<App />, { route: "/wms/shipments" });

    await user.click(await screen.findByRole("button", { name: /Change Route/i }));
    await user.selectOptions(screen.getByLabelText("Target route"), "99");
    await user.click(screen.getByRole("button", { name: "Change Route" }));

    expect(await screen.findByText("Target route is not eligible.")).toBeInTheDocument();
  });

  it("preserves filter state in URL params", async () => {
    const user = userEvent.setup();
    mockShipmentsApi();

    renderWithProviders(<App />, { route: "/wms/shipments" });

    await user.type(await screen.findByLabelText("Customer"), "Demo");
    expect(await screen.findByDisplayValue("Demo")).toBeInTheDocument();
  });
});
