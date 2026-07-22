import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";

import { apiClient, getHealth, getList } from "./client";
import type {
  Branch,
  BranchDispatchPolicy,
  BranchMembership,
  AuditLog,
  CorrectionActivityResponse,
  CycleCountSession,
  CycleCountReviewQueueResponse,
  DeliveryRoute,
  InventoryExceptionSummary,
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
  ReturnDocument,
  ReturnBatch,
  ReplenishmentRequest,
  RouteRoundSchedule,
  RouteRun,
  ScannerContentsResponse,
  ScannerCycleCountRecount,
  ScannerCycleCountResponse,
  ScannerCycleCountSession,
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
  SalesCorrection,
  SalesHistoryCandidate,
  Shipment,
  ShipmentRouteTarget,
  StockMovement,
  TransportOverview,
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

export function doNotRetryDeterministicHttpErrors(failureCount: number, error: unknown) {
  if (axios.isAxiosError(error) && [400, 401, 403, 404].includes(error.response?.status ?? 0)) {
    return false;
  }
  return failureCount < 1;
}

function buildCountPath(endpoint: string, branch?: string, status?: string, branchParam = "branch") {
  const params = new URLSearchParams();
  if (branch) params.set(branchParam, branch);
  if (status) params.set("status", status);
  params.set("page_size", "1");
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

export function useProductSearch(search: string) {
  return useQuery({
    queryKey: ["products", "search", search],
    queryFn: () => getList<Product>(`/products/${search ? `?search=${encodeURIComponent(search)}` : ""}`),
  });
}

export function useInventoryItems(branch?: string) {
  return useQuery({
    queryKey: ["inventory-items", branch],
    queryFn: () => getList<InventoryItem>(`/inventory-items/${branch ? `?branch=${branch}` : ""}`),
  });
}

export function useInventoryPosition(branch?: string, location?: number | string, product?: number | string) {
  return useQuery({
    enabled: Boolean(branch && location && product),
    queryKey: ["inventory-position", branch, location, product],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      if (location) params.set("location", String(location));
      if (product) params.set("product", String(product));
      return getList<InventoryItem>(`/inventory-items/?${params.toString()}`);
    },
  });
}

export type StockTransferListFilters = {
  branch?: string;
  dateFrom?: string;
  dateTo?: string;
  destinationLocation?: string;
  page?: number;
  product?: string;
  search?: string;
  sourceLocation?: string;
};

export function useStockTransfers(filters: StockTransferListFilters = {}) {
  return useQuery({
    queryKey: ["stock-transfers", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      params.set("internal_transfer", "true");
      params.set("movement_type", "transfer");
      if (filters.branch) params.set("branch", filters.branch);
      if (filters.search) params.set("search", filters.search);
      if (filters.product) params.set("product", filters.product);
      if (filters.sourceLocation) params.set("source_location", filters.sourceLocation);
      if (filters.destinationLocation) params.set("destination_location", filters.destinationLocation);
      if (filters.dateFrom) params.set("date_from", filters.dateFrom);
      if (filters.dateTo) params.set("date_to", filters.dateTo);
      if (filters.page && filters.page > 1) params.set("page", String(filters.page));
      return getList<StockMovement>(`/stock-movements/?${params.toString()}`);
    },
  });
}

export function useStockTransfer(movementId?: string) {
  return useQuery({
    enabled: Boolean(movementId),
    queryKey: ["stock-transfer", movementId],
    queryFn: async () => {
      const response = await apiClient.get<StockMovement>(`/stock-movements/${movementId}/`);
      return response.data;
    },
  });
}

export type StockAdjustmentListFilters = {
  branch?: string;
  dateFrom?: string;
  dateTo?: string;
  direction?: string;
  location?: string;
  page?: number;
  performedBy?: string;
  product?: string;
  reasonCode?: string;
  search?: string;
};

export type CreateStockAdjustmentPayload = {
  branch: string;
  direction: "increase" | "decrease";
  location: number | string;
  note: string;
  product: number | string;
  quantity: string;
  reasonCode: string;
};

export function useStockAdjustments(filters: StockAdjustmentListFilters = {}) {
  return useQuery({
    queryKey: ["stock-adjustments", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      params.set("movement_type", "adjustment");
      if (filters.branch) params.set("branch", filters.branch);
      if (filters.search) params.set("search", filters.search);
      if (filters.product) params.set("product", filters.product);
      if (filters.location) params.set("location", filters.location);
      if (filters.direction) params.set("adjustment_direction", filters.direction);
      if (filters.reasonCode) params.set("adjustment_reason", filters.reasonCode);
      if (filters.performedBy) params.set("performed_by", filters.performedBy);
      if (filters.dateFrom) params.set("date_from", filters.dateFrom);
      if (filters.dateTo) params.set("date_to", filters.dateTo);
      if (filters.page && filters.page > 1) params.set("page", String(filters.page));
      return getList<StockMovement>(`/stock-adjustments/?${params.toString()}`);
    },
  });
}

export function useStockAdjustment(movementId?: string) {
  return useQuery({
    enabled: Boolean(movementId),
    queryKey: ["stock-adjustment", movementId],
    queryFn: async () => {
      const response = await apiClient.get<StockMovement>(`/stock-adjustments/${movementId}/`);
      return response.data;
    },
  });
}

export function useCreateStockAdjustment() {
  return useMutation({
    mutationFn: async (payload: CreateStockAdjustmentPayload) => {
      const response = await apiClient.post<StockMovement>("/stock-adjustments/", {
        branch: payload.branch,
        direction: payload.direction,
        location: payload.location,
        note: payload.note,
        product: payload.product,
        quantity: payload.quantity,
        reason_code: payload.reasonCode,
      });
      return response.data;
    },
  });
}

export type CycleCountListFilters = {
  branch?: string;
  dateFrom?: string;
  dateTo?: string;
  page?: number;
  search?: string;
  status?: string;
};

export function useCycleCounts(filters: CycleCountListFilters = {}) {
  return useQuery({
    queryKey: ["cycle-counts", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters.branch) params.set("branch", filters.branch);
      if (filters.status) params.set("status", filters.status);
      if (filters.search) params.set("search", filters.search);
      if (filters.dateFrom) params.set("date_from", filters.dateFrom);
      if (filters.dateTo) params.set("date_to", filters.dateTo);
      if (filters.page && filters.page > 1) params.set("page", String(filters.page));
      return getList<CycleCountSession>(`/cycle-counts/?${params.toString()}`);
    },
  });
}

