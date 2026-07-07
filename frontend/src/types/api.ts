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
  picked_picking_tasks: number;
  completed_picking_tasks: number;
  progress_percent: number;
  last_activity_at: string | null;
  is_ready_to_close: boolean;
  is_late: boolean;
  close_result: "on_time" | "late" | "unknown";
  ready_at: string | null;
  documents_printed_at: string | null;
  closed_at: string | null;
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
  quantity_prepared: string;
  remaining_quantity: string;
  remaining_to_prepare: string;
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
  route_run?: RouteRun;
  state?: "waiting_for_location" | "waiting_for_product" | "completed";
  confirmed_location_code?: string | null;
  current_instruction?: PickInstruction | null;
  cart_item?: ScannerCartItem;
};

export type ScannerSession = {
  id: number;
  cart: number;
  cart_code: string;
  cart_name: string;
  cart_work_session: number | null;
  picking_job: number | null;
  worker_code: string;
  status: string;
  started_at: string;
  ended_at: string | null;
};

export type ScannerSessionResponse = {
  message?: string;
  session: ScannerSession;
};

export type ScannerCartItem = {
  id: number;
  session: number;
  cart_code: string;
  route_run: number;
  route_code: string;
  picking_task: number;
  product: number;
  product_sku: string;
  product_barcode: string | null;
  product_name: string;
  order_reference: string;
  customer_name: string;
  quantity_picked: string;
  quantity_prepared: string;
  remaining_quantity: string;
  customer_label_ready: boolean;
  customer_label_scan_code: string | null;
};

export type ScannerCartItemsResponse = {
  session: ScannerSession;
  items: ScannerCartItem[];
};

export type ScannerControlTargetResponse = {
  product_sku: string;
  candidates: ScannerCartItem[];
};

export type ScannerPrintLabelResponse = {
  message: string;
  label: {
    id: number;
    scan_code: string;
    order_reference: string;
    printer_code: string;
    printed_at: string;
  };
};

export type ScannerInventoryPosition = {
  id: number;
  branch: number;
  branch_code: string;
  location: number;
  location_code: string;
  location_name: string;
  product: number;
  product_sku: string;
  product_barcode: string | null;
  product_name: string;
  quantity_on_hand: string;
  quantity_reserved: string;
};

export type ScannerProductLookupResponse = {
  product: {
    id: number;
    sku: string;
    barcode: string | null;
    name: string;
    description: string | null;
    image_url: string | null;
    unit_of_measure: string;
  };
  inventory_positions: ScannerInventoryPosition[];
};

export type ScannerLocationContentsResponse = {
  location: {
    id: number;
    branch: number;
    branch_code: string;
    code: string;
    name: string;
    location_type: string;
  };
  inventory_items: ScannerInventoryPosition[];
};

export type ScannerContentsItem = {
  product_id: number;
  sku: string;
  name: string;
  quantity: number;
  reserved_quantity?: number;
  expected_quantity?: number;
  received_quantity?: number;
  picked_quantity?: number;
  prepared_quantity?: number;
  remaining_quantity?: number;
  missing_quantity?: number;
  discrepancy_type?: string | null;
  order_reference?: string;
  customer_name?: string;
};

export type ScannerContentsResponse = {
  object_type: "location" | "cart" | "customer_label" | "pallet";
  code: string;
  title: string;
  status: string;
  description: string;
  discrepancy_reference?: string | null;
  items: ScannerContentsItem[];
};

export type ScannerQuickTransferResponse = {
  message: string;
  movement_id: number;
  source_inventory: ScannerInventoryPosition;
  target_inventory: ScannerInventoryPosition;
};

export type TransferPalletManifestItem = {
  id: number;
  product: number;
  product_sku: string;
  product_barcode: string | null;
  product_name: string;
  expected_quantity: number;
  received_quantity: number;
  remaining_quantity: number;
};

export type ScannerReceivingSession = {
  id: number;
  session_id: number;
  status: string;
  worker_code: string;
  state: "waiting_for_product" | "waiting_for_location";
  pallet: {
    id: number;
    scan_code: string;
    status: string;
    source_branch_code: string;
    destination_branch_code: string;
    transfer_reference: string;
  };
  summary: {
    lines: number;
    expected_quantity: number;
    received_quantity: number;
    remaining_quantity: number;
  };
  pending: {
    pallet_item: number;
    product_sku: string;
    product_name: string;
    quantity: number;
  } | null;
  current_item: {
    pallet_item: number;
    product_sku: string;
    product_name: string;
    quantity: number;
  } | null;
  pending_quantity: number | null;
  discrepancy: TransferDiscrepancySummary | null;
  manifest: TransferPalletManifestItem[];
};

