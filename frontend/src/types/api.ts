export type PaginatedResponse<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type HealthResponse = {
  status: string;
};

export type Branch = {
  id: number;
  code: string;
  name: string;
  city: string;
  country: string;
  is_active: boolean;
};

export type Location = {
  id: number;
  branch: number;
  branch_code: string;
  code: string;
  name: string;
  location_type: string;
  is_active: boolean;
};

export type Product = {
  id: number;
  sku: string;
  name: string;
  barcode: string | null;
  unit_of_measure: string;
  is_active: boolean;
};

export type InventoryItem = {
  id: number;
  branch: number;
  branch_code: string;
  location: number;
  location_code: string;
  product: number;
  product_sku: string;
  quantity_on_hand: string;
  quantity_reserved: string;
};

export type Order = {
  id: number;
  branch: number;
  branch_code: string;
  route_run: number | null;
  route_run_label: string | null;
  external_reference: string;
  customer_name: string;
  status: string;
  requested_ship_date: string | null;
};

export type RouteRun = {
  id: number;
  route: number;
  branch: number;
  route_code: string;
  route_name: string;
  branch_code: string;
  service_date: string;
  run_number: number;
  order_cutoff_time: string;
  sync_time: string;
  departure_time: string;
  status: string;
  orders_count: number;
  order_lines_count: number;
  picked_lines_count: number;
  pending_lines_count: number;
  has_pending_work: boolean;
  is_urgent: boolean;
  is_selectable: boolean;
  total_picking_tasks: number;
  open_picking_tasks: number;
  in_progress_picking_tasks: number;
  completed_picking_tasks: number;
  progress_percent: number;
  last_activity_at: string | null;
};

export type OrderLine = {
  id: number;
  order: number;
  order_reference: string;
  product: number;
  product_sku: string;
  product_name: string;
  line_number: number;
  quantity_ordered: string;
  quantity_picked: string;
  remaining_quantity: string;
  source_location_code: string | null;
  source_location_name: string | null;
};

export type PickingTask = {
  id: number;
  branch: number;
  branch_code: string;
  order_line: number;
  order_reference: string;
  product_sku: string;
  product_name: string;
  source_location: number;
  source_location_code: string;
  source_location_name: string;
  assigned_to: number | null;
  assigned_to_username: string | null;
  status: string;
  quantity_to_pick: string;
  quantity_picked: string;
  remaining_quantity: string;
};

export type ReturnBatch = {
  id: number;
  branch: number;
  branch_code: string;
  reference: string;
  status: string;
  received_at: string | null;
};

export type AuditLog = {
  id: number;
  actor: number | null;
  actor_username: string | null;
  action_type: string;
  entity_name: string;
  entity_id: string;
  message: string;
  created_at: string;
};

export type ScannerPickingScanResponse = {
  message: string;
  task: PickingTask;
  route_run: RouteRun;
};
