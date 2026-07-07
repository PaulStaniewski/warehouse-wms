import { useMutation, useQuery } from "@tanstack/react-query";

import { apiClient, getHealth, getList } from "./client";
import type {
  Branch,
  AuditLog,
  InventoryItem,
  Location,
  Order,
  OrderLine,
  PickingTask,
  Product,
  ReturnBatch,
  RouteRun,
  ScannerContentsResponse,
  ScannerLocationContentsResponse,
  ScannerPickingScanResponse,
  ScannerProductLookupResponse,
  ScannerQuickTransferResponse,
  ScannerReceivingResponse,
  ScannerCartItemsResponse,
  ScannerCartWorkResponse,
  ScannerCreateJobsResponse,
  ScannerJobsResponse,
  ScannerControlCartResponse,
  ScannerControlTargetResponse,
  ScannerProformasResponse,
  ScannerPrintLabelResponse,
  ScannerSessionResponse,
  ScannerTaskStartResponse,
  TransferDiscrepancy,
} from "../types/api";


export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });
}

export function useProducts() {
  return useQuery({
    queryKey: ["products"],
    queryFn: () => getList<Product>("/products/"),
  });
}

export function useInventoryItems() {
  return useQuery({
    queryKey: ["inventory-items"],
    queryFn: () => getList<InventoryItem>("/inventory-items/"),
  });
}

export function useOrders() {
  return useQuery({
    queryKey: ["orders"],
    queryFn: () => getList<Order>("/orders/"),
  });
}

export function useLocations() {
  return useQuery({
    queryKey: ["locations"],
    queryFn: () => getList<Location>("/locations/"),
  });
}

export function useBranches() {
  return useQuery({
    queryKey: ["branches"],
    queryFn: () => getList<Branch>("/branches/"),
  });
}

export function usePickingTasks(routeRunId?: string) {
  return useQuery({
    queryKey: ["picking-tasks", routeRunId ?? "all"],
    queryFn: () => getList<PickingTask>(routeRunId ? `/picking-tasks/?route_run=${routeRunId}` : "/picking-tasks/"),
  });
}

export function useReturnBatches() {
  return useQuery({
    queryKey: ["return-batches"],
    queryFn: () => getList<ReturnBatch>("/return-batches/"),
  });
}

export function useRouteRuns(branchId?: number) {
  return useQuery({
    queryKey: ["route-runs", branchId ?? "all"],
    queryFn: () => getList<RouteRun>(branchId ? `/route-runs/?branch=${branchId}` : "/route-runs/"),
  });
}

export function useRouteRun(routeRunId?: string) {
  return useQuery({
    enabled: Boolean(routeRunId),
    queryKey: ["route-run", routeRunId],
    queryFn: async () => {
      const response = await apiClient.get<RouteRun>(`/route-runs/${routeRunId}/`);
      return response.data;
    },
  });
}

export function useRouteRunArchive() {
  return useQuery({
    queryKey: ["route-runs", "archive"],
    queryFn: () => getList<RouteRun>("/route-runs/archive/"),
  });
}

export function usePrintRouteDocuments() {
  return useMutation({
    mutationFn: async ({ routeRunId }: { routeRunId: number }) => {
      const response = await apiClient.post<{ message: string; route_run: RouteRun }>(
        `/route-runs/${routeRunId}/print-documents/`,
      );
      return response.data;
    },
  });
}

export function useCloseRouteRun() {
  return useMutation({
    mutationFn: async ({ routeRunId }: { routeRunId: number }) => {
      const response = await apiClient.post<{ message: string; route_run: RouteRun }>(
        `/route-runs/${routeRunId}/close/`,
      );
      return response.data;
    },
  });
}

export function useOrderLines(routeRunId?: string) {
  return useQuery({
    enabled: Boolean(routeRunId),
    queryKey: ["order-lines", routeRunId],
    queryFn: () => getList<OrderLine>(`/order-lines/?route_run=${routeRunId}`),
  });
}