export function useCycleCount(sessionId?: string | number) {
  return useQuery({
    enabled: Boolean(sessionId),
    queryKey: ["cycle-count", sessionId],
    queryFn: async () => {
      const response = await apiClient.get<CycleCountSession>(`/cycle-counts/${sessionId}/`);
      return response.data;
    },
  });
}

export function useCreateCycleCount() {
  return useMutation({
    mutationFn: async (payload: { branch: string; locationIds: number[]; name: string; note: string }) => {
      const response = await apiClient.post<CycleCountSession>("/cycle-counts/", {
        branch: payload.branch,
        location_ids: payload.locationIds,
        name: payload.name,
        note: payload.note,
      });
      return response.data;
    },
  });
}

export function useOpenCycleCount() {
  return useMutation({
    mutationFn: async (sessionId: number) => {
      const response = await apiClient.post<CycleCountSession>(`/cycle-counts/${sessionId}/open/`);
      return response.data;
    },
  });
}

export function useCloseCycleCount() {
  return useMutation({
    mutationFn: async (sessionId: number) => {
      const response = await apiClient.post<CycleCountSession>(`/cycle-counts/${sessionId}/close/`);
      return response.data;
    },
  });
}

export function useCancelCycleCount() {
  return useMutation({
    mutationFn: async (sessionId: number) => {
      const response = await apiClient.post<CycleCountSession>(`/cycle-counts/${sessionId}/cancel/`);
      return response.data;
    },
  });
}

export function useApplyCycleCountAdjustment() {
  return useMutation({
    mutationFn: async (payload: { sessionId: number; lineId: number; note?: string }) => {
      const response = await apiClient.post<CycleCountSession>(
        `/cycle-counts/${payload.sessionId}/lines/${payload.lineId}/apply-adjustment/`,
        { note: payload.note ?? "" },
      );
      return response.data;
    },
  });
}

export function useResolveCycleCountWithoutAdjustment() {
  return useMutation({
    mutationFn: async (payload: { sessionId: number; lineId: number; note: string }) => {
      const response = await apiClient.post<CycleCountSession>(
        `/cycle-counts/${payload.sessionId}/lines/${payload.lineId}/resolve-without-adjustment/`,
        { note: payload.note },
      );
      return response.data;
    },
  });
}

