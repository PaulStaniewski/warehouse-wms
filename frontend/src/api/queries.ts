import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import axios from "axios";

import { apiClient, getHealth, getList } from "./client";
import type {
  Branch,
  BranchMembership,
  AuditLog,
  InventoryItem,
  InterBranchArrivalResponse,
  InterBranchMMTask,
  Location,
  Order,
  OrderLine,
  PickingTask,
  PickingShortage,
  PickingShortageChallenge,
  Product,
  ReturnBatch,
  ReplenishmentRequest,
  RouteRun,
  ScannerContentsResponse,
  ScannerLocationContentsResponse,
  ScannerPickingScanResponse,
  ScannerPickingShortageResponse,
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
  TransferDiscrepancyAction,
  TransferDiscrepancyConfirmShortageResponse,
  TransferDiscrepancyPrintResponse,
  TransferDiscrepancyRecoverResponse,
  TransferDiscrepancyReconciliation,
  TransferDiscrepancyReconciliationResponse,
  TransferDiscrepancySourceStockRecoveryResponse,
  TransferDiscrepancySourceStockVerification,
  TransferDiscrepancySourceStockVerificationResponse,
  TransferDiscrepancySourceReview,
  TransferDiscrepancySourceReviewResponse,
  TransferDiscrepancyTransitInvestigation,
  TransferDiscrepancyTransitInvestigationResponse,
} from "../types/api";

type DashboardCountParams = {
  branch?: string;
  branchParam?: string;
  endpoint: string;
  key: string;
  statuses?: string[];
};

function buildCountPath(endpoint: string, branch?: string, status?: string, branchParam = "branch") {
  const params = new URLSearchParams();
  if (branch) params.set(branchParam, branch);
  if (status) params.set("status", status);
  const query = params.toString();
  return `${endpoint}${query ? `?${query}` : ""}`;
}

export function useDashboardResourceCount({ branch, branchParam = "branch", endpoint, key, statuses = [] }: DashboardCountParams) {
  const queryStatuses = statuses.length > 0 ? statuses : [""];
  const queries = useQueries({
    queries: queryStatuses.map((status) => ({
      enabled: Boolean(branch),
      queryKey: ["dashboard-count", key, branch, branchParam, status || "all"],
      queryFn: async () => {
        const response = await getList<unknown>(buildCountPath(endpoint, branch, status || undefined, branchParam));
        return response.count;
      },
    })),
  });

  return {
    count: queries.reduce((total, query) => total + (query.data ?? 0), 0),
    error: queries.find((query) => query.error)?.error ?? null,
    isError: queries.some((query) => query.isError),
    isLoading: queries.some((query) => query.isLoading),
    isSuccess: queries.every((query) => query.isSuccess),
    refetch: () => Promise.all(queries.map((query) => query.refetch())),
  };
}


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

export function useInventoryItems(branch?: string) {
  return useQuery({
    queryKey: ["inventory-items", branch],
    queryFn: () => getList<InventoryItem>(`/inventory-items/${branch ? `?branch=${branch}` : ""}`),
  });
}

export function useOrders(branch?: string) {
  return useQuery({
    queryKey: ["orders", branch],
    queryFn: () => getList<Order>(`/orders/${branch ? `?branch=${branch}` : ""}`),
  });
}

export function useLocations(branch?: string) {
  return useQuery({
    queryKey: ["locations", branch],
    queryFn: () => getList<Location>(`/locations/${branch ? `?branch=${branch}` : ""}`),
  });
}

export function useBranches() {
  return useQuery({
    queryKey: ["branches"],
    queryFn: () => getList<Branch>("/branches/"),
  });
}

