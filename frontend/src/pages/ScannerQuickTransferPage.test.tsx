import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ScannerQuickTransferPage } from "./ScannerQuickTransferPage";
import { mockApiClient } from "../test/apiClientMock";
import { renderWithProviders } from "../test/render";

const sourceLocation = {
  location: {
    id: 1,
    branch: 1,
    branch_code: "GDY",
    code: "SRC-01",
    name: "Source",
    location_type: "storage",
  },
  inventory_items: [
    {
      id: 1,
      branch: 1,
      branch_code: "GDY",
      location: 1,
      location_code: "SRC-01",
      location_name: "Source",
      product: 1,
      product_sku: "QT-001",
      product_barcode: "881000000001",
      product_name: "Quick Transfer Product",
      quantity_on_hand: "10.000",
      quantity_reserved: "0.000",
    },
  ],
};

const targetLocation = {
  location: {
    id: 2,
    branch: 1,
    branch_code: "GDY",
    code: "DST-01",
    name: "Target",
    location_type: "picking",
  },
  inventory_items: [],
};

const productLookup = {
  product: {
    id: 1,
    sku: "QT-001",
    barcode: "881000000001",
    name: "Quick Transfer Product",
    description: null,
    image_url: null,
    unit_of_measure: "pcs",
  },
  inventory_positions: sourceLocation.inventory_items,
};

function quickTransferResponse(operationId: string, replayed = false) {
  return {
    message: replayed ? "Quick transfer already completed." : "Quick transfer completed.",
    movement_id: 10,
    reference: `SCANNER-TRANSFER-${operationId}`,
    client_operation_id: operationId,
    replayed,
    product: 1,
    product_sku: "QT-001",
    source_location: 1,
    source_location_code: "SRC-01",
    target_location: 2,
    target_location_code: "DST-01",
    quantity: "2.000",
    quantity_before: "10.000",
    quantity_after: "8.000",
    performed_by: 1,
    performed_by_username: "GDY_WORKER",
    created_at: "2026-07-18T00:00:00Z",
    source_inventory: { ...sourceLocation.inventory_items[0], quantity_on_hand: "8.000" },
    target_inventory: { ...sourceLocation.inventory_items[0], id: 2, location: 2, location_code: "DST-01", quantity_on_hand: "2.000" },
  };
}

function setupLookups() {
  mockApiClient.get.mockImplementation(async (path: string) => {
    if (path === "/scanner/locations/contents/?code=SRC-01") {
      return { data: sourceLocation };
    }
    if (path === "/scanner/products/lookup/?code=QT-001") {
      return { data: productLookup };
    }
    if (path === "/scanner/locations/contents/?code=DST-01") {
      return { data: targetLocation };
    }
    return { data: { count: 0, next: null, previous: null, results: [] } };
  });
}

async function scanTransferPayload(user: ReturnType<typeof userEvent.setup>, quantity = "2") {
  await user.type(screen.getByLabelText("Source location"), "SRC-01{Enter}");
  await screen.findByText("Source: SRC-01");
  await user.type(screen.getByLabelText("Product SKU or barcode"), "QT-001{Enter}");
  await screen.findByText(/Product: QT-001/i);
  await user.type(screen.getByLabelText("Target location"), "DST-01{Enter}");
  await screen.findByText("Target: DST-01");
  await user.clear(screen.getByLabelText("Quantity"));
  await user.type(screen.getByLabelText("Quantity"), quantity);
}