export function useRequestCycleCountRecount() {
  return useMutation({
    mutationFn: async (payload: { sessionId: number; lineId: number; reason: string }) => {
      const response = await apiClient.post<CycleCountSession>(
        `/cycle-counts/${payload.sessionId}/lines/${payload.lineId}/request-recount/`,
        { reason: payload.reason },
      );
      return response.data;
    },
  });
}

export function useAcceptCycleCountRecount() {
  return useMutation({
    mutationFn: async (payload: { sessionId: number; recountId: number; note?: string }) => {
      const response = await apiClient.post<CycleCountSession>(
        `/cycle-counts/${payload.sessionId}/recounts/${payload.recountId}/accept/`,
        { note: payload.note ?? "" },
      );
      return response.data;
    },
  });
}

export function useCancelCycleCountRecount() {
  return useMutation({
    mutationFn: async (payload: { sessionId: number; recountId: number; note: string }) => {
      const response = await apiClient.post<CycleCountSession>(
        `/cycle-counts/${payload.sessionId}/recounts/${payload.recountId}/cancel/`,
        { note: payload.note },
      );
      return response.data;
    },
  });
}

export type CycleCountReviewQueueFilters = {
  branch?: string;
  dateFrom?: string;
  dateTo?: string;
  itemType?: string;
  location?: string;
  page?: number;
  pageSize?: number;
  product?: string;
  recountStatus?: string;
  reconciliationStatus?: string;
  search?: string;
  staleOnly?: boolean;
};

export function useCycleCountReviewQueue(filters: CycleCountReviewQueueFilters = {}) {
  return useQuery({
    enabled: Boolean(filters.branch),
    queryKey: ["cycle-count-review-queue", filters],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.branch) params.set("branch", filters.branch);
      if (filters.itemType) params.set("item_type", filters.itemType);
      if (filters.search) params.set("search", filters.search);
      if (filters.location) params.set("location", filters.location);
      if (filters.product) params.set("product", filters.product);
      if (filters.recountStatus) params.set("recount_status", filters.recountStatus);
      if (filters.reconciliationStatus) params.set("reconciliation_status", filters.reconciliationStatus);
      if (filters.staleOnly) params.set("stale_only", "true");
      if (filters.dateFrom) params.set("date_from", filters.dateFrom);
      if (filters.dateTo) params.set("date_to", filters.dateTo);
      if (filters.page && filters.page > 1) params.set("page", String(filters.page));
      if (filters.pageSize) params.set("page_size", String(filters.pageSize));
      const response = await apiClient.get<CycleCountReviewQueueResponse>(`/cycle-count-review-queue/?${params.toString()}`);
      return response.data;
    },
  });
}

export function useOrders(branch?: string) {
  return useQuery({
    queryKey: ["orders", branch],
    queryFn: () => getList<Order>(`/orders/${branch ? `?branch=${branch}` : ""}`),
  });
}

export type ShipmentListFilters = {
  branch?: string;
  customer?: string;
  deliveryDate?: string;
  externalReference?: string;
  ordering?: string;
  page?: number;
  paymentMethod?: string;
  pickingStatus?: string;
  route?: string;
  search?: string;
  shipmentStatus?: string;
};

function shipmentFilterParams(filters: ShipmentListFilters = {}) {
  const params = new URLSearchParams();
  if (filters.branch) params.set("branch", filters.branch);
  if (filters.search) params.set("search", filters.search);
  if (filters.shipmentStatus) params.set("shipment_status", filters.shipmentStatus);
  if (filters.pickingStatus) params.set("picking_status", filters.pickingStatus);
  if (filters.route) params.set("route", filters.route);
  if (filters.deliveryDate) params.set("delivery_date", filters.deliveryDate);
  if (filters.customer) params.set("customer", filters.customer);
  if (filters.paymentMethod) params.set("payment_method", filters.paymentMethod);
  if (filters.externalReference) params.set("external_reference", filters.externalReference);
  if (filters.ordering) params.set("ordering", filters.ordering);
  if (filters.page && filters.page > 1) params.set("page", String(filters.page));
  return params;
}

export function useShipments(filters: ShipmentListFilters = {}) {
  return useQuery({
    enabled: Boolean(filters.branch),
    queryKey: ["shipments", filters],
    queryFn: () => getList<Shipment>(`/shipments/?${shipmentFilterParams(filters).toString()}`),
  });
}

export function useShipment(shipmentId?: string | number | null, branch?: string) {
  return useQuery({
    enabled: Boolean(shipmentId),
    queryKey: ["shipment", shipmentId, branch],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      const suffix = params.toString() ? `?${params.toString()}` : "";
      const response = await apiClient.get<Shipment>(`/shipments/${shipmentId}/${suffix}`);
      return response.data;
    },
  });
}