export function useBranchMemberships(enabled = true) {
  return useQuery({
    enabled,
    queryKey: ["me", "branch-memberships"],
    queryFn: async () => {
      const response = await apiClient.get<BranchMembership[]>("/me/branch-memberships/");
      return response.data;
    },
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

export function useRouteRuns(branch?: number | string) {
  return useQuery({
    queryKey: ["route-runs", branch ?? "all"],
    queryFn: () => {
      if (!branch) {
        return getList<RouteRun>("/route-runs/");
      }
      const param = typeof branch === "number" ? `branch=${branch}` : `branch_code=${branch}`;
      return getList<RouteRun>(`/route-runs/?${param}`);
    },
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

export function useRouteRunArchive(branchCode?: string) {
  return useQuery({
    queryKey: ["route-runs", "archive", branchCode],
    queryFn: () => getList<RouteRun>(`/route-runs/archive/${branchCode ? `?branch_code=${branchCode}` : ""}`),
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

export function useScannerPickingShortageChallenge() {
  return useMutation({
    mutationFn: async ({
      cartWorkSessionId,
      quantity,
      workerCode,
    }: {
      cartWorkSessionId: number;
      quantity: string;
      workerCode?: string;
    }) => {
      const response = await apiClient.post<PickingShortageChallenge>("/scanner/picking/shortage-challenge/", {
        cart_work_session_id: cartWorkSessionId,
        quantity,
        worker_code: workerCode,
      });
      return response.data;
    },
  });
}

export function useScannerPickingReportShortage() {
  return useMutation({
    mutationFn: async ({
      challengeToken,
      confirmationCode,
      clientOperationId,
      note,
    }: {
      challengeToken: string;
      confirmationCode: string;
      clientOperationId: string;
      note?: string;
    }) => {
      const response = await apiClient.post<ScannerPickingShortageResponse>("/scanner/picking/report-shortage/", {
        challenge_token: challengeToken,
        confirmation_code: confirmationCode,
        client_operation_id: clientOperationId,
        note,
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
    enabled: Boolean(branchId),
    queryKey: ["scanner-proformas", branchId ?? "all"],
    queryFn: async () => {
      const response = await apiClient.get<ScannerProformasResponse>(
        `/scanner/proformas/?branch=${branchId}`,
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
    }: {
      mode: "merged" | "separate";
      routeRunIds: number[];
    }) => {
      const response = await apiClient.post<ScannerCreateJobsResponse>("/scanner/proformas/create-jobs/", {
        mode,
        route_run_ids: routeRunIds,
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
    mutationFn: async ({ cartCode, jobId }: { cartCode: string; jobId: number }) => {
      const response = await apiClient.post<ScannerTaskStartResponse>(`/scanner/tasks/${jobId}/start/`, {
        cart_code: cartCode,
      });
      return response.data;
    },
  });
}

export function useScannerCartWorkJoin() {
  return useMutation({
    mutationFn: async ({ cartBarcode }: { cartBarcode: string }) => {
      const response = await apiClient.post<ScannerCartWorkResponse>("/scanner/cart-work/join/", {
        cart_barcode: cartBarcode,
      });
      return response.data;
    },
  });
}

export function useScannerCartWorkClaim() {
  return useMutation({
    mutationFn: async ({
      cartWorkSessionId,
      direction,
      mode,
      pickingTaskId,
    }: {
      cartWorkSessionId: number;
      direction?: "beginning" | "end";
      mode?: "beginning" | "end" | "specific";
      pickingTaskId?: number;
    }) => {
      const response = await apiClient.post<ScannerCartWorkResponse>("/scanner/cart-work/claim/", {
        cart_work_session_id: cartWorkSessionId,
        direction,
        mode,
        picking_task_id: pickingTaskId,
      });
      return response.data;
    },
  });
}

export function useScannerCartWorkLeave() {
  return useMutation({
    mutationFn: async ({ cartWorkSessionId }: { cartWorkSessionId: number }) => {
      const response = await apiClient.post<{ message: string }>("/scanner/cart-work/leave/", {
        cart_work_session_id: cartWorkSessionId,
      });
      return response.data;
    },
  });
}

export function useScannerCartWork(
  sessionId?: number,
  cartWorkSessionId?: number | null,
  options: { onStaleSession?: () => void } = {},
) {
  return useQuery({
    enabled: Boolean(sessionId || cartWorkSessionId),
    refetchInterval: (query) => {
      const error = query.state.error;
      if (axios.isAxiosError(error) && error.response?.status === 404) {
        return false;
      }
      const status = query.state.data?.cart_work_session?.status;
      if (status === "completed" || status === "cancelled") {
        return false;
      }
      return 4000;
    },
    queryKey: ["scanner-cart-work", sessionId ?? "no-session", cartWorkSessionId ?? "no-work"],
    queryFn: async () => {
      const query = cartWorkSessionId ? `cart_work_session_id=${cartWorkSessionId}` : `session_id=${sessionId}`;
      try {
        const response = await apiClient.get<ScannerCartWorkResponse>(`/scanner/cart-work/current/?${query}`);
        return response.data;
      } catch (error) {
        if (axios.isAxiosError(error) && error.response?.status === 404) {
          options.onStaleSession?.();
        }
        throw error;
      }
    },
    retry: (failureCount, error) => {
      if (axios.isAxiosError(error) && error.response?.status === 404) {
        return false;
      }
      return failureCount < 2;
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

export function useInterBranchArrivals(branch?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["inter-branch-arrivals", branch],
    queryFn: () => getList<InterBranchMMTask>(`/scanner/inter-branch-arrivals/?branch=${encodeURIComponent(branch ?? "")}`),
    refetchInterval: 10_000,
  });
}

export function useRegisterInterBranchArrival() {
  return useMutation({
    mutationFn: async ({ palletCode, workerCode }: { palletCode: string; workerCode?: string }) => {
      const response = await apiClient.post<InterBranchArrivalResponse>("/scanner/inter-branch-arrivals/", {
        pallet_code: palletCode,
        worker_code: workerCode,
        client_operation_id: crypto.randomUUID(),
      });
      return response.data;
    },
  });
}

export function useInterBranchMMTasks(branch?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["inter-branch-mm-tasks", branch],
    queryFn: () => getList<InterBranchMMTask>(`/mm-tasks/?branch=${encodeURIComponent(branch ?? "")}`),
    refetchInterval: 10_000,
  });
}

export function useTransferDiscrepancies(branch?: string) {
  return useQuery({
    queryKey: ["transfer-discrepancies", branch],
    queryFn: () => getList<TransferDiscrepancy>(`/transfer-discrepancies/${branch ? `?branch=${branch}` : ""}`),
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

export function usePrintTransferDiscrepancyReport() {
  return useMutation({
    mutationFn: async ({
      discrepancyId,
      printerCode,
      workerCode,
    }: {
      discrepancyId: number;
      printerCode: string;
      workerCode: string;
    }) => {
      const response = await apiClient.post<TransferDiscrepancyPrintResponse>(
        `/transfer-discrepancies/${discrepancyId}/print-report/`,
        {
          printer_code: printerCode,
          worker_code: workerCode,
        },
      );
      return response.data;
    },
  });
}

export function useRecoverTransferDiscrepancyItem() {
  return useMutation({
    mutationFn: async ({
      clientOperationId,
      destinationLocationCode,
      discrepancyId,
      productCode,
      quantity,
      workerCode,
    }: {
      clientOperationId: string;
      destinationLocationCode: string;
      discrepancyId: number;
      productCode: string;
      quantity: string;
      workerCode: string;
    }) => {
      const response = await apiClient.post<TransferDiscrepancyRecoverResponse>(
        `/transfer-discrepancies/${discrepancyId}/recover-item/`,
        {
          client_operation_id: clientOperationId,
          destination_location_code: destinationLocationCode,
          product_code: productCode,
          quantity,
          worker_code: workerCode,
        },
      );
      return response.data;
    },
  });
}

export function useConfirmTransferDiscrepancyShortage() {
  return useMutation({
    mutationFn: async ({
      clientOperationId,
      discrepancyId,
      productCode,
      quantity,
      workerCode,
    }: {
      clientOperationId: string;
      discrepancyId: number;
      productCode: string;
      quantity: string;
      workerCode: string;
    }) => {
      const response = await apiClient.post<TransferDiscrepancyConfirmShortageResponse>(
        `/transfer-discrepancies/${discrepancyId}/confirm-shortage/`,
        {
          client_operation_id: clientOperationId,
          product_code: productCode,
          quantity,
          worker_code: workerCode,
        },
      );
      return response.data;
    },
  });
}

export function useTransferDiscrepancySourceReviews(status?: string, search?: string, branch?: string) {
  return useQuery({
    queryKey: ["transfer-discrepancy-source-reviews", status, search, branch],
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) {
        params.set("status", status);
      }
      if (search) {
        params.set("search", search);
      }
      if (branch) {
        params.set("branch", branch);
      }
      const query = params.toString();
      return getList<TransferDiscrepancySourceReview>(
        `/transfer-discrepancy-source-reviews/${query ? `?${query}` : ""}`,
      );
    },
  });
}

export function useTransferDiscrepancySourceReview(id?: string) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["transfer-discrepancy-source-review", id],
    queryFn: async () => {
      const response = await apiClient.get<TransferDiscrepancySourceReview>(
        `/transfer-discrepancy-source-reviews/${id}/`,
      );
      return response.data;
    },
  });
}

export function useBeginTransferDiscrepancySourceReview() {
  return useMutation({
    mutationFn: async ({ reviewId, workerCode }: { reviewId: number; workerCode: string }) => {
      const response = await apiClient.post<TransferDiscrepancySourceReviewResponse>(
        `/transfer-discrepancy-source-reviews/${reviewId}/begin/`,
        { worker_code: workerCode },
      );
      return response.data;
    },
  });
}

export function useCompleteTransferDiscrepancySourceReview() {
  return useMutation({
    mutationFn: async ({
      clientOperationId,
      finding,
      findingNote,
      reviewId,
      workerCode,
    }: {
      clientOperationId: string;
      finding: string;
      findingNote: string;
      reviewId: number;
      workerCode: string;
    }) => {
      const response = await apiClient.post<TransferDiscrepancySourceReviewResponse>(
        `/transfer-discrepancy-source-reviews/${reviewId}/complete/`,
        {
          client_operation_id: clientOperationId,
          finding,
          finding_note: findingNote,
          worker_code: workerCode,
        },
      );
      return response.data;
    },
  });
}

export function useTransferDiscrepancyReconciliations(status?: string, route?: string, search?: string, branch?: string) {
  return useQuery({
    queryKey: ["transfer-discrepancy-reconciliations", status, route, search, branch],
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) {
        params.set("status", status);
      }
      if (route) {
        params.set("route", route);
      }
      if (search) {
        params.set("search", search);
      }
      if (branch) {
        params.set("branch", branch);
      }
      const query = params.toString();
      return getList<TransferDiscrepancyReconciliation>(
        `/transfer-discrepancy-reconciliations/${query ? `?${query}` : ""}`,
      );
    },
  });
}

export function useTransferDiscrepancyActions(actionType?: string, branch?: string, search?: string) {
  return useQuery({
    queryKey: ["transfer-discrepancy-actions", actionType, branch, search],
    queryFn: () => {
      const params = new URLSearchParams();
      if (actionType) {
        params.set("action_type", actionType);
      }
      if (branch) {
        params.set("branch", branch);
      }
      if (search) {
        params.set("search", search);
      }
      const query = params.toString();
      return getList<TransferDiscrepancyAction>(`/transfer-discrepancy-actions/${query ? `?${query}` : ""}`);
    },
  });
}

export function useTransferDiscrepancyReconciliation(id?: string) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["transfer-discrepancy-reconciliation", id],
    queryFn: async () => {
      const response = await apiClient.get<TransferDiscrepancyReconciliation>(
        `/transfer-discrepancy-reconciliations/${id}/`,
      );
      return response.data;
    },
  });
}

export function useAcknowledgeTransferDiscrepancyReconciliation() {
  return useMutation({
    mutationFn: async ({ reconciliationId, workerCode }: { reconciliationId: number; workerCode: string }) => {
      const response = await apiClient.post<TransferDiscrepancyReconciliationResponse>(
        `/transfer-discrepancy-reconciliations/${reconciliationId}/acknowledge/`,
        { worker_code: workerCode },
      );
      return response.data;
    },
  });
}

export function useCompleteManualTransferDiscrepancyReconciliation() {
  return useMutation({
    mutationFn: async ({
      clientOperationId,
      decisionNote,
      outcome,
      reconciliationId,
      workerCode,
    }: {
      clientOperationId: string;
      decisionNote: string;
      outcome: string;
      reconciliationId: number;
      workerCode: string;
    }) => {
      const response = await apiClient.post<TransferDiscrepancyReconciliationResponse>(
        `/transfer-discrepancy-reconciliations/${reconciliationId}/complete-manual/`,
        {
          client_operation_id: clientOperationId,
          decision_note: decisionNote,
          outcome,
          worker_code: workerCode,
        },
      );
      return response.data;
    },
  });
}

export function useTransferDiscrepancySourceStockVerifications(status?: string, search?: string, branch?: string) {
  return useQuery({
    queryKey: ["transfer-discrepancy-source-stock-verifications", status, search, branch],
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) {
        params.set("status", status);
      }
      if (search) {
        params.set("search", search);
      }
      if (branch) {
        params.set("branch", branch);
      }
      const query = params.toString();
      return getList<TransferDiscrepancySourceStockVerification>(
        `/transfer-discrepancy-source-stock-verifications/${query ? `?${query}` : ""}`,
      );
    },
  });
}

export function useTransferDiscrepancySourceStockVerification(id?: string) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["transfer-discrepancy-source-stock-verification", id],
    queryFn: async () => {
      const response = await apiClient.get<TransferDiscrepancySourceStockVerification>(
        `/transfer-discrepancy-source-stock-verifications/${id}/`,
      );
      return response.data;
    },
  });
}

export function useBeginTransferDiscrepancySourceStockVerification() {
  return useMutation({
    mutationFn: async ({ verificationId, workerCode }: { verificationId: number; workerCode: string }) => {
      const response = await apiClient.post<TransferDiscrepancySourceStockVerificationResponse>(
        `/transfer-discrepancy-source-stock-verifications/${verificationId}/begin/`,
        { worker_code: workerCode },
      );
      return response.data;
    },
  });
}

export function useRecordTransferDiscrepancySourceStockFound() {
  return useMutation({
    mutationFn: async ({
      clientOperationId,
      destinationLocationCode,
      productCode,
      quantity,
      verificationId,
      workerCode,
    }: {
      clientOperationId: string;
      destinationLocationCode: string;
      productCode: string;
      quantity: string;
      verificationId: number;
      workerCode: string;
    }) => {
      const response = await apiClient.post<TransferDiscrepancySourceStockRecoveryResponse>(
        `/transfer-discrepancy-source-stock-verifications/${verificationId}/record-found/`,
        {
          client_operation_id: clientOperationId,
          destination_location_code: destinationLocationCode,
          product_code: productCode,
          quantity,
          worker_code: workerCode,
        },
      );
      return response.data;
    },
  });
}

export function useCompleteTransferDiscrepancySourceSearch() {
  return useMutation({
    mutationFn: async ({
      clientOperationId,
      searchCompletionNote,
      verificationId,
      workerCode,
    }: {
      clientOperationId: string;
      searchCompletionNote: string;
      verificationId: number;
      workerCode: string;
    }) => {
      const response = await apiClient.post<TransferDiscrepancySourceStockVerificationResponse>(
        `/transfer-discrepancy-source-stock-verifications/${verificationId}/complete-search/`,
        {
          client_operation_id: clientOperationId,
          search_completion_note: searchCompletionNote,
          worker_code: workerCode,
        },
      );
      return response.data;
    },
  });
}

export function useTransferDiscrepancyTransitInvestigations(status?: string, search?: string, branch?: string) {
  return useQuery({
    queryKey: ["transfer-discrepancy-transit-investigations", status, search, branch],
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) {
        params.set("status", status);
      }
      if (search) {
        params.set("search", search);
      }
      if (branch) {
        params.set("branch", branch);
      }
      const query = params.toString();
      return getList<TransferDiscrepancyTransitInvestigation>(
        `/transfer-discrepancy-transit-investigations/${query ? `?${query}` : ""}`,
      );
    },
  });
}

export function useTransferDiscrepancyTransitInvestigation(id?: string) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["transfer-discrepancy-transit-investigation", id],
    queryFn: async () => {
      const response = await apiClient.get<TransferDiscrepancyTransitInvestigation>(
        `/transfer-discrepancy-transit-investigations/${id}/`,
      );
      return response.data;
    },
  });
}