describe("ScannerQuickTransferPage idempotency", () => {
  beforeEach(() => {
    setupLookups();
    let counter = 0;
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: vi.fn(() => {
        counter += 1;
        return `00000000-0000-4000-8000-${String(counter).padStart(12, "0")}`;
      }),
    });
  });

  it("sends client_operation_id and resets with a new id after confirmed success", async () => {
    const user = userEvent.setup();
    mockApiClient.post.mockImplementation(async (_path: string, body: { client_operation_id: string }) => ({
      data: quickTransferResponse(body.client_operation_id),
    }));

    renderWithProviders(<ScannerQuickTransferPage />);
    await scanTransferPayload(user);
    await user.click(screen.getByRole("button", { name: "Confirm transfer" }));
    await screen.findByText("Ready for the next transfer.");

    expect(mockApiClient.post).toHaveBeenCalledTimes(1);
    expect(mockApiClient.post.mock.calls[0][1]).toMatchObject({
      client_operation_id: "00000000-0000-4000-8000-000000000001",
      product_code: "QT-001",
      quantity: "2",
      source_location_code: "SRC-01",
      target_location_code: "DST-01",
    });
    expect(screen.getByLabelText("Source location")).toHaveValue("");

    await scanTransferPayload(user, "1");
    await user.click(screen.getByRole("button", { name: "Confirm transfer" }));

    expect(mockApiClient.post).toHaveBeenCalledTimes(2);
    expect(mockApiClient.post.mock.calls[1][1].client_operation_id).toBe("00000000-0000-4000-8000-000000000002");
  });

  it("reuses the same operation id for manual retry after an uncertain failure", async () => {
    const user = userEvent.setup();
    mockApiClient.post
      .mockRejectedValueOnce(new Error("network timeout"))
      .mockImplementationOnce(async (_path: string, body: { client_operation_id: string }) => ({
        data: quickTransferResponse(body.client_operation_id, true),
      }));

    renderWithProviders(<ScannerQuickTransferPage />);
    await scanTransferPayload(user);
    await user.click(screen.getByRole("button", { name: "Confirm transfer" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Confirm transfer" }));

    expect(mockApiClient.post).toHaveBeenCalledTimes(2);
    expect(mockApiClient.post.mock.calls[0][1].client_operation_id).toBe("00000000-0000-4000-8000-000000000001");
    expect(mockApiClient.post.mock.calls[1][1].client_operation_id).toBe("00000000-0000-4000-8000-000000000001");
    expect(await screen.findByText("Ready for the next transfer.")).toBeInTheDocument();
  });

  it("generates a new operation id when payload changes after failure", async () => {
    const user = userEvent.setup();
    mockApiClient.post
      .mockRejectedValueOnce(new Error("network timeout"))
      .mockImplementationOnce(async (_path: string, body: { client_operation_id: string }) => ({
        data: quickTransferResponse(body.client_operation_id),
      }));

    renderWithProviders(<ScannerQuickTransferPage />);
    await scanTransferPayload(user);
    await user.click(screen.getByRole("button", { name: "Confirm transfer" }));
    await screen.findByRole("alert");
    await user.clear(screen.getByLabelText("Quantity"));
    await user.type(screen.getByLabelText("Quantity"), "3");
    await user.click(screen.getByRole("button", { name: "Confirm transfer" }));

    expect(mockApiClient.post.mock.calls[0][1].client_operation_id).toBe("00000000-0000-4000-8000-000000000001");
    expect(mockApiClient.post.mock.calls[1][1].client_operation_id).toBe("00000000-0000-4000-8000-000000000002");
  });

  it("blocks rapid duplicate submit while pending", async () => {
    const user = userEvent.setup();
    let resolveTransfer: (value: { data: ReturnType<typeof quickTransferResponse> }) => void = () => undefined;
    mockApiClient.post.mockImplementation(
      (_path: string, body: { client_operation_id: string }) =>
        new Promise((resolve) => {
          resolveTransfer = () => resolve({ data: quickTransferResponse(body.client_operation_id) });
        }),
    );

    renderWithProviders(<ScannerQuickTransferPage />);
    await scanTransferPayload(user);
    const button = screen.getByRole("button", { name: "Confirm transfer" });
    await user.click(button);
    await user.click(button);

    expect(mockApiClient.post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Moving stock..." })).toBeDisabled();
    resolveTransfer({ data: quickTransferResponse("00000000-0000-4000-8000-000000000001") });
    await waitFor(() => expect(screen.getByText("Ready for the next transfer.")).toBeInTheDocument());
  });
});
