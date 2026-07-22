import {
  Ban,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  PackageCheck,
  Printer,
  RefreshCw,
  Route,
  Send,
  Shuffle,
  SlidersHorizontal,
  Truck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import type { AxiosError } from "axios";

import { useActiveBranch } from "../api/ActiveBranchContext";
import {
  useActivateShipment,
  useCancelShipment,
  useChangeShipmentRoute,
  useChangeShipmentStatus,
  useCloseShipmentRoute,
  useConfirmShipmentPickingRoute,
  usePostShipmentDocuments,
  usePostShipmentPickingLists,
  usePrepareShipment,
  usePrintShipmentDocuments,
  usePrintShipmentProforma,
  useRemoveShipmentLineQuantity,
  useShipment,
  useShipmentRouteTargets,
  useShipments,
} from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import type { Shipment, ShipmentLine, ShipmentRouteTarget } from "../types/api";

const SHIPMENT_STATUSES = [
  "",
  "pending_activation",
  "active",
  "picking",
  "picked",
  "controlled",
  "prepared",
  "documents_posted",
  "ready_for_dispatch",
  "dispatched",
  "completed",
  "cancelled",
  "exception",
];

const PICKING_STATUSES = ["", "not_started", "in_progress", "completed", "shortage"];
const MANUAL_NEXT_STATUSES = ["active", "exception", "cancelled"];

type DialogMode = "cancel" | "change_route" | "change_status" | "remove_quantity" | null;

type ShipmentCommand =
  | {
      description: string;
      dialog: Exclude<DialogMode, null>;
      icon: LucideIcon;
      key: string;
      title: string;
    }
  | {
      description: string;
      icon: LucideIcon;
      key: string;
      run: () => Promise<{ message: string }> | null;
      title: string;
    };

function label(value: string | null | undefined) {
  if (!value) return "-";
  return value.replaceAll("_", " ");
}

function dateTime(value: string | null | undefined) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(new Date(value));
}

function dateOnly(value: string | null | undefined) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value));
}

function statusTone(value: string): "ok" | "error" | "loading" {
  if (["completed", "prepared", "controlled", "posted", "documents_posted"].includes(value)) return "ok";
  if (["cancelled", "exception", "shortage", "blocked"].includes(value)) return "error";
  return "loading";
}

function targetById(targets: ShipmentRouteTarget[], id: string) {
  return targets.find((target) => String(target.id) === id) ?? null;
}

function errorMessage(error: unknown) {
  const axiosError = error as AxiosError<{ detail?: string; non_field_errors?: string[] }>;
  return axiosError.response?.data?.detail ?? axiosError.response?.data?.non_field_errors?.join(" ") ?? axiosError.message ?? "Action failed.";
}

type CommandTileProps = {
  description: string;
  disabledReason?: string;
  icon: LucideIcon;
  isEnabled: boolean;
  isPending?: boolean;
  onClick: () => void;
  title: string;
};

function CommandTile({ description, disabledReason, icon: Icon, isEnabled, isPending, onClick, title }: CommandTileProps) {
  const disabled = !isEnabled || Boolean(isPending);
  return (
    <button
      aria-disabled={disabled}
      className="shipment-command-tile"
      disabled={disabled}
      onClick={onClick}
      title={!isEnabled ? disabledReason : undefined}
      type="button"
    >
      <Icon aria-hidden="true" size={16} />
      <span>
        <strong>{isPending ? "Working..." : title}</strong>
        <small>{isEnabled ? description : disabledReason || description}</small>
      </span>
    </button>
  );
}

function Definition({ label: title, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="definition-row">
      <dt>{title}</dt>
      <dd>{value || "-"}</dd>
    </div>
  );
}