export function useShipmentRouteTargets({
  branch,
  currentRouteRun,
  operationalDate,
  scope = "today",
  search = "",
}: {
  branch?: string;
  currentRouteRun?: number | null;
  operationalDate?: string | null;
  scope?: "today" | "week";
  search?: string;
}) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["shipment-route-targets", branch, currentRouteRun, operationalDate, scope, search],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      if (currentRouteRun) params.set("exclude_route_run", String(currentRouteRun));
      if (operationalDate) params.set("operational_date", operationalDate);
      if (scope) params.set("scope", scope);
      if (search) params.set("search", search);
      const response = await apiClient.get<{ results: ShipmentRouteTarget[] }>(`/shipments/route-targets/?${params.toString()}`);
      return response.data;
    },
  });
}

type ShipmentCommandPayload = {
  id: number;
  action: string;
  payload?: Record<string, unknown>;
};

function useShipmentCommand() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ action, id, payload = {} }: ShipmentCommandPayload) => {
      const response = await apiClient.post<{ message: string; shipment: Shipment }>(`/shipments/${id}/${action}/`, payload);
      return response.data;
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["shipments"] });
      void queryClient.invalidateQueries({ queryKey: ["shipment", data.shipment.id] });
      void queryClient.invalidateQueries({ queryKey: ["route-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard-count"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-logs"] });
      void queryClient.invalidateQueries({ queryKey: ["transport-overview"] });
      void queryClient.invalidateQueries({ queryKey: ["picking-tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["scanner-inter-branch-arrivals"] });
    },
  });
}

export function useActivateShipment() {
  const mutation = useShipmentCommand();
  return { ...mutation, mutateAsync: (id: number) => mutation.mutateAsync({ id, action: "activate", payload: { client_operation_id: crypto.randomUUID() } }) };
}

export function usePostShipmentPickingLists() {
  const mutation = useShipmentCommand();
  return { ...mutation, mutateAsync: (id: number) => mutation.mutateAsync({ id, action: "post-picking-lists", payload: { client_operation_id: crypto.randomUUID() } }) };
}

export function usePrepareShipment() {
  const mutation = useShipmentCommand();
  return { ...mutation, mutateAsync: (id: number) => mutation.mutateAsync({ id, action: "prepare" }) };
}

export function useCancelShipment() {
  const mutation = useShipmentCommand();
  return { ...mutation, mutateAsync: (id: number, reason: string) => mutation.mutateAsync({ id, action: "cancel", payload: { reason } }) };
}

export function usePrintShipmentDocuments() {
  const mutation = useShipmentCommand();
  return { ...mutation, mutateAsync: (id: number) => mutation.mutateAsync({ id, action: "print-documents", payload: { printer: "WMS-DEMO" } }) };
}

export function usePostShipmentDocuments() {
  const mutation = useShipmentCommand();
  return { ...mutation, mutateAsync: (id: number) => mutation.mutateAsync({ id, action: "post-documents" }) };
}

export function useConfirmShipmentPickingRoute() {
  const mutation = useShipmentCommand();
  return { ...mutation, mutateAsync: (id: number) => mutation.mutateAsync({ id, action: "confirm-picking-route" }) };
}

export function usePrintShipmentProforma() {
  const mutation = useShipmentCommand();
  return { ...mutation, mutateAsync: (id: number) => mutation.mutateAsync({ id, action: "print-proforma" }) };
}

export function useCloseShipmentRoute() {
  const mutation = useShipmentCommand();
  return { ...mutation, mutateAsync: (id: number) => mutation.mutateAsync({ id, action: "close-route" }) };
}

export function useChangeShipmentRoute() {
  const mutation = useShipmentCommand();
  return {
    ...mutation,
    mutateAsync: (id: number, target: ShipmentRouteTarget) => {
      const payload =
        target.target_type === "schedule_slot"
          ? {
              schedule: target.schedule,
              operational_date: target.service_date,
              client_operation_id: crypto.randomUUID(),
            }
          : {
              route_run: target.route_run,
              client_operation_id: crypto.randomUUID(),
            };
      return mutation.mutateAsync({ id, action: "change-route", payload });
    },
  };
}

export function useChangeShipmentStatus() {
  const mutation = useShipmentCommand();
  return {
    ...mutation,
    mutateAsync: (id: number, nextStatus: string, reason: string) =>
      mutation.mutateAsync({
        id,
        action: "change-status",
        payload: { status: nextStatus, reason, client_operation_id: crypto.randomUUID() },
      }),
  };
}

export function useRemoveShipmentLineQuantity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      lineId,
      quantity,
      reason,
    }: {
      id: number;
      lineId: number;
      quantity: string;
      reason: string;
    }) => {
      const response = await apiClient.post<{ message: string; shipment: Shipment; line_id: number; adjustment_id: number }>(
        `/shipments/${id}/lines/${lineId}/remove-quantity/`,
        { quantity, reason, client_operation_id: crypto.randomUUID() },
      );
      return response.data;
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["shipments"] });
      void queryClient.invalidateQueries({ queryKey: ["shipment", data.shipment.id] });
      void queryClient.invalidateQueries({ queryKey: ["route-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["picking-tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-logs"] });
    },
  });
}