export function useBeginTransferDiscrepancyTransitInvestigation() {
  return useMutation({
    mutationFn: async ({ investigationId, workerCode }: { investigationId: number; workerCode: string }) => {
      const response = await apiClient.post<TransferDiscrepancyTransitInvestigationResponse>(
        `/transfer-discrepancy-transit-investigations/${investigationId}/begin/`,
        { worker_code: workerCode },
      );
      return response.data;
    },
  });
}

export function useCompleteTransferDiscrepancyTransitInvestigation() {
  return useMutation({
    mutationFn: async ({
      clientOperationId,
      finding,
      findingNote,
      investigationId,
      workerCode,
    }: {
      clientOperationId: string;
      finding: string;
      findingNote: string;
      investigationId: number;
      workerCode: string;
    }) => {
      const response = await apiClient.post<TransferDiscrepancyTransitInvestigationResponse>(
        `/transfer-discrepancy-transit-investigations/${investigationId}/complete/`,
        {
          client_operation_id: clientOperationId,
          finding,
          finding_note: findingNote,
          worker_code: workerCode,
        },
      );
      return response.data;
    },
  });
}

export type CurrentEventFilters = {
  actor?: string;
  cart?: string;
  eventType?: string;
  location?: string;
  order?: string;
  product?: string;
  result?: string;
  search?: string;
};

