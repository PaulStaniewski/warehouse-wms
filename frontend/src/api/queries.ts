import { useMutation, useQuery } from "@tanstack/react-query";

import { apiClient, getHealth, getList } from "./client";
import type {
  Branch,
  InventoryItem,
  Location,
  Order,
  OrderLine,
  PickingTask,
  Product,
  ReturnBatch,
  RouteRun,
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