export type LocationListFilters = {
  branch?: string;
  isActive?: string;
  locationType?: string;
  page?: number;
  search?: string;
};

export type BranchListFilters = {
  isActive?: string;
  page?: number;
  search?: string;
};

export function useLocations(branch?: string) {
  return useQuery({
    queryKey: ["locations", branch],
    queryFn: () => getList<Location>(`/locations/${branch ? `?branch=${branch}` : ""}`),
  });
}

export function useLocationSearch(branch?: string, search = "") {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["locations", "search", branch, search],
    queryFn: () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      if (search) params.set("search", search);
      return getList<Location>(`/locations/?${params.toString()}`);
    },
  });
}

export function useLocationList(filters: LocationListFilters = {}) {
  return useQuery({
    queryKey: ["locations", "register", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters.branch) params.set("branch", filters.branch);
      if (filters.search) params.set("search", filters.search);
      if (filters.locationType) params.set("location_type", filters.locationType);
      if (filters.isActive) params.set("is_active", filters.isActive);
      if (filters.page && filters.page > 1) params.set("page", String(filters.page));
      const query = params.toString();
      return getList<Location>(`/locations/${query ? `?${query}` : ""}`);
    },
  });
}

export function useLocation(locationId?: string) {
  return useQuery({
    enabled: Boolean(locationId),
    queryKey: ["location", locationId],
    queryFn: async () => {
      const response = await apiClient.get<Location>(`/locations/${locationId}/`);
      return response.data;
    },
  });
}

export function useBranches(filters: BranchListFilters = {}) {
  return useQuery({
    queryKey: ["branches", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters.search) params.set("search", filters.search);
      if (filters.isActive) params.set("is_active", filters.isActive);
      if (filters.page && filters.page > 1) params.set("page", String(filters.page));
      const query = params.toString();
      return getList<Branch>(`/branches/${query ? `?${query}` : ""}`);
    },
  });
}

export function useBranch(branchId?: string) {
  return useQuery({
    enabled: Boolean(branchId),
    queryKey: ["branch", branchId],
    queryFn: async () => {
      const response = await apiClient.get<Branch>(`/branches/${branchId}/`);
      return response.data;
    },
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

export function useReturnDocuments(branch?: string, search?: string, status?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["return-documents", branch, search, status],
    queryFn: () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      if (search) params.set("search", search);
      if (status) params.set("status", status);
      return getList<ReturnDocument>(`/return-documents/?${params.toString()}`);
    },
  });
}

export function useReturnDocument(id?: string) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["return-document", id],
    queryFn: async () => {
      const response = await apiClient.get<ReturnDocument>(`/return-documents/${id}/`);
      return response.data;
    },
  });
}

export function useLookupReturnDocument() {
  return useMutation({
    mutationFn: async ({ branch, externalReference }: { branch: string; externalReference: string }) => {
      const params = new URLSearchParams({ branch, external_reference: externalReference });
      const response = await apiClient.get<ReturnDocument>(`/return-documents/lookup/?${params.toString()}`);
      return response.data;
    },
  });
}

export function useRecordReturnAction() {
  return useMutation({
    mutationFn: async ({
      actionType,
      clientOperationId,
      documentId,
      lineId,
      note,
      quantity,
    }: {
      actionType: string;
      clientOperationId: string;
      documentId: number;
      lineId: number;
      note?: string;
      quantity: string;
    }) => {
      const response = await apiClient.post<{ message: string; action_id: number; document: ReturnDocument }>(
        `/return-documents/${documentId}/lines/${lineId}/actions/`,
        {
          action_type: actionType,
          client_operation_id: clientOperationId,
          note: note || "",
          quantity,
        },
      );
      return response.data;
    },
  });
}