export function useCurrentAuditLogs(branch?: string, filters: CurrentEventFilters = {}) {
  return useQuery({
    queryKey: ["audit-logs", "current", branch, filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      if (filters.search) params.set("search", filters.search);
      if (filters.eventType) params.set("event_type", filters.eventType);
      if (filters.product) params.set("product", filters.product);
      if (filters.result) params.set("result", filters.result);
      if (filters.cart) params.set("cart", filters.cart);
      if (filters.location) params.set("location", filters.location);
      if (filters.order) params.set("order", filters.order);
      if (filters.actor) params.set("actor", filters.actor);
      const query = params.toString();
      return getList<AuditLog>(`/current-events/${query ? `?${query}` : ""}`);
    },
  });
}

export type PickingShortageFilters = {
  actor?: string;
  dateFrom?: string;
  dateTo?: string;
  location?: string;
  product?: string;
  search?: string;
  status?: string;
};

export function usePickingShortages(branch?: string, filters: PickingShortageFilters = {}) {
  return useQuery({
    queryKey: ["picking-shortages", branch, filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      if (filters.search) params.set("search", filters.search);
      if (filters.status) params.set("status", filters.status);
      if (filters.product) params.set("product", filters.product);
      if (filters.location) params.set("location", filters.location);
      if (filters.actor) params.set("actor", filters.actor);
      if (filters.dateFrom) params.set("date_from", filters.dateFrom);
      if (filters.dateTo) params.set("date_to", filters.dateTo);
      const query = params.toString();
      return getList<PickingShortage>(`/picking-shortages/${query ? `?${query}` : ""}`);
    },
  });
}