export function useCompletePickingTask() {
  return useMutation({
    mutationFn: async ({
      locationCode,
      productCode,
      taskId,
    }: {
      locationCode: string;
      productCode: string;
      taskId: number;
    }) => {
      const response = await apiClient.post<{ message: string; task: PickingTask }>(
        `/picking-tasks/${taskId}/complete/`,
        {
          location_code: locationCode,
          product_code: productCode,
        },
      );
      return response.data;
    },
  });
}

export function useScannerPickingScan() {
  return useMutation({
    mutationFn: async ({ code, routeRunId }: { code: string; routeRunId: number }) => {
      const response = await apiClient.post<ScannerPickingScanResponse>("/scanner/picking/scan/", {
        code,
        route_run_id: routeRunId,
      });
      return response.data;
    },
  });
}

export function useScannerPickingPick() {
  return useMutation({
    mutationFn: async ({
      cartWorkSessionId,
      code,
      quantity,
      routeRunId,
      sessionId,
      workerCode,
    }: {
      cartWorkSessionId?: number;
      code: string;
      quantity: string;
      routeRunId?: number;
      sessionId?: number;
      workerCode?: string;
    }) => {
      const response = await apiClient.post<ScannerPickingScanResponse>("/scanner/picking/pick/", {
        code,
        product_code: code,
        quantity,
        route_run_id: routeRunId,
        cart_work_session_id: cartWorkSessionId,
        session_id: sessionId,
        worker_code: workerCode,
      });
      return response.data;
    },
  });
}

export function useScannerConfirmLocation() {
  return useMutation({
    mutationFn: async ({
      cartWorkSessionId,
      locationCode,
    }: {
      cartWorkSessionId: number;
      locationCode: string;
    }) => {
      const response = await apiClient.post<ScannerCartWorkResponse>("/scanner/picking/confirm-location/", {
        cart_work_session_id: cartWorkSessionId,
        location_code: locationCode,
      });
      return response.data;
    },
  });
}

export function useScannerPickingPrepare() {
  return useMutation({
    mutationFn: async ({
      code,
      productCode,
      quantity,
      routeRunId,
      sessionId,
    }: {
      code: string;
      productCode: string;
      quantity: string;
      routeRunId: number;
      sessionId: number;
    }) => {
      const response = await apiClient.post<ScannerPickingScanResponse>("/scanner/picking/prepare/", {
        order_reference: code,
        product_code: productCode,
        quantity,
        route_run_id: routeRunId,
        session_id: sessionId,
      });
      return response.data;
    },
  });
}

export function useScannerSessionStart() {
  return useMutation({
    mutationFn: async ({ cartCode, workerCode }: { cartCode: string; workerCode: string }) => {
      const response = await apiClient.post<ScannerSessionResponse>("/scanner/session/start/", {
        cart_code: cartCode,
        worker_code: workerCode,
      });
      return response.data;
    },
  });
}

export function useScannerProformas(branchId?: number) {
  return useQuery({
    queryKey: ["scanner-proformas", branchId ?? "all"],
    queryFn: async () => {
      const response = await apiClient.get<ScannerProformasResponse>(
        branchId ? `/scanner/proformas/?branch=${branchId}` : "/scanner/proformas/",
      );
      return response.data;
    },
  });
}

export function useScannerCreateJobs() {
  return useMutation({
    mutationFn: async ({
      mode,
      routeRunIds,
      workerCode,
    }: {
      mode: "merged" | "separate";
      routeRunIds: number[];
      workerCode: string;
    }) => {
      const response = await apiClient.post<ScannerCreateJobsResponse>("/scanner/proformas/create-jobs/", {
        mode,
        route_run_ids: routeRunIds,
        worker_code: workerCode,
      });
      return response.data;
    },
  });
}

export function useScannerJobs() {
  return useQuery({
    queryKey: ["scanner-jobs"],
    queryFn: async () => {
      const response = await apiClient.get<ScannerJobsResponse>("/scanner/tasks/");
      return response.data;
    },
  });
}

export function useScannerTaskStart() {
  return useMutation({
    mutationFn: async ({ cartCode, jobId, workerCode }: { cartCode: string; jobId: number; workerCode: string }) => {
      const response = await apiClient.post<ScannerTaskStartResponse>(`/scanner/tasks/${jobId}/start/`, {
        cart_code: cartCode,
        worker_code: workerCode,
      });
      return response.data;
    },
  });
}