export function useSalesCorrections(branch?: string, status?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["sales-corrections", branch, status],
    queryFn: () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      if (status) params.set("status", status);
      return getList<SalesCorrection>(`/sales-corrections/?${params.toString()}`);
    },
  });
}

export function useSalesCorrection(id?: string) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["sales-correction", id],
    queryFn: async () => {
      const response = await apiClient.get<SalesCorrection>(`/sales-corrections/${id}/`);
      return response.data;
    },
  });
}

export function useCreateSalesCorrection() {
  return useMutation({
    mutationFn: async ({ branch, note }: { branch: string; note?: string }) => {
      const response = await apiClient.post<SalesCorrection>("/sales-corrections/", { branch, note: note || "" });
      return response.data;
    },
  });
}

export function useSalesHistorySearch(branch?: string, product?: string) {
  return useQuery({
    enabled: Boolean(branch && product),
    queryKey: ["sales-history", branch, product],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      if (product) params.set("product", product);
      const response = await apiClient.get<SalesHistoryCandidate[]>(`/sales-corrections/sales-history/?${params.toString()}`);
      return response.data;
    },
  });
}

export function useAddSalesCorrectionLine() {
  return useMutation({
    mutationFn: async ({ correctionId, quantity, sourceOrderLine }: { correctionId: number; quantity: string; sourceOrderLine: number }) => {
      const response = await apiClient.post<SalesCorrection>(`/sales-corrections/${correctionId}/add-line/`, {
        quantity,
        source_order_line: sourceOrderLine,
      });
      return response.data;
    },
  });
}

export function useUpdateSalesCorrectionLine() {
  return useMutation({
    mutationFn: async ({ correctionId, lineId, quantity }: { correctionId: number; lineId: number; quantity: string }) => {
      const response = await apiClient.post<SalesCorrection>(`/sales-corrections/${correctionId}/lines/${lineId}/update/`, {
        quantity,
      });
      return response.data;
    },
  });
}

export function useRemoveSalesCorrectionLine() {
  return useMutation({
    mutationFn: async ({ correctionId, lineId }: { correctionId: number; lineId: number }) => {
      const response = await apiClient.post<SalesCorrection>(`/sales-corrections/${correctionId}/lines/${lineId}/remove/`);
      return response.data;
    },
  });
}

export function useConfirmSalesCorrection() {
  return useMutation({
    mutationFn: async ({ clientOperationId, correctionId }: { clientOperationId: string; correctionId: number }) => {
      const response = await apiClient.post<{ message: string; correction: SalesCorrection }>(`/sales-corrections/${correctionId}/confirm/`, {
        client_operation_id: clientOperationId,
      });
      return response.data;
    },
  });
}

export function useCorrectionActivityReport(filters: {
  branch?: string;
  correctionReference?: string;
  customer?: string;
  dateFrom?: string;
  dateTo?: string;
  employee?: string;
  product?: string;
  sourceSalesDocument?: string;
}) {
  return useQuery({
    enabled: Boolean(filters.branch),
    queryKey: ["correction-activity", filters],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.branch) params.set("branch", filters.branch);
      if (filters.employee) params.set("employee", filters.employee);
      if (filters.dateFrom) params.set("date_from", filters.dateFrom);
      if (filters.dateTo) params.set("date_to", filters.dateTo);
      if (filters.correctionReference) params.set("correction_reference", filters.correctionReference);
      if (filters.customer) params.set("customer", filters.customer);
      if (filters.sourceSalesDocument) params.set("source_sales_document", filters.sourceSalesDocument);
      if (filters.product) params.set("product", filters.product);
      const response = await apiClient.get<CorrectionActivityResponse>(`/sales-corrections/activity-report/?${params.toString()}`);
      return response.data;
    },
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

export function useDeliveryRoutes(branch?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["delivery-routes", branch],
    queryFn: () => getList<DeliveryRoute>(`/delivery-routes/${branch ? `?branch=${branch}` : ""}`),
    retry: doNotRetryDeterministicHttpErrors,
  });
}

export function useRouteRoundSchedules(branch?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["route-round-schedules", branch],
    queryFn: () => getList<RouteRoundSchedule>(`/route-round-schedules/${branch ? `?branch=${branch}` : ""}`),
    retry: doNotRetryDeterministicHttpErrors,
  });
}

