import { useQuery } from "@tanstack/react-query";

import { getHealth, getList } from "./client";
import type { Branch, InventoryItem, Location, Order, PickingTask, Product, ReturnBatch, RouteRun } from "../types/api";


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

export function usePickingTasks() {
  return useQuery({
    queryKey: ["picking-tasks"],
    queryFn: () => getList<PickingTask>("/picking-tasks/"),
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