function CommandDialog({
  children,
  error,
  isPending,
  onClose,
  onSubmit,
  submitLabel,
  title,
}: {
  children: ReactNode;
  error: string;
  isPending: boolean;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  submitLabel: string;
  title: string;
}) {
  return (
    <div className="shipment-dialog-backdrop" role="presentation">
      <form aria-labelledby="shipment-dialog-title" className="shipment-dialog" onSubmit={onSubmit}>
        <h2 id="shipment-dialog-title">{title}</h2>
        {children}
        {error && <div className="shipment-error-alert">{error}</div>}
        <footer>
          <button disabled={isPending} type="button" onClick={onClose}>
            Back
          </button>
          <button disabled={isPending} type="submit">
            {isPending ? "Working..." : submitLabel}
          </button>
        </footer>
      </form>
    </div>
  );
}

function ShipmentDetail({
  onSelectLine,
  selectedLineId,
  shipment,
}: {
  onSelectLine: (line: ShipmentLine) => void;
  selectedLineId: number | null;
  shipment: Shipment;
}) {
  const selectedLine = shipment.lines.find((line) => line.id === selectedLineId) ?? null;

  return (
    <section className="shipment-detail-grid">
      <div className="panel shipment-summary-panel">
        <h2>Shipment Summary</h2>
        <dl>
          <Definition label="Reference" value={shipment.reference} />
          <Definition label="External ref" value={shipment.external_reference} />
          <Definition label="Branch" value={shipment.branch_code} />
          <Definition label="Route" value={shipment.route_code ? `${shipment.route_code} ${shipment.route_time || ""}` : "-"} />
          <Definition label="Cutoff" value={shipment.cutoff_time} />
          <Definition label="Delivery" value={dateOnly(shipment.delivery_date)} />
          <Definition label="Customer" value={shipment.customer_name} />
          <Definition label="Account" value={shipment.external_customer_account} />
          <Definition label="Payment" value={shipment.payment_method} />
          <Definition label="Shipment" value={label(shipment.status)} />
          <Definition label="Picking" value={label(shipment.picking_status)} />
          <Definition label="Control" value={label(shipment.control_status)} />
          <Definition label="Documents" value={label(shipment.document_status)} />
          <Definition label="Imported" value={dateTime(shipment.external_created_at)} />
          <Definition label="Prepared by" value={shipment.prepared_by_username} />
        </dl>
        {shipment.route_assignments.length > 0 && (
          <div className="route-history-box">
            <strong>Route history</strong>
            {shipment.route_assignments.slice(0, 3).map((assignment) => (
              <p key={assignment.id}>
                {assignment.previous_route_label || "Unassigned"} {"->"} {assignment.new_route_label}
              </p>
            ))}
          </div>
        )}
      </div>

      <div className="panel shipment-lines-panel">
        <h2>Shipment Lines</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Line</th>
                <th>Product Code</th>
                <th>Product Name</th>
                <th>Original</th>
                <th>Effective</th>
                <th>Removed</th>
                <th>Picked</th>
                <th>Controlled</th>
                <th>Prepared</th>
                <th>Remaining</th>
                <th>Max Remove</th>
                <th>Operational State</th>
                <th>Source Location</th>
                <th>External Line ID</th>
              </tr>
            </thead>
            <tbody>
              {shipment.lines.map((line) => (
                <tr
                  className={selectedLineId === line.id ? "selected-row" : ""}
                  key={line.id}
                  onClick={() => onSelectLine(line)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") onSelectLine(line);
                  }}
                  tabIndex={0}
                >
                  <td>{line.line_number}</td>
                  <td className="mono">{line.product_sku}</td>
                  <td>{line.product_name}</td>
                  <td>{line.original_ordered_quantity}</td>
                  <td>{line.effective_quantity}</td>
                  <td>{line.removed_quantity}</td>
                  <td>{line.picked_quantity}</td>
                  <td>{line.controlled_quantity}</td>
                  <td>{line.prepared_quantity}</td>
                  <td>{line.remaining_to_pick}</td>
                  <td>{line.maximum_removable_quantity}</td>
                  <td title={line.blocking_reason}><StatusBadge label={label(line.operational_line_state)} tone={statusTone(line.operational_line_state)} /></td>
                  <td>{line.source_location_code || <span className="muted">-</span>}</td>
                  <td className="mono">{line.external_line_reference || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {selectedLine && selectedLine.quantity_adjustments.length > 0 && (
          <div className="shipment-adjustment-history">
            <strong>Removal history</strong>
            {selectedLine.quantity_adjustments.map((adjustment) => (
              <p key={adjustment.id}>
                {adjustment.quantity_removed} removed by {adjustment.adjusted_by_username || "-"} / effective {adjustment.previous_effective_quantity} {"->"} {adjustment.new_effective_quantity}
              </p>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export function ShipmentsPage() {
  const { id: routeShipmentId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { activeBranchCode } = useActiveBranch();
  const [message, setMessage] = useState("");
  const [dialogError, setDialogError] = useState("");
  const [dialogMode, setDialogMode] = useState<DialogMode>(null);
  const [routeTodayOnly, setRouteTodayOnly] = useState(true);
  const [routeSearch, setRouteSearch] = useState("");
  const [cancelReason, setCancelReason] = useState("");
  const [statusReason, setStatusReason] = useState("");
  const [removeReason, setRemoveReason] = useState("");
  const [removeQuantity, setRemoveQuantity] = useState("");
  const [selectedRoute, setSelectedRoute] = useState("");
  const [nextStatus, setNextStatus] = useState("");
  const [selectedLineId, setSelectedLineId] = useState<number | null>(null);

  const selectedShipmentId = routeShipmentId ?? searchParams.get("shipment");
  const page = Number(searchParams.get("page") || "1");
  const filters = {
    branch: activeBranchCode,
    customer: searchParams.get("customer") || "",
    deliveryDate: searchParams.get("delivery_date") || "",
    externalReference: searchParams.get("external_reference") || "",
    ordering: searchParams.get("ordering") || "",
    page,
    paymentMethod: searchParams.get("payment_method") || "",
    pickingStatus: searchParams.get("picking_status") || "",
    route: searchParams.get("route") || "",
    search: searchParams.get("search") || "",
    shipmentStatus: searchParams.get("shipment_status") || "",
  };

  const shipments = useShipments(filters);
  const selectedFromList = shipments.data?.results.find((shipment) => String(shipment.id) === String(selectedShipmentId));
  const detailShipmentId = routeShipmentId ? selectedShipmentId : selectedFromList ? null : selectedShipmentId;
  const selectedQuery = useShipment(detailShipmentId, activeBranchCode);
  const selectedShipment = selectedQuery.data ?? selectedFromList ?? (!routeShipmentId ? shipments.data?.results[0] ?? null : null);
  const selectedLine = selectedShipment?.lines.find((line) => line.id === selectedLineId) ?? null;
  const routeTargets = useShipmentRouteTargets({
    branch: activeBranchCode,
    currentRouteRun: selectedShipment?.route_run,
    operationalDate: selectedShipment?.delivery_date,
    scope: routeTodayOnly ? "today" : "week",
    search: routeSearch,
  });
  const selectedNotFound = selectedQuery.isError && (selectedQuery.error as AxiosError | null)?.response?.status === 404;

  const activate = useActivateShipment();
  const postPickingLists = usePostShipmentPickingLists();
  const prepare = usePrepareShipment();
  const cancel = useCancelShipment();
  const printDocuments = usePrintShipmentDocuments();
  const postDocuments = usePostShipmentDocuments();
  const confirmPickingRoute = useConfirmShipmentPickingRoute();
  const printProforma = usePrintShipmentProforma();
  const closeRoute = useCloseShipmentRoute();
  const changeRoute = useChangeShipmentRoute();
  const changeStatus = useChangeShipmentStatus();
  const removeLineQuantity = useRemoveShipmentLineQuantity();

  const isMutating = [
    activate,
    postPickingLists,
    prepare,
    cancel,
    printDocuments,
    postDocuments,
    confirmPickingRoute,
    printProforma,
    closeRoute,
    changeRoute,
    changeStatus,
    removeLineQuantity,
  ].some((mutation) => mutation.isPending);

  useEffect(() => {
    setSelectedLineId(null);
  }, [selectedShipment?.id, activeBranchCode]);

  useEffect(() => {
    if (!routeShipmentId && selectedShipment && !searchParams.get("shipment")) {
      const next = new URLSearchParams(searchParams);
      next.set("shipment", String(selectedShipment.id));
      setSearchParams(next, { replace: true });
    }
  }, [routeShipmentId, searchParams, selectedShipment, setSearchParams]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    next.delete("shipment");
    setSearchParams(next);
  }

  function selectShipment(shipment: Shipment) {
    if (routeShipmentId) return;
    const next = new URLSearchParams(searchParams);
    next.set("shipment", String(shipment.id));
    setSearchParams(next);
  }

  async function runAction(action: () => Promise<{ message: string }>) {
    try {
      const result = await action();
      setMessage(result.message);
      setDialogMode(null);
      setDialogError("");
    } catch (error) {
      const text = errorMessage(error);
      if (dialogMode) setDialogError(text);
      else setMessage(text);
    }
  }

  function openDialog(mode: DialogMode) {
    setDialogError("");
    if (mode === "change_route") {
      setSelectedRoute("");
      setRouteTodayOnly(true);
      setRouteSearch("");
    }
    setDialogMode(mode);
  }

  const eligibility = selectedShipment?.command_eligibility ?? {};
  const commandTiles = useMemo<ShipmentCommand[]>(
    () => [
      { key: "activate", icon: CheckCircle2, title: "Activation", description: "Activate pending shipments.", run: () => selectedShipment && activate.mutateAsync(selectedShipment.id) },
      {
        key: "post_picking_lists",
        icon: ClipboardCheck,
        title: "Post Picking Lists",
        description: "Create warehouse picking work.",
        run: () => selectedShipment && postPickingLists.mutateAsync(selectedShipment.id),
      },
      { key: "prepare", icon: PackageCheck, title: "Prepare", description: "Confirm completed work.", run: () => selectedShipment && prepare.mutateAsync(selectedShipment.id) },
      { key: "cancel", icon: Ban, title: "Cancel", description: "Cancel eligible shipment.", dialog: "cancel" },
      { key: "post_documents", icon: Send, title: "Post Documents", description: "Post shipment documents only.", run: () => selectedShipment && postDocuments.mutateAsync(selectedShipment.id) },
      {
        key: "confirm_picking_route",
        icon: Route,
        title: "Picking Route",
        description: "Confirm route review.",
        run: () => selectedShipment && confirmPickingRoute.mutateAsync(selectedShipment.id),
      },
      { key: "proforma", icon: FileText, title: "Proforma", description: "Print order preview.", run: () => selectedShipment && printProforma.mutateAsync(selectedShipment.id) },
      { key: "close_route", icon: Truck, title: "Close Routes", description: "Close eligible route.", run: () => selectedShipment && closeRoute.mutateAsync(selectedShipment.id) },
      { key: "change_route", icon: Shuffle, title: "Change Route", description: "Move route.", dialog: "change_route" },
      { key: "change_status", icon: SlidersHorizontal, title: "Change Status", description: "Controlled transition.", dialog: "change_status" },
      { key: "print_documents", icon: Printer, title: "Print Documents", description: "Print WMS documents.", run: () => selectedShipment && printDocuments.mutateAsync(selectedShipment.id) },
    ],
    [activate, closeRoute, confirmPickingRoute, postDocuments, postPickingLists, prepare, printDocuments, printProforma, selectedShipment],
  );

  return (
    <div className="shipments-page">
      <PageHeader
        title="Shipments"
        description="Manage outbound shipments and routes."
        action={
          <button onClick={() => shipments.refetch()} type="button">
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

      <section className="shipment-command-panel" aria-label="Shipment commands">
        {commandTiles.map((tile) => {
          const state = eligibility[tile.key];
          const enabled = Boolean(selectedShipment && state?.enabled);
          return (
            <CommandTile
              key={tile.key}
              description={tile.description}
              disabledReason={selectedShipment ? state?.reason : "Select a shipment first."}
              icon={tile.icon}
              isEnabled={enabled}
              isPending={isMutating}
              onClick={() => {
                if ("dialog" in tile) openDialog(tile.dialog);
                else void runAction(tile.run as () => Promise<{ message: string }>);
              }}
              title={tile.title}
            />
          );
        })}
      </section>

      {selectedLine && (
        <section className="shipment-line-actions">
          <span>
            Selected line {selectedLine.line_number}: <strong>{selectedLine.product_sku}</strong>
          </span>
          <button
            disabled={isMutating || !selectedLine.can_remove_quantity}
            onClick={() => {
              setRemoveQuantity(selectedLine.maximum_removable_quantity === "0" ? "" : selectedLine.maximum_removable_quantity);
              openDialog("remove_quantity");
            }}
            title={selectedLine.remove_blocked_reason || undefined}
            type="button"
          >
            Remove Quantity
          </button>
          {!selectedLine.can_remove_quantity && <small>{selectedLine.remove_blocked_reason}</small>}
        </section>
      )}

      {message && (
        <div className="shipment-message">
          <span>{message}</span>
          <button aria-label="Dismiss message" onClick={() => setMessage("")} type="button">×</button>
        </div>
      )}

      <section className="filter-panel shipment-filter-panel">
        <label>
          <span>Search</span>
          <input onChange={(event) => setFilter("search", event.target.value)} placeholder="Shipment, customer, route" value={filters.search} />
        </label>
        <label>
          <span>Shipment status</span>
          <select onChange={(event) => setFilter("shipment_status", event.target.value)} value={filters.shipmentStatus}>
            {SHIPMENT_STATUSES.map((status) => <option key={status || "all"} value={status}>{status ? label(status) : "All"}</option>)}
          </select>
        </label>
        <label>
          <span>Picking status</span>
          <select onChange={(event) => setFilter("picking_status", event.target.value)} value={filters.pickingStatus}>
            {PICKING_STATUSES.map((status) => <option key={status || "all"} value={status}>{status ? label(status) : "All"}</option>)}
          </select>
        </label>
        <label>
          <span>Route</span>
          <input onChange={(event) => setFilter("route", event.target.value)} placeholder="ROUTE-01" value={filters.route} />
        </label>
        <label>
          <span>Delivery date</span>
          <input onChange={(event) => setFilter("delivery_date", event.target.value)} type="date" value={filters.deliveryDate} />
        </label>
        <label>
          <span>Customer</span>
          <input onChange={(event) => setFilter("customer", event.target.value)} placeholder="Customer or account" value={filters.customer} />
        </label>
        <label>
          <span>Payment</span>
          <input onChange={(event) => setFilter("payment_method", event.target.value)} placeholder="Account" value={filters.paymentMethod} />
        </label>
        <label>
          <span>External ref</span>
          <input onChange={(event) => setFilter("external_reference", event.target.value)} placeholder="AX reference" value={filters.externalReference} />
        </label>
      </section>

      <DataState
        isLoading={shipments.isLoading || selectedQuery.isLoading}
        isError={shipments.isError || (selectedQuery.isError && !selectedNotFound)}
        error={shipments.error || selectedQuery.error}
      >
        <section className="panel shipment-table-panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Branch</th>
                  <th>Reference</th>
                  <th>Lines</th>
                  <th>Shipment Status</th>
                  <th>Picking Status</th>
                  <th>Route ID</th>
                  <th>Route Time</th>
                  <th>Cutoff</th>
                  <th>Printed</th>
                  <th>Payment</th>
                  <th>Customer Alias</th>
                  <th>Recipient</th>
                  <th>External Notes</th>
                  <th>Delivery Name</th>
                  <th>Delivery Date</th>
                </tr>
              </thead>
              <tbody>
                {(shipments.data?.results ?? []).map((shipment) => (
                  <tr
                    className={selectedShipment?.id === shipment.id ? "selected-row" : ""}
                    key={shipment.id}
                    onClick={() => selectShipment(shipment)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") selectShipment(shipment);
                    }}
                    tabIndex={0}
                  >
                    <td>{shipment.branch_code}</td>
                    <td><Link className="table-link mono" to={`/wms/shipments/${shipment.id}`}>{shipment.reference}</Link></td>
                    <td>{shipment.line_count}</td>
                    <td><StatusBadge label={label(shipment.status)} tone={statusTone(shipment.status)} /></td>
                    <td><StatusBadge label={label(shipment.picking_status)} tone={statusTone(shipment.picking_status)} /></td>
                    <td>{shipment.route_code || "-"}</td>
                    <td>{shipment.route_time || "-"}</td>
                    <td>{shipment.cutoff_time || "-"}</td>
                    <td>{shipment.document_status === "printed" || shipment.document_status === "posted" ? "Yes" : "-"}</td>
                    <td>{shipment.payment_method || "-"}</td>
                    <td>{shipment.customer_alias || "-"}</td>
                    <td>{shipment.recipient_account || "-"}</td>
                    <td className="shipment-notes-cell">{shipment.external_notes || "-"}</td>
                    <td>{shipment.delivery_name || "-"}</td>
                    <td>{dateOnly(shipment.delivery_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(shipments.data?.results ?? []).length === 0 && <div className="state-box">No shipments found.</div>}
          <div className="pagination-bar">
            <span>{shipments.data?.count ?? 0} shipments</span>
            <div>
              <button disabled={!shipments.data?.previous || page <= 1} onClick={() => setFilter("page", String(Math.max(1, page - 1)))} type="button">
                Previous
              </button>
              <strong>Page {page}</strong>
              <button disabled={!shipments.data?.next} onClick={() => setFilter("page", String(page + 1))} type="button">
                Next
              </button>
            </div>
          </div>
        </section>
        {selectedShipment ? (
          <ShipmentDetail
            onSelectLine={(line) => setSelectedLineId(line.id)}
            selectedLineId={selectedLineId}
            shipment={selectedShipment}
          />
        ) : routeShipmentId ? (
          <div className="state-box">Shipment was not found or is not available for this branch.</div>
        ) : null}
      </DataState>

      {dialogMode === "cancel" && selectedShipment && (
        <CommandDialog
          error={dialogError}
          isPending={cancel.isPending}
          onClose={() => setDialogMode(null)}
          onSubmit={(event) => {
            event.preventDefault();
            void runAction(() => cancel.mutateAsync(selectedShipment.id, cancelReason));
          }}
          submitLabel="Cancel Shipment"
          title={`Cancel ${selectedShipment.reference}`}
        >
          <label>
            <span>Reason</span>
            <textarea autoFocus onChange={(event) => setCancelReason(event.target.value)} required value={cancelReason} />
          </label>
        </CommandDialog>
      )}

      {dialogMode === "change_route" && selectedShipment && (
        <CommandDialog
          error={dialogError}
          isPending={changeRoute.isPending}
          onClose={() => setDialogMode(null)}
          onSubmit={(event) => {
            event.preventDefault();
            if (!selectedRoute) {
              setDialogError("Target route is required.");
              return;
            }
            const target = targetById(routeTargets.data?.results ?? [], selectedRoute);
            if (!target) {
              setDialogError("Selected route target is no longer available.");
              return;
            }
            void runAction(() => changeRoute.mutateAsync(selectedShipment.id, target));
          }}
          submitLabel="Change Route"
          title={`Change route for ${selectedShipment.reference}`}
        >
          <p className="shipment-dialog-context">Current route: {selectedShipment.route_code || "-"} {selectedShipment.route_time || ""}</p>
          <label className="shipment-checkbox-label">
            <input checked={routeTodayOnly} onChange={(event) => setRouteTodayOnly(event.target.checked)} type="checkbox" />
            <span>Today only</span>
          </label>
          <label>
            <span>Route search</span>
            <input
              onChange={(event) => setRouteSearch(event.target.value)}
              placeholder={routeTodayOnly ? "Search today's routes" : "Search this operational week"}
              value={routeSearch}
            />
          </label>
          <label>
            <span>Target route</span>
            <select autoFocus onChange={(event) => setSelectedRoute(event.target.value)} required value={selectedRoute}>
              <option value="">Select route</option>
              {(routeTargets.data?.results ?? []).map((route) => (
                <option key={route.id} value={String(route.id)}>
                  {route.operational_identifier} / {route.weekday} {route.service_date} / {route.departure_time} / {route.dispatch_wave || "No wave"} / {route.creates_route_run ? "creates run" : `${route.shipment_count} shipment(s)`}
                </option>
              ))}
            </select>
          </label>
          {!routeTodayOnly && <p className="shipment-dialog-context">Showing eligible route runs from the current operational week.</p>}
        </CommandDialog>
      )}

      {dialogMode === "change_status" && selectedShipment && (
        <CommandDialog
          error={dialogError}
          isPending={changeStatus.isPending}
          onClose={() => setDialogMode(null)}
          onSubmit={(event) => {
            event.preventDefault();
            if (!nextStatus) {
              setDialogError("Next status is required.");
              return;
            }
            void runAction(() => changeStatus.mutateAsync(selectedShipment.id, nextStatus, statusReason));
          }}
          submitLabel="Change Status"
          title={`Change status for ${selectedShipment.reference}`}
        >
          <p className="shipment-dialog-context">Current status: {label(selectedShipment.status)}</p>
          <label>
            <span>Next status</span>
            <select autoFocus onChange={(event) => setNextStatus(event.target.value)} required value={nextStatus}>
              <option value="">Select status</option>
              {MANUAL_NEXT_STATUSES.map((status) => <option key={status} value={status}>{label(status)}</option>)}
            </select>
          </label>
          <label>
            <span>Reason</span>
            <textarea onChange={(event) => setStatusReason(event.target.value)} required value={statusReason} />
          </label>
        </CommandDialog>
      )}

      {dialogMode === "remove_quantity" && selectedShipment && selectedLine && (
        <CommandDialog
          error={dialogError}
          isPending={removeLineQuantity.isPending}
          onClose={() => setDialogMode(null)}
          onSubmit={(event) => {
            event.preventDefault();
            void runAction(() => removeLineQuantity.mutateAsync({
              id: selectedShipment.id,
              lineId: selectedLine.id,
              quantity: removeQuantity,
              reason: removeReason,
            }));
          }}
          submitLabel="Remove Quantity"
          title={`Remove quantity from ${selectedShipment.reference}`}
        >
          <div className="shipment-remove-summary">
            <Definition label="Product" value={`${selectedLine.product_sku} ${selectedLine.product_name}`} />
            <Definition label="Original" value={selectedLine.original_ordered_quantity} />
            <Definition label="Effective" value={selectedLine.effective_quantity} />
            <Definition label="Picked" value={selectedLine.picked_quantity} />
            <Definition label="Controlled" value={selectedLine.controlled_quantity} />
            <Definition label="Max removable" value={selectedLine.maximum_removable_quantity} />
          </div>
          <p className="shipment-dialog-context">
            Removed unpicked units stay in their current inventory location. This does not create a return, stock movement, or sales correction.
          </p>
          <label>
            <span>Quantity to remove</span>
            <input autoFocus min="0.001" onChange={(event) => setRemoveQuantity(event.target.value)} required step="0.001" type="number" value={removeQuantity} />
          </label>
          <label>
            <span>Reason</span>
            <textarea onChange={(event) => setRemoveReason(event.target.value)} required value={removeReason} />
          </label>
        </CommandDialog>
      )}
    </div>
  );
}