export function useBranchDispatchPolicies(branch?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["branch-dispatch-policies", branch],
    queryFn: () => getList<BranchDispatchPolicy>(`/branch-dispatch-policies/${branch ? `?branch=${branch}` : ""}`),
    retry: doNotRetryDeterministicHttpErrors,
  });
}

export function useSaveBranchDispatchPolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      branch: number;
      id?: number;
      maxRoutesPerWave: number;
      minWaveGapMinutes: number;
    }) => {
      const body = {
        branch: payload.branch,
        max_routes_per_wave: payload.maxRoutesPerWave,
        min_wave_gap_minutes: payload.minWaveGapMinutes,
      };
      const response = payload.id
        ? await apiClient.patch<BranchDispatchPolicy>(`/branch-dispatch-policies/${payload.id}/`, body)
        : await apiClient.post<BranchDispatchPolicy>("/branch-dispatch-policies/", body);
      return response.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["branch-dispatch-policies"] });
      void queryClient.invalidateQueries({ queryKey: ["route-round-schedules"] });
    },
  });
}

export function useCreateRouteRoundSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      route: number;
      weekday: number;
      roundNumber: number;
      cutoffTime: string;
      departureTime: string;
      dispatchWave: string;
      operationalLabel?: string;
      isActive: boolean;
    }) => {
      const response = await apiClient.post<RouteRoundSchedule>("/route-round-schedules/", {
        route: payload.route,
        weekday: payload.weekday,
        round_number: payload.roundNumber,
        cutoff_time: payload.cutoffTime,
        departure_time: payload.departureTime,
        dispatch_wave: payload.dispatchWave,
        operational_label: payload.operationalLabel ?? "",
        is_active: payload.isActive,
      });
      return response.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["route-round-schedules"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-logs"] });
    },
  });
}

export function useUpdateRouteRoundSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      id: number;
      route: number;
      weekday: number;
      roundNumber: number;
      cutoffTime: string;
      departureTime: string;
      dispatchWave: string;
      operationalLabel?: string;
      isActive: boolean;
    }) => {
      const response = await apiClient.put<RouteRoundSchedule>(`/route-round-schedules/${payload.id}/`, {
        route: payload.route,
        weekday: payload.weekday,
        round_number: payload.roundNumber,
        cutoff_time: payload.cutoffTime,
        departure_time: payload.departureTime,
        dispatch_wave: payload.dispatchWave,
        operational_label: payload.operationalLabel ?? "",
        is_active: payload.isActive,
      });
      return response.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["route-round-schedules"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-logs"] });
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

export function useOverrideRouteRunTimes() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      cutoffAt,
      dispatchWave,
      plannedDepartureAt,
      routeRunId,
    }: {
      cutoffAt: string;
      dispatchWave: string;
      plannedDepartureAt: string;
      routeRunId: number;
    }) => {
      const response = await apiClient.post<{ message: string; route_run: RouteRun }>(
        `/route-runs/${routeRunId}/override-times/`,
        {
          cutoff_at: cutoffAt,
          planned_departure_at: plannedDepartureAt,
          dispatch_wave: dispatchWave,
        },
      );
      return response.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["route-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-logs"] });
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
      clientOperationId,
      productCode,
      quantity,
      sourceLocationCode,
      targetLocationCode,
    }: {
      clientOperationId: string;
      productCode: string;
      quantity: string;
      sourceLocationCode: string;
      targetLocationCode: string;
    }) => {
      const response = await apiClient.post<ScannerQuickTransferResponse>("/scanner/quick-transfer/", {
        client_operation_id: clientOperationId,
        product_code: productCode,
        quantity,
        source_location_code: sourceLocationCode,
        target_location_code: targetLocationCode,
      });
      return response.data;
    },
  });
}

export function useScannerCycleCounts(branch?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["scanner-cycle-counts", branch],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      const response = await apiClient.get<{ results: ScannerCycleCountSession[] }>(`/scanner/cycle-counts/?${params.toString()}`);
      return response.data;
    },
  });
}

export function useScannerCycleCount(sessionId?: string | number) {
  return useQuery({
    enabled: Boolean(sessionId),
    queryKey: ["scanner-cycle-count", sessionId],
    queryFn: async () => {
      const response = await apiClient.get<ScannerCycleCountResponse>(`/scanner/cycle-counts/${sessionId}/`);
      return response.data;
    },
  });
}