export function useScannerCartWork(sessionId?: number, cartWorkSessionId?: number | null) {
  return useQuery({
    enabled: Boolean(sessionId || cartWorkSessionId),
    refetchInterval: 4000,
    queryKey: ["scanner-cart-work", sessionId ?? "no-session", cartWorkSessionId ?? "no-work"],
    queryFn: async () => {
      const query = cartWorkSessionId ? `cart_work_session_id=${cartWorkSessionId}` : `session_id=${sessionId}`;
      const response = await apiClient.get<ScannerCartWorkResponse>(`/scanner/cart-work/current/?${query}`);
      return response.data;
    },
  });
}

export function useScannerControlCart(cartCode: string) {
  return useQuery({
    enabled: Boolean(cartCode),
    refetchInterval: 4000,
    queryKey: ["scanner-control-cart", cartCode],
    queryFn: async () => {
      const response = await apiClient.get<ScannerControlCartResponse>(
        `/scanner/control/cart/?cart_code=${encodeURIComponent(cartCode)}`,
      );
      return response.data;
    },
  });
}

export function useScannerSessionEnd() {
  return useMutation({
    mutationFn: async ({ sessionId }: { sessionId: number }) => {
      const response = await apiClient.post<ScannerSessionResponse>("/scanner/session/end/", {
        session_id: sessionId,
      });
      return response.data;
    },
  });
}

export function useScannerCartItems(sessionId?: number) {
  return useQuery({
    enabled: Boolean(sessionId),
    queryKey: ["scanner-control-cart-items", sessionId],
    queryFn: async () => {
      const response = await apiClient.get<ScannerCartItemsResponse>(
        `/scanner/control/cart-items/?session_id=${sessionId}`,
      );
      return response.data;
    },
  });
}

export function useScannerControlTarget(sessionId?: number, productCode?: string) {
  return useQuery({
    enabled: Boolean(sessionId && productCode),
    queryKey: ["scanner-control-target", sessionId, productCode],
    queryFn: async () => {
      const response = await apiClient.get<ScannerControlTargetResponse>(
        `/scanner/control/target/?session_id=${sessionId}&product_code=${encodeURIComponent(productCode ?? "")}`,
      );
      return response.data;
    },
  });
}

export function useScannerPrintLabel() {
  return useMutation({
    mutationFn: async ({
      orderReference,
      printerCode,
      sessionId,
    }: {
      orderReference: string;
      printerCode: string;
      sessionId: number;
    }) => {
      const response = await apiClient.post<ScannerPrintLabelResponse>("/scanner/control/print-label/", {
        order_reference: orderReference,
        printer_code: printerCode,
        session_id: sessionId,
      });
      return response.data;
    },
  });
}

export function useScannerControlFinish() {
  return useMutation({
    mutationFn: async ({ sessionId }: { sessionId: number }) => {
      const response = await apiClient.post<ScannerSessionResponse>("/scanner/control/finish/", {
        session_id: sessionId,
      });
      return response.data;
    },
  });
}

export function useScannerProductLookup(code: string) {
  return useQuery({
    enabled: Boolean(code),
    queryKey: ["scanner-product-lookup", code],
    queryFn: async () => {
      const response = await apiClient.get<ScannerProductLookupResponse>(
        `/scanner/products/lookup/?code=${encodeURIComponent(code)}`,
      );
      return response.data;
    },
  });
}

export function useScannerLocationContents(code: string) {
  return useQuery({
    enabled: Boolean(code),
    queryKey: ["scanner-location-contents", code],
    queryFn: async () => {
      const response = await apiClient.get<ScannerLocationContentsResponse>(
        `/scanner/locations/contents/?code=${encodeURIComponent(code)}`,
      );
      return response.data;
    },
  });
}

export function useScannerContents(code: string) {
  return useQuery({
    enabled: Boolean(code),
    queryKey: ["scanner-contents", code],
    queryFn: async () => {
      const response = await apiClient.get<ScannerContentsResponse>(
        `/scanner/contents/?code=${encodeURIComponent(code)}`,
      );
      return response.data;
    },
  });
}

