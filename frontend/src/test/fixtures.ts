import type {
  AuthSession,
  BranchMembership,
  InventoryExceptionSummary,
  PaginatedResponse,
  TransportOverview,
} from "../types/api";

export function paginated<T>(results: T[] = [], count = results.length): PaginatedResponse<T> {
  return {
    count,
    next: null,
    previous: null,
    results,
  };
}

export function authSession(username = "GDY_LEADER"): AuthSession {
  return {
    is_authenticated: true,
    is_superuser: false,
    username,
  };
}

export function anonymousSession(): AuthSession {
  return {
    is_authenticated: false,
    is_superuser: false,
    username: null,
  };
}

export function branchMembership(role: BranchMembership["role"] = "leader", code = "GDY"): BranchMembership {
  return {
    branch_city: code === "GDY" ? "Gdynia" : "Gdansk",
    branch_code: code,
    branch_country: "Poland",
    branch_id: code === "GDY" ? 1 : 2,
    branch_name: code === "GDY" ? "Magazyn Gdynia" : "Gdansk",
    role,
    role_label: role === "leader" ? "Leader" : "Worker",
  };
}

export function inventoryExceptionSummary(): InventoryExceptionSummary {
  return {
    active_categories: 0,
    categories: [],
    immediate_attention: [],
    leader_only_count: 0,
    oldest_waiting_since: null,
    total_actionable: 0,
  };
}

export function transportOverview(): TransportOverview {
  return {
    active_routes: [],
    attention_items: [],
    summary: {
      active_route_runs: 0,
      pallets_awaiting_receipt: 0,
      preparing_route_runs: 0,
      ready_to_close_route_runs: 0,
      transit_investigations: 0,
      transfers_in_transit: 0,
      unresolved_discrepancy_transfers: 0,
    },
  };
}