export function useScannerCycleCountSaveLine() {
  return useMutation({
    mutationFn: async ({
      locationId,
      productCode,
      quantity,
      sessionId,
    }: {
      locationId: number;
      productCode: string;
      quantity: string;
      sessionId: number;
    }) => {
      const response = await apiClient.post<ScannerCycleCountResponse>(
        `/scanner/cycle-counts/${sessionId}/locations/${locationId}/count/`,
        { product_code: productCode, quantity },
      );
      return response.data;
    },
  });
}

export function useScannerCycleCountSubmitLocation() {
  return useMutation({
    mutationFn: async ({
      confirmZeroes,
      locationId,
      sessionId,
    }: {
      confirmZeroes: boolean;
      locationId: number;
      sessionId: number;
    }) => {
      const response = await apiClient.post<ScannerCycleCountResponse>(
        `/scanner/cycle-counts/${sessionId}/locations/${locationId}/submit/`,
        { confirm_zeroes: confirmZeroes },
      );
      return response.data;
    },
  });
}

export function useScannerCycleCountRecounts(branch?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["scanner-cycle-count-recounts", branch],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      const response = await apiClient.get<{ results: ScannerCycleCountRecount[] }>(`/scanner/cycle-count-recounts/?${params.toString()}`);
      return response.data.results;
    },
  });
}

export function useScannerCycleCountRecount(recountId?: string | number) {
  return useQuery({
    enabled: Boolean(recountId),
    queryKey: ["scanner-cycle-count-recount", recountId],
    queryFn: async () => {
      const response = await apiClient.get<ScannerCycleCountRecount>(`/scanner/cycle-count-recounts/${recountId}/`);
      return response.data;
    },
  });
}

export function useScannerCycleCountRecountSubmit() {
  return useMutation({
    mutationFn: async ({
      locationCode,
      productCode,
      quantity,
      recountId,
    }: {
      locationCode: string;
      productCode: string;
      quantity: string;
      recountId: number;
    }) => {
      const response = await apiClient.post<ScannerCycleCountRecount>(
        `/scanner/cycle-count-recounts/${recountId}/submit/`,
        {
          location_code: locationCode,
          product_code: productCode,
          quantity,
        },
      );
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
  dateFrom?: string;
  dateTo?: string;
  eventType?: string;
  location?: string;
  order?: string;
  page?: number;
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
      if (filters.dateFrom) params.set("date_from", filters.dateFrom);
      if (filters.dateTo) params.set("date_to", filters.dateTo);
      if (filters.page && filters.page > 1) params.set("page", String(filters.page));
      const query = params.toString();
      return getList<AuditLog>(`/current-events/${query ? `?${query}` : ""}`);
    },
  });
}

export function useAuditLogDetail(id?: string) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["audit-log", id],
    queryFn: async () => {
      const response = await apiClient.get<AuditLog>(`/audit-logs/${id}/`);
      return response.data;
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

export function useInventoryExceptionSummary(branch?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["inventory-exceptions", "summary", branch],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      const response = await apiClient.get<InventoryExceptionSummary>(`/inventory-exceptions/summary/?${params.toString()}`);
      return response.data;
    },
  });
}

export function useTransportOverview(branch?: string) {
  return useQuery({
    enabled: Boolean(branch),
    queryKey: ["transport-overview", branch],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (branch) params.set("branch", branch);
      const response = await apiClient.get<TransportOverview>(`/transport-overview/?${params.toString()}`);
      return response.data;
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

export function useArchiveAuditLogs(branch?: string, filters: CurrentEventFilters = {}) {
  const dateFrom = filters.dateFrom ?? "";
  const dateTo = filters.dateTo ?? "";
  return useQuery({
    enabled: Boolean(dateFrom && dateTo),
    queryKey: ["audit-logs", "archive", branch, filters],
    queryFn: () => {
      const params = new URLSearchParams();
      params.set("date_from", dateFrom);
      params.set("date_to", dateTo);
      if (branch) params.set("branch", branch);
      if (filters.search) params.set("search", filters.search);
      if (filters.eventType) params.set("event_type", filters.eventType);
      if (filters.product) params.set("product", filters.product);
      if (filters.result) params.set("result", filters.result);
      if (filters.cart) params.set("cart", filters.cart);
      if (filters.location) params.set("location", filters.location);
      if (filters.order) params.set("order", filters.order);
      if (filters.actor) params.set("actor", filters.actor);
      if (filters.page && filters.page > 1) params.set("page", String(filters.page));
      return getList<AuditLog>(`/audit-logs/archive/?${params.toString()}`);
    },
  });
}
