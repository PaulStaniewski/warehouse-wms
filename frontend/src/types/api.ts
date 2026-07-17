export type PaginatedResponse<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type HealthResponse = {
  status: string;
};

export type AuthSession = {
  is_authenticated: boolean;
  username: string | null;
  is_superuser: boolean;
};

export type Branch = {
  id: number;
  code: string;
  name: string;
  city: string;
  country: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type BranchMembership = {
  branch_id: number;
  branch_code: string;
  branch_name: string;
  branch_city: string;
  branch_country: string;
  role: "worker" | "leader";
  role_label: string;
};

export type Location = {
  id: number;
  branch: number;
  branch_code: string;
  code: string;
  name: string;
  location_type: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
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
  customer_alias: string;
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
  product_brand: string;
  product_description: string;
  product_image_url: string;
  source_location: number;
  source_location_code: string;
  source_location_name: string;
  assigned_to: number | null;
  assigned_to_username: string | null;
  status: string;
  quantity_to_pick: string;
  quantity_picked: string;
  shortage_quantity: string;
  quantity_prepared: string;
  remaining_quantity: string;
  remaining_to_prepare: string;
  is_replacement_pick?: boolean;
  replacement_shortage_reference?: string | null;
  original_shortage_location_code?: string | null;
  is_system_reallocated_pick?: boolean;
  reallocation_reason?: string | null;
  reallocated_from_location_code?: string | null;
  claim_status?: string | null;
  claimed_by?: number | null;
  claimed_by_username?: string | null;
  is_claimed_by_current_user?: boolean;
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
  actor_display: string;
  action_type: string;
  event_type: string;
  branch: number | null;
  branch_code: string | null;
  product: number | null;
  product_sku: string | null;
  product_name: string | null;
  quantity: string | null;
  expected_quantity: string | null;
  checked_quantity: string | null;
  source_location: number | null;
  source_location_code: string | null;
  destination_location: number | null;
  destination_location_code: string | null;
  source_label: string;
  destination_label: string;
  cart: number | null;
  cart_code: string | null;
  order: number | null;
  order_reference: string | null;
  route_run: number | null;
  route_run_label: string | null;
  transfer: number | null;
  transfer_reference: string | null;
  pallet: number | null;
  pallet_code: string | null;
  discrepancy: number | null;
  discrepancy_reference: string | null;
  result: string;
  reference: string;
  entity_name: string;
  entity_id: string;
  message: string;
  created_at: string;
};

export type StockMovement = {
  id: number;
  branch: number;
  branch_code: string;
  product: number;
  product_sku: string;
  product_name: string;
  inventory_item: number | null;
  source_location: number | null;
  source_location_code: string | null;
  destination_location: number | null;
  destination_location_code: string | null;
  movement_type: string;
  movement_type_label: string;
  adjustment_direction: "increase" | "decrease" | "unknown" | null;
  adjustment_location: number | null;
  adjustment_location_code: string | null;
  quantity: string;
  reference: string;
  performed_by: number | null;
  performed_by_username: string | null;
  status: "completed";
  origin: string;
  created_at: string;
  updated_at: string;
};

export type TransferDiscrepancyAction = {
  action_type: string;
  action_label: string;
  target_type: string;
  target_reference: string;
  target_url: string;
  discrepancy_reference: string;
  transfer_reference: string;
  pallet_reference: string;
  source_branch: string;
  destination_branch: string;
  route: string;
  route_label: string;
  current_status: string;
  current_status_label: string;
  confirmed_shortage_quantity: string;
  waiting_since: string;
  created_at: string;
};

export type ScannerPickingScanResponse = {
  message: string;
  task: PickingTask;
  route_run?: RouteRun;
  state?: "waiting_for_location" | "waiting_for_product" | "waiting_for_available_line" | "completed";
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
  posted_to_unconfirmed_quantity?: number;
  recovered_quantity?: number;
  confirmed_shortage_quantity?: number;
  investigation_remaining_quantity?: number;
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
  discrepancy_status?: string | null;
  report_printed?: boolean;
  shortage_posted?: boolean;
  source_review?: TransferDiscrepancySourceReviewSummary | null;
  reconciliation?: TransferDiscrepancyReconciliationSummary | null;
  source_stock_verification?: TransferDiscrepancySourceStockVerificationSummary | null;
  transit_investigation?: TransferDiscrepancyTransitInvestigationSummary | null;
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

export type InterBranchMMTask = {
  pallet_id: number;
  pallet_code: string;
  transfer_id: number;
  transfer_reference: string;
  source_branch: string;
  destination_branch: string;
  arrived_at: string;
  expected_units: number;
  put_away_units: number;
  remaining_units: number;
  line_count: number;
  status: "waiting_for_receiving" | "receiving";
  arrival_result?: "registered" | "already_registered";
};

export type InterBranchArrivalResponse = {
  message: string;
  arrival: InterBranchMMTask;
};

export type TransferDiscrepancySummary = {
  id: number;
  reference: string;
  status: string;
  report_printed_at: string | null;
  report_print_count: number;
  last_report_printer_code: string;
  shortage_posted_at: string | null;
  resolved_at: string | null;
  resolved_by_worker_code: string;
  confirmed_shortage_at: string | null;
  confirmed_shortage_by_worker_code: string;
  line_count: number;
  total_discrepancy_quantity: number;
  total_posted_to_unconfirmed_quantity: number;
  total_recovered_quantity: number;
  total_confirmed_shortage_quantity: number;
  total_remaining_quantity: number;
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
  posted_to_unconfirmed_quantity: number;
  recovered_quantity: number;
  confirmed_shortage_quantity: number;
  remaining_quantity: number;
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
  posted_to_unconfirmed_quantity: string;
  posted_to_unconfirmed_at: string | null;
  recovered_quantity: string;
  last_recovered_at: string | null;
  confirmed_shortage_quantity: string;
  last_confirmed_shortage_at: string | null;
  remaining_quantity: string;
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
  report_printed_at: string | null;
  report_print_count: number;
  last_report_printer_code: string;
  shortage_posted_at: string | null;
  resolved_at: string | null;
  resolved_by_worker_code: string;
  confirmed_shortage_at: string | null;
  confirmed_shortage_by_worker_code: string;
  line_count: number;
  total_discrepancy_quantity: string;
  total_posted_to_unconfirmed_quantity: string;
  total_recovered_quantity: string;
  total_confirmed_shortage_quantity: string;
  total_remaining_quantity: string;
  items: TransferDiscrepancyItem[];
  recoveries: TransferDiscrepancyRecovery[];
  shortage_confirmations: TransferDiscrepancyShortageConfirmation[];
  source_review: TransferDiscrepancySourceReviewSummary | null;
  reconciliation: TransferDiscrepancyReconciliationSummary | null;
  created_at: string;
  updated_at: string;
};

export type TransferDiscrepancySourceReviewSummary = {
  id: number;
  reference: string;
  status: string;
  finding: string;
  finding_display: string;
  completed_at: string | null;
};

export type TransferDiscrepancyReconciliationSummary = {
  id: number;
  reference: string;
  route: string;
  route_label: string;
  status: string;
  status_label?: string;
  next_action_label: string;
  manual_decision_required?: boolean;
  manual_decision?: TransferDiscrepancyManualDecision | null;
  source_stock_verification?: TransferDiscrepancySourceStockVerificationSummary | null;
  transit_investigation?: TransferDiscrepancyTransitInvestigationSummary | null;
};

export type TransferDiscrepancyPrintResponse = {
  message: string;
  first_print: boolean;
  posted_quantity: string;
  discrepancy: TransferDiscrepancy;
};

export type TransferDiscrepancyRecovery = {
  id: number;
  product_sku: string;
  product_name: string;
  quantity: string;
  source_location_code: string;
  destination_location_code: string;
  worker_code: string;
  recovered_at: string;
  client_operation_id: string;
};

export type TransferDiscrepancyShortageConfirmation = {
  id: number;
  product_sku: string;
  product_name: string;
  quantity: string;
  unconfirmed_location_code: string;
  worker_code: string;
  confirmed_at: string;
  client_operation_id: string;
};

export type TransferDiscrepancyRecoverResponse = {
  message: string;
  recovery: {
    discrepancy_reference: string;
    status: string;
    product_code: string;
    recovered_quantity: string;
    line_recovered_quantity: string;
    line_confirmed_shortage_quantity: string;
    line_remaining_quantity: string;
    total_remaining_quantity: string;
    destination_location_code: string;
    recovery_id: number;
  };
};

export type TransferDiscrepancyConfirmShortageResponse = {
  message: string;
  confirmation: {
    discrepancy_reference: string;
    status: string;
    product_code: string;
    confirmed_quantity: string;
    line_recovered_quantity: string;
    line_confirmed_shortage_quantity: string;
    line_remaining_quantity: string;
    total_recovered_quantity: string;
    total_confirmed_shortage_quantity: string;
    total_remaining_quantity: string;
    unconfirmed_location_code: string;
    confirmation_id: number;
  };
};

export type TransferDiscrepancySourceReviewLine = {
  id: number;
  product_sku: string;
  product_name: string;
  expected_quantity: string;
  received_quantity: string;
  missing_quantity: string;
  recovered_quantity: string;
  confirmed_shortage_quantity: string;
  remaining_quantity: string;
};

export type TransferDiscrepancySourceReviewEvidence = {
  product_sku: string;
  product_name: string;
  expected_quantity?: string;
  quantity?: string;
  pallet_code?: string;
  released_at?: string | null;
  destination_location_code?: string;
  worker_code?: string;
  scanned_at?: string;
};

export type TransferDiscrepancySourceReview = {
  id: number;
  reference: string;
  status: string;
  finding: string;
  finding_display: string;
  started_at: string | null;
  started_by_worker_code: string;
  completed_at: string | null;
  completed_by_worker_code: string;
  finding_note: string;
  created_at: string;
  updated_at: string;
  discrepancy: number;
  discrepancy_reference: string;
  discrepancy_status: string;
  discrepancy_created_at: string;
  discrepancy_confirmed_shortage_at: string | null;
  discrepancy_confirmed_shortage_by_worker_code: string;
  transfer_reference: string;
  source_branch: number;
  source_branch_code: string;
  source_branch_name: string;
  destination_branch_code: string;
  destination_branch_name: string;
  pallet_code: string;
  pallet_closed_at: string | null;
  total_expected_quantity: string;
  total_received_quantity: string;
  total_missing_quantity: string;
  total_posted_to_unconfirmed_quantity: string;
  total_recovered_quantity: string;
  total_confirmed_shortage_quantity: string;
  total_remaining_quantity: string;
  lines: TransferDiscrepancySourceReviewLine[];
  source_dispatch_evidence: TransferDiscrepancySourceReviewEvidence[];
  destination_receiving_evidence: TransferDiscrepancySourceReviewEvidence[];
  recoveries: TransferDiscrepancyRecovery[];
  shortage_confirmations: TransferDiscrepancyShortageConfirmation[];
  reconciliation: TransferDiscrepancyReconciliationSummary | null;
};

export type TransferDiscrepancySourceReviewResponse = {
  message: string;
  source_review: TransferDiscrepancySourceReview;
  reconciliation_id?: number | null;
};

export type TransferDiscrepancyReconciliationLine = {
  id: number;
  product_sku: string;
  product_name: string;
  missing_quantity: string;
  recovered_quantity: string;
  confirmed_shortage_quantity: string;
  remaining_quantity: string;
};

export type TransferDiscrepancyReconciliation = {
  id: number;
  reference: string;
  route: string;
  route_label: string;
  status: string;
  status_label: string;
  next_action_label: string;
  manual_decision_required: boolean;
  manual_decision: TransferDiscrepancyManualDecision | null;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
  acknowledged_by_worker_code: string;
  completed_at: string | null;
  completed_by_worker_code: string;
  source_stock_verification: TransferDiscrepancySourceStockVerificationSummary | null;
  transit_investigation: TransferDiscrepancyTransitInvestigationSummary | null;
  discrepancy: number;
  discrepancy_reference: string;
  discrepancy_status: string;
  discrepancy_confirmed_shortage_at: string | null;
  discrepancy_confirmed_shortage_by_worker_code: string;
  source_review: number;
  source_review_reference: string;
  source_review_status: string;
  source_review_finding: string;
  source_review_finding_display: string;
  source_review_finding_note: string;
  source_review_completed_at: string | null;
  source_review_completed_by_worker_code: string;
  transfer_reference: string;
  source_branch_code: string;
  source_branch_name: string;
  destination_branch_code: string;
  destination_branch_name: string;
  pallet_code: string;
  total_posted_to_unconfirmed_quantity: string;
  total_recovered_quantity: string;
  total_confirmed_shortage_quantity: string;
  total_remaining_quantity: string;
  lines: TransferDiscrepancyReconciliationLine[];
};

export type TransferDiscrepancyManualDecision = {
  id: number;
  outcome: string;
  outcome_label: string;
  decision_note: string;
  decided_at: string;
  decided_by_worker_code: string;
};

export type TransferDiscrepancyReconciliationResponse = {
  message: string;
  reconciliation: TransferDiscrepancyReconciliation;
  source_stock_verification_id?: number | null;
  source_stock_verification_created?: boolean;
  transit_investigation_id?: number | null;
  transit_investigation_created?: boolean;
  manual_decision?: TransferDiscrepancyManualDecision;
};

export type TransferDiscrepancyTransitInvestigationSummary = {
  id: number;
  reference: string;
  status: string;
  status_label: string;
  finding: string;
  finding_label: string;
  finding_note?: string;
  started_at: string | null;
  started_by_worker_code: string;
  completed_at: string | null;
  completed_by_worker_code: string;
};

export type TransitRouteEvidence = {
  label: string;
  reference: string;
  timestamp: string | null;
};

export type TransferDiscrepancyTransitInvestigation = TransferDiscrepancyTransitInvestigationSummary & {
  next_action_label: string;
  created_at: string;
  updated_at: string;
  completion_operation_id: string | null;
  reconciliation: number;
  reconciliation_reference: string;
  reconciliation_status: string;
  reconciliation_status_label: string;
  reconciliation_route: string;
  reconciliation_route_label: string;
  reconciliation_manual_decision: TransferDiscrepancyManualDecision | null;
  source_review_reference: string;
  source_review_finding: string;
  source_review_finding_display: string;
  source_review_finding_note: string;
  discrepancy_reference: string;
  discrepancy_status: string;
  transfer_reference: string;
  transfer_status: string;
  source_branch_code: string;
  source_branch_name: string;
  destination_branch_code: string;
  destination_branch_name: string;
  pallet_code: string;
  pallet_status: string;
  transfer_summary: Record<string, string | null>;
  source_dispatch_evidence: TransferDiscrepancySourceReviewEvidence[];
  transit_route_evidence: TransitRouteEvidence[];
  destination_receiving_evidence: TransferDiscrepancySourceReviewEvidence[];
  destination_investigation_outcome: {
    discrepancy_reference: string;
    discrepancy_status: string;
    posted_to_unconfirmed: string;
    destination_recovered: string;
    confirmed_shortage: string;
    destination_remaining: string;
    recoveries: TransferDiscrepancyRecovery[];
    shortage_confirmations: TransferDiscrepancyShortageConfirmation[];
  };
  final_accounting_lines: TransferDiscrepancyReconciliationLine[];
};

export type TransferDiscrepancyTransitInvestigationResponse = {
  message: string;
  transit_investigation: TransferDiscrepancyTransitInvestigation;
};

export type TransferDiscrepancySourceStockVerificationSummary = {
  id: number;
  reference: string;
  status: string;
  status_label?: string;
  total_target_quantity: string | number;
  total_found_quantity: string | number;
  total_remaining_quantity: string | number;
  total_unresolved_quantity: string | number;
  search_completed_at?: string | null;
  search_completed_by_worker_code?: string;
  search_completion_note?: string;
};

export type TransferDiscrepancySourceStockVerificationItem = {
  id: number;
  product_sku: string;
  product_name: string;
  target_quantity: string;
  found_quantity: string;
  remaining_quantity: string;
  unresolved_quantity: string;
  last_found_at: string | null;
};

export type TransferDiscrepancySourceStockRecovery = {
  id: number;
  product_sku: string;
  product_name: string;
  quantity: string;
  destination_location_code: string;
  destination_location_name: string;
  worker_code: string;
  recovered_at: string;
  client_operation_id: string;
};

export type TransferDiscrepancySourceStockVerification = {
  id: number;
  reference: string;
  status: string;
  status_label: string;
  next_action_label: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  started_by_worker_code: string;
  completed_at: string | null;
  completed_by_worker_code: string;
  search_completed_at: string | null;
  search_completed_by_worker_code: string;
  search_completion_note: string;
  search_completion_operation_id: string | null;
  reconciliation: number;
  reconciliation_reference: string;
  reconciliation_status: string;
  reconciliation_status_label: string;
  reconciliation_route: string;
  reconciliation_route_label: string;
  reconciliation_manual_decision: TransferDiscrepancyManualDecision | null;
  source_review_reference: string;
  source_review_finding: string;
  source_review_finding_display: string;
  discrepancy_reference: string;
  discrepancy_status: string;
  transfer_reference: string;
  source_branch_code: string;
  source_branch_name: string;
  destination_branch_code: string;
  destination_branch_name: string;
  pallet_code: string;
  total_target_quantity: string;
  total_found_quantity: string;
  total_remaining_quantity: string;
  total_unresolved_quantity: string;
  items: TransferDiscrepancySourceStockVerificationItem[];
  recoveries: TransferDiscrepancySourceStockRecovery[];
};

export type TransferDiscrepancySourceStockVerificationResponse = {
  message: string;
  verification: TransferDiscrepancySourceStockVerification;
};

export type TransferDiscrepancySourceStockRecoveryResponse = {
  message: string;
  recovery: {
    verification_reference: string;
    verification_status: string;
    reconciliation_reference: string;
    reconciliation_status: string;
    product_code: string;
    found_quantity: string;
    line_found_quantity: string;
    line_remaining_quantity: string;
    total_found_quantity: string;
    total_remaining_quantity: string;
    destination_location_code: string;
    recovery_id: number;
  };
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
  cart_work_session: number | null;
  active_workers: string[];
  active_workers_count: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
};

export type CartWorkParticipant = {
  id: number;
  user: number;
  username: string;
  display_name: string;
  branch: number;
  branch_code: string;
  status: string;
  picking_direction: string;
  picking_direction_label: string;
  participant_work_state: string;
  participant_work_state_label: string;
  is_current_user: boolean;
  current_picking_task: number | null;
  current_product_sku: string | null;
  current_product_name: string | null;
  current_location_code: string | null;
  confirmed_location: number | null;
  confirmed_location_code: string | null;
  joined_at: string;
  last_seen_at: string;
  left_at: string | null;
};

export type CartWorkSession = {
  id: number;
  cart: number;
  cart_code: string;
  confirmed_location: number | null;
  confirmed_location_code: string | null;
  picking_job: PickingJob;
  scanner_session: ScannerSession | null;
  participants: CartWorkParticipant[];
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
  order_reference: string;
  product: {
    id: number;
    sku: string;
    barcode: string | null;
    name: string;
    brand: string;
    description: string;
    image_url: string;
  };
  required_quantity: string;
  picked_quantity: string;
  shortage_quantity: string;
  remaining_quantity: string;
  customer_alias: string;
};

export type PickingShortageChallenge = {
  confirmation_code: string;
  challenge_token: string;
  expires_at: string;
  summary: {
    picking_task_id: number;
    product_sku: string;
    product_name: string;
    product_brand: string;
    branch_code: string;
    location_code: string;
    order_reference: string;
    customer_alias: string;
    cart_code: string;
    required_quantity: string;
    picked_quantity: string;
    shortage_quantity: string;
  };
};

export type ScannerPickingShortageResponse = {
  message: string;
  shortage: {
    id: number;
    reference: string;
    quantity: string;
    location_missing_quantity: string;
    alternative_allocated_quantity: string;
    customer_unfulfilled_quantity: string;
    unresolved_unconfirmed_quantity: string;
    status: string;
    product_sku: string;
    reported_location_code: string;
    unconfirmed_location_code: string;
    allocations: PickingShortageAllocation[];
  };
  alternative_allocations: PickingShortageAllocation[];
  replenishment_request: {
    id: number;
    reference: string;
    status: string;
    quantity: string;
  } | null;
  task: PickingTask;
  picking_job: PickingJob;
  cart_work_session: CartWorkSession;
  state: "waiting_for_location" | "waiting_for_product" | "waiting_for_available_line" | "completed";
  confirmed_location_code?: string | null;
  current_instruction?: PickInstruction | null;
};

export type PickingShortageAllocation = {
  id: number;
  location?: number;
  location_code: string;
  location_name?: string;
  quantity: string;
  picked_quantity: string;
  status: string;
  status_label?: string;
  replacement_picking_task: number;
};

export type PickingShortage = {
  id: number;
  reference: string;
  branch_code: string;
  product_sku: string;
  product_name: string;
  product_brand: string;
  quantity: string;
  location_missing_quantity: string;
  alternative_allocated_quantity: string;
  customer_unfulfilled_quantity: string;
  recovered_quantity: string;
  confirmed_missing_quantity: string;
  unresolved_quantity: string;
  unresolved_unconfirmed_quantity: string;
  allocations: PickingShortageAllocation[];
  replenishment_reference: string | null;
  replenishment_quantity: string | null;
  reported_location_code: string;
  unconfirmed_location_code: string;
  found_location_code: string | null;
  cart_code: string | null;
  order_reference: string;
  customer_alias_snapshot: string;
  reported_by_username: string | null;
  reported_by_worker_code: string;
  reported_at: string;
  status: string;
  status_label: string;
};

export type ReplenishmentRequest = {
  id: number;
  reference: string;
  shortage_reference: string;
  branch_code: string;
  customer_alias: string;
  order_reference: string;
  product_sku: string;
  product_name: string;
  product_brand: string;
  quantity: string;
  reason: string;
  reason_label: string;
  status: string;
  status_label: string;
  external_system: string;
  external_reference: string;
  cart_code: string | null;
  reported_location_code: string;
  reported_by_worker_code: string;
  reported_at: string;
  created_at: string;
  ordered_at: string | null;
  note: string;
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
  participant?: CartWorkParticipant | null;
};

export type ScannerCartWorkResponse = {
  message?: string;
  state?: "waiting_for_location" | "waiting_for_product" | "waiting_for_available_line" | "completed";
  confirmed_location_code?: string | null;
  cart_work_session: CartWorkSession;
  current_instruction?: PickInstruction | null;
  participant?: CartWorkParticipant | null;
  repair_messages?: string[];
  session?: ScannerSession;
  tasks?: PickingTask[];
};

export type ScannerControlCartResponse = {
  session: ScannerSession;
  cart_work_session: CartWorkSession;
  items: ScannerCartItem[];
};