export function usePickingShortageFoundStock() {
  return useMutation({
    mutationFn: async ({
      locationCode,
      note,
      quantity,
      shortageId,
      workerCode,
    }: {
      locationCode: string;
      note?: string;
      quantity: string;
      shortageId: number;
      workerCode?: string;
    }) => {
      const response = await apiClient.post<{ message: string; shortage: PickingShortage }>(
        `/picking-shortages/${shortageId}/found-stock/`,
        { location_code: locationCode, note, quantity, worker_code: workerCode },
      );
      return response.data;
    },
  });
}

export function usePickingShortageConfirmMissing() {
  return useMutation({
    mutationFn: async ({ note, shortageId, workerCode }: { note?: string; shortageId: number; workerCode?: string }) => {
      const response = await apiClient.post<{ message: string; shortage: PickingShortage }>(
        `/picking-shortages/${shortageId}/confirm-missing/`,
        { note, worker_code: workerCode },
      );
      return response.data;
    },
  });
}

export type ReplenishmentRequestFilters = {
  customerAlias?: string;
  order?: string;
  product?: string;
  search?: string;
  status?: string;
};

export function useReplenishmentRequests(branch?: string, filters: ReplenishmentRequestFilters = {}) {
  return useQuery({
    queryKey: ["replenishment-requests", branch, filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      if (filters.search) params.set("search", filters.search);
      if (filters.status) params.set("status", filters.status);
      if (filters.product) params.set("product", filters.product);
      if (filters.customerAlias) params.set("customer_alias", filters.customerAlias);
      if (filters.order) params.set("order", filters.order);
      const query = params.toString();
      return getList<ReplenishmentRequest>(`/replenishment-requests/${query ? `?${query}` : ""}`);
    },
  });
}

export function useMarkReplenishmentOrderedManually() {
  return useMutation({
    mutationFn: async ({
      externalReference,
      note,
      requestId,
      workerCode,
    }: {
      externalReference?: string;
      note?: string;
      requestId: number;
      workerCode?: string;
    }) => {
      const response = await apiClient.post<{ message: string; request: ReplenishmentRequest }>(
        `/replenishment-requests/${requestId}/mark-ordered-manually/`,
        { external_reference: externalReference, note, worker_code: workerCode },
      );
      return response.data;
    },
  });
}

export function useArchiveAuditLogs(dateFrom: string, dateTo: string) {
  return useQuery({
    enabled: Boolean(dateFrom && dateTo),
    queryKey: ["audit-logs", "archive", dateFrom, dateTo],
    queryFn: () => getList<AuditLog>(`/audit-logs/archive/?date_from=${dateFrom}&date_to=${dateTo}`),
  });
}