export type ScannerReceivingResponse = {
  message?: string;
  result?: "exact" | "discrepancy";
  receiving_session: ScannerReceivingSession;
};

export type TransferDiscrepancySummary = {
  id: number;
  reference: string;
  status: string;
  line_count: number;
  total_discrepancy_quantity: number;
  items: TransferDiscrepancySummaryItem[];
};

export type TransferDiscrepancySummaryItem = {
  id: number;
  product: number;
  product_sku: string;
  product_name: string;
  discrepancy_type: string;
  expected_quantity: number;
  received_quantity: number;
  difference_quantity: number;
  discrepancy_quantity: number;
};

export type TransferDiscrepancyScanHistory = {
  id: number;
  product_sku: string;
  destination_location_code: string;
  quantity: string;
  worker_code: string;
  scanned_at: string;
};

export type TransferDiscrepancyItem = {
  id: number;
  pallet_item: number;
  product: number;
  product_sku: string;
  product_name: string;
  discrepancy_type: string;
  expected_quantity: string;
  received_quantity: string;
  difference_quantity: string;
  discrepancy_quantity: string;
  scan_history: TransferDiscrepancyScanHistory[];
};

export type TransferDiscrepancy = {
  id: number;
  reference: string;
  pallet: number;
  pallet_code: string;
  transfer: number;
  transfer_reference: string;
  source_branch_code: string;
  destination_branch_code: string;
  status: string;
  created_by_worker_code: string;
  notes: string;
  closed_at: string | null;
  line_count: number;
  total_discrepancy_quantity: string;
  items: TransferDiscrepancyItem[];
  created_at: string;
  updated_at: string;
};

export type ScannerProforma = {
  id: number;
  route_code: string;
  route_name: string;
  branch: number;
  branch_code: string;
  run_number: number;
  status: string;
  departure_time: string;
  akt: number;
  lines: number;
  started: number;
  picked: number;
  prepared: number;
  is_selectable: boolean;
};

export type PickingJobRoute = {
  id: number;
  route_code: string;
  route_name: string;
  branch_code: string;
  run_number: number;
  departure_time: string;
};

export type PickingJob = {
  id: number;
  status: string;
  mode: string;
  routes: PickingJobRoute[];
  total_lines: number;
  remaining_lines: number;
  total_quantity: string;
  picked_quantity: string;
  prepared_quantity: string;
  progress_percent: number;
  assigned_cart_code: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
};

export type CartWorkSession = {
  id: number;
  cart: number;
  cart_code: string;
  confirmed_location: number | null;
  confirmed_location_code: string | null;
  picking_job: PickingJob;
  scanner_session: ScannerSession | null;
  status: string;
  started_at: string;
  finished_at: string | null;
};

export type PickInstruction = {
  picking_task_id: number;
  route_run_id: number;
  location: {
    id: number;
    code: string;
    name: string;
  };
  product: {
    id: number;
    sku: string;
    barcode: string | null;
    name: string;
  };
  required_quantity: string;
  picked_quantity: string;
  remaining_quantity: string;
};

export type ScannerProformasResponse = {
  results: ScannerProforma[];
};

export type ScannerJobsResponse = {
  results: PickingJob[];
};

export type ScannerCreateJobsResponse = {
  message: string;
  jobs: PickingJob[];
};

export type ScannerTaskStartResponse = {
  message: string;
  job: PickingJob;
  cart_work_session: CartWorkSession;
  session: ScannerSession;
};

export type ScannerCartWorkResponse = {
  message?: string;
  state?: "waiting_for_location" | "waiting_for_product" | "completed";
  confirmed_location_code?: string | null;
  cart_work_session: CartWorkSession;
  current_instruction?: PickInstruction | null;
  session?: ScannerSession;
  tasks?: PickingTask[];
};

export type ScannerControlCartResponse = {
  session: ScannerSession;
  cart_work_session: CartWorkSession;
  items: ScannerCartItem[];
};