export function useScannerQuickTransfer() {
  return useMutation({
    mutationFn: async ({
      productCode,
      quantity,
      sourceLocationCode,
      targetLocationCode,
    }: {
      productCode: string;
      quantity: string;
      sourceLocationCode: string;
      targetLocationCode: string;
    }) => {
      const response = await apiClient.post<ScannerQuickTransferResponse>("/scanner/quick-transfer/", {
        product_code: productCode,
        quantity,
        source_location_code: sourceLocationCode,
        target_location_code: targetLocationCode,
      });
      return response.data;
    },
  });
}

export function useScannerReceivingCurrent(receivingSessionId?: number | null) {
  return useQuery({
    enabled: Boolean(receivingSessionId),
    refetchInterval: 4000,
    queryKey: ["scanner-receiving-current", receivingSessionId ?? "none"],
    queryFn: async () => {
      const response = await apiClient.get<ScannerReceivingResponse>(
        `/scanner/receiving/current/?receiving_session_id=${receivingSessionId}`,
      );
      return response.data;
    },
  });
}

export function useScannerReceivingStart() {
  return useMutation({
    mutationFn: async ({ palletCode, workerCode }: { palletCode: string; workerCode: string }) => {
      const response = await apiClient.post<ScannerReceivingResponse>("/scanner/receiving/start/", {
        pallet_code: palletCode,
        worker_code: workerCode,
      });
      return response.data;
    },
  });
}

export function useScannerReceivingScanProduct() {
  return useMutation({
    mutationFn: async ({
      productCode,
      quantity,
      receivingSessionId,
    }: {
      productCode: string;
      quantity: string;
      receivingSessionId: number;
    }) => {
      const response = await apiClient.post<ScannerReceivingResponse>("/scanner/receiving/scan-product/", {
        product_code: productCode,
        quantity,
        receiving_session_id: receivingSessionId,
      });
      return response.data;
    },
  });
}

export function useScannerReceivingPutAway() {
  return useMutation({
    mutationFn: async ({
      locationCode,
      receivingSessionId,
    }: {
      locationCode: string;
      receivingSessionId: number;
    }) => {
      const response = await apiClient.post<ScannerReceivingResponse>("/scanner/receiving/put-away/", {
        location_code: locationCode,
        receiving_session_id: receivingSessionId,
      });
      return response.data;
    },
  });
}

export function useScannerReceivingComplete() {
  return useMutation({
    mutationFn: async ({ receivingSessionId }: { receivingSessionId: number }) => {
      const response = await apiClient.post<ScannerReceivingResponse>("/scanner/receiving/complete/", {
        receiving_session_id: receivingSessionId,
      });
      return response.data;
    },
  });
}

export function useScannerReceivingClose() {
  return useMutation({
    mutationFn: async ({ receivingSessionId }: { receivingSessionId: number }) => {
      const response = await apiClient.post<ScannerReceivingResponse>("/scanner/receiving/close/", {
        receiving_session_id: receivingSessionId,
      });
      return response.data;
    },
  });
}

export function useTransferDiscrepancies() {
  return useQuery({
    queryKey: ["transfer-discrepancies"],
    queryFn: () => getList<TransferDiscrepancy>("/transfer-discrepancies/"),
  });
}

export function useTransferDiscrepancy(id?: string) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["transfer-discrepancy", id],
    queryFn: async () => {
      const response = await apiClient.get<TransferDiscrepancy>(`/transfer-discrepancies/${id}/`);
      return response.data;
    },
  });
}

export function useCurrentAuditLogs() {
  return useQuery({
    queryKey: ["audit-logs", "current"],
    queryFn: () => getList<AuditLog>("/audit-logs/current/"),
  });
}

export function useArchiveAuditLogs(dateFrom: string, dateTo: string) {
  return useQuery({
    enabled: Boolean(dateFrom && dateTo),
    queryKey: ["audit-logs", "archive", dateFrom, dateTo],
    queryFn: () => getList<AuditLog>(`/audit-logs/archive/?date_from=${dateFrom}&date_to=${dateTo}`),
  });
}
