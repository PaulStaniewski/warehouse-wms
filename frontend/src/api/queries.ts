import { useQuery } from "@tanstack/react-query";

import { getHealth, getList } from "./client";
import type { InventoryItem, Location, Order, Product } from "../types/api";


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
