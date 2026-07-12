import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { Archive, ArrowLeft, Camera, ChevronDown, ClipboardList, History, Menu, PackageSearch } from "lucide-react";
import { Link } from "react-router-dom";

import { useCurrentAuditLogs, useScannerCartWork, useScannerConfirmLocation, useScannerPickingPick } from "../api/queries";
import { useActiveBranch } from "../api/ActiveBranchContext";
import { useStoredScannerSession } from "../api/scannerSession";
import { DataState } from "../components/DataState";
import { CameraBarcodeScanner } from "../components/scanner/CameraBarcodeScanner";


function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

function formatStatus(status: string) {
  return status.replaceAll("_", " ");
}

function toNumber(value: string | number) {
  return Number.parseFloat(String(value));
}

function formatQuantity(value: string | number) {
  const numberValue = toNumber(value);
  if (!Number.isFinite(numberValue)) {
    return String(value);
  }

  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(numberValue);
}

function ProductImage({ alt, imageUrl, compact = false }: { alt: string; imageUrl?: string; compact?: boolean }) {
  const [failed, setFailed] = useState(false);
  if (!imageUrl || failed) {
    return <div className={`concept-product-placeholder ${compact ? "concept-product-placeholder--compact" : ""}`}>Product image unavailable</div>;
  }
  return <img className={compact ? "concept-product-thumb" : "concept-product-image"} src={imageUrl} alt={alt} onError={() => setFailed(true)} />;
}

export function ScannerPickingPage() {
  const { activeBranchCode } = useActiveBranch();
  const queryClient = useQueryClient();
  const activeSession = useStoredScannerSession();
  const cartWork = useScannerCartWork(activeSession?.id, activeSession?.cart_work_session);
  const confirmLocation = useScannerConfirmLocation();
  const scannerPick = useScannerPickingPick();
  const [workerCode] = useState(activeSession?.worker_code || "DEMO");
  const [locationCode, setLocationCode] = useState("");
  const [productCode, setProductCode] = useState("");
  const [pickQuantity, setPickQuantity] = useState("1");
  const [cameraMode, setCameraMode] = useState<"location" | "product" | null>(null);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const locationInputRef = useRef<HTMLInputElement | null>(null);
  const productInputRef = useRef<HTMLInputElement | null>(null);
  const tasks = cartWork.data?.tasks ?? [];
  const work = cartWork.data?.cart_work_session;
  const instruction = cartWork.data?.current_instruction ?? null;
  const pickingState = cartWork.data?.state ?? "waiting_for_location";
  const totalToPick = tasks.reduce((sum, task) => sum + toNumber(task.quantity_to_pick), 0);
  const totalPicked = tasks.reduce((sum, task) => sum + toNumber(task.quantity_picked), 0);
  const totalPrepared = tasks.reduce((sum, task) => sum + toNumber(task.quantity_prepared), 0);
  const totalRemaining = tasks.reduce((sum, task) => sum + toNumber(task.remaining_quantity), 0);
  const progress = work?.picking_job.progress_percent ?? 0;
  const recentActions = useCurrentAuditLogs(activeBranchCode, { cart: work?.cart_code, eventType: "pick" });
  const locationGroups = useMemo(() => {
    const grouped = new Map<string, typeof tasks>();
    tasks.forEach((task) => grouped.set(task.source_location_code, [...(grouped.get(task.source_location_code) ?? []), task]));
    return Array.from(grouped, ([location, groupTasks]) => ({
      location,
      name: groupTasks[0]?.source_location_name,
      tasks: groupTasks,
      toPick: groupTasks.reduce((sum, task) => sum + toNumber(task.quantity_to_pick), 0),
      picked: groupTasks.reduce((sum, task) => sum + toNumber(task.quantity_picked), 0),
      remaining: groupTasks.reduce((sum, task) => sum + toNumber(task.remaining_quantity), 0),
    }));
  }, [tasks]);
  const currentLocation = instruction?.location.code ?? null;
  const [expandedLocation, setExpandedLocation] = useState<string | null>(null);

  useEffect(() => {
    setExpandedLocation(currentLocation);
  }, [currentLocation]);

  const refreshPickingData = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["scanner-cart-work"] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-control-cart-items", activeSession?.id] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-jobs"] }),
      queryClient.invalidateQueries({ queryKey: ["route-runs"] }),
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
    ]);
  }, [activeSession?.id, queryClient]);

  const submitLocation = useCallback(async (code: string) => {
    if (!work || !instruction) {
      return;
    }

    setMessage(null);
    try {
      const result = await confirmLocation.mutateAsync({ cartWorkSessionId: work.id, locationCode: code });
      setMessage({ type: "success", text: result.message || "Location confirmed." });
      setLocationCode("");
      await refreshPickingData();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not confirm the location.") });
      locationInputRef.current?.focus();
    }
  }, [confirmLocation, instruction, refreshPickingData, work]);

  const submitProduct = useCallback(async (code: string) => {
    if (!work || !instruction || pickingState !== "waiting_for_product") {
      setMessage({ type: "error", text: "Scan the expected location before scanning the product." });
      return;
    }

    setMessage(null);
    try {
      const result = await scannerPick.mutateAsync({
        cartWorkSessionId: work.id,
        code,
        quantity: pickQuantity,
        workerCode,
      });
      setMessage({ type: "success", text: result.message });
      setProductCode("");
      setPickQuantity("1");
      await refreshPickingData();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not pick the product.") });
      productInputRef.current?.focus();
    }
  }, [instruction, pickQuantity, pickingState, refreshPickingData, scannerPick, work, workerCode]);

  function handleConfirmLocation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitLocation(locationCode);
  }

  function handlePick(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitProduct(productCode);
  }

  const handleCameraDetected = useCallback(async (code: string) => {
    const mode = cameraMode;
    setCameraMode(null);

    if (mode === "location") {
      setLocationCode(code);
      await submitLocation(code);
      return;
    }

    if (mode === "product") {
      setProductCode(code);
      await submitProduct(code);
    }
  }, [cameraMode, submitLocation, submitProduct]);

  function handleCameraClose() {
    const mode = cameraMode;
    setCameraMode(null);
    window.setTimeout(() => {
      if (mode === "location") {
        locationInputRef.current?.focus();
      }
      if (mode === "product") {
        productInputRef.current?.focus();
      }
    }, 0);
  }

  useEffect(() => {
    setProductCode("");
    setLocationCode("");
  }, [instruction?.picking_task_id, pickingState]);

  useEffect(() => {
    if (!work || !instruction) return;
    window.setTimeout(() => {
      if (pickingState === "waiting_for_location") locationInputRef.current?.focus();
      if (pickingState === "waiting_for_product") productInputRef.current?.focus();
    }, 0);
  }, [instruction, pickingState, work]);

  const active = Boolean(work && instruction && pickingState !== "completed");
  const currentTask = tasks.find((task) => task.id === instruction?.picking_task_id);

  return (
    <div className="concept-picking-page">
      <div className="scanner-links"><Link to="/scanner"><ArrowLeft size={17} />Scanner menu</Link><Link to="/scanner/tasks">Tasks</Link><Link to="/scanner/control">Control</Link></div>
      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}
      <CameraBarcodeScanner isOpen={cameraMode !== null} onClose={handleCameraClose} onDetected={handleCameraDetected} />

      <DataState isLoading={Boolean(activeSession) && cartWork.isLoading} isError={Boolean(activeSession) && cartWork.isError} error={cartWork.error}>
        <section className={`concept-current-bar ${active ? "" : "concept-current-bar--empty"}`}>
          <div className="concept-current-identity">
            {instruction ? <ProductImage compact alt={instruction.product.name} imageUrl={instruction.product.image_url} /> : <div className="concept-product-placeholder concept-product-placeholder--compact">—</div>}
            <div><small>Current pick</small><strong>{instruction?.product.sku ?? "No active picking work yet"}</strong><span>{instruction?.product.name ?? "Open Tasks, choose a job, and scan a cart."}</span>{instruction?.product.brand && <em>{instruction.product.brand}</em>}</div>
          </div>
          <div className="concept-current-location"><small>Location</small><strong>{instruction?.location.code ?? "—"}</strong></div>
          <dl className="concept-current-stats"><div><dt>To pick</dt><dd>{formatQuantity(instruction?.required_quantity ?? 0)}</dd></div><div><dt>Picked</dt><dd>{formatQuantity(instruction?.picked_quantity ?? 0)}</dd></div><div><dt>Remaining</dt><dd>{formatQuantity(instruction?.remaining_quantity ?? 0)}</dd></div><div><dt>Progress</dt><dd>{progress}%</dd></div></dl>
        </section>

        <section className="concept-summary-strip">
          <span>Task: <strong>{work ? `#${work.picking_job.id}` : "—"}</strong></span><span>Cart: <strong>{work?.cart_code ?? "—"}</strong></span><span>Progress: <strong>{progress}%</strong></span><span>To pick: <strong>{formatQuantity(totalToPick)}</strong></span><span>Picked: <strong>{formatQuantity(totalPicked)}</strong></span><span>Prepared: <strong>{formatQuantity(totalPrepared)}</strong></span><span>Remaining: <strong>{formatQuantity(totalRemaining)}</strong></span>
        </section>

        <section className="concept-product-card">
          <div className="concept-product-visual"><ProductImage alt={instruction?.product.name ?? "Product"} imageUrl={instruction?.product.image_url} /></div>
          <div className="concept-product-copy"><span className="concept-eyebrow">{instruction?.product.brand || "Brand unavailable"}</span><h1>{instruction?.product.name ?? "No active product"}</h1><strong className="concept-sku">{instruction?.product.sku ?? "—"}</strong><p>{instruction?.product.description || "Product description unavailable."}</p><dl><div><dt>Location</dt><dd>{instruction?.location.code ?? "—"}</dd></div><div><dt>Cart</dt><dd>{work?.cart_code ?? "—"}</dd></div><div><dt>Order</dt><dd>{instruction?.order_reference ?? "—"}</dd></div></dl></div>
          <dl className="concept-product-quantities"><div><dt>To pick</dt><dd>{formatQuantity(currentTask?.quantity_to_pick ?? 0)}</dd></div><div><dt>Picked</dt><dd>{formatQuantity(currentTask?.quantity_picked ?? 0)}</dd></div><div><dt>Prepared</dt><dd>{formatQuantity(currentTask?.quantity_prepared ?? 0)}</dd></div><div><dt>Remaining</dt><dd>{formatQuantity(currentTask?.remaining_quantity ?? 0)}</dd></div></dl>
        </section>

        <section className="concept-scan-grid">
          <form className={`concept-scan-card ${pickingState === "waiting_for_location" && active ? "concept-scan-card--active" : ""}`} onSubmit={handleConfirmLocation}><header><span>Scan location</span><strong>Scan the required warehouse location.</strong></header><p>Expected: <b>{instruction?.location.code ?? "—"}</b></p><div className="concept-scan-controls"><input ref={locationInputRef} disabled={!active || pickingState !== "waiting_for_location"} value={locationCode} onChange={(event) => setLocationCode(event.target.value)} placeholder="Scan location barcode" autoComplete="off" /><button type="button" disabled={!active || pickingState !== "waiting_for_location"} onClick={() => setCameraMode("location")}><Camera size={18} /></button></div><button className="sr-only" type="submit">Confirm location</button></form>
          <form className={`concept-scan-card ${pickingState === "waiting_for_product" && active ? "concept-scan-card--active" : ""}`} onSubmit={handlePick}><header><span>Scan product</span><strong>Scan the product barcode to confirm the pick.</strong></header><p>Expected: <b>{instruction?.product.sku ?? "—"}</b></p><div className="concept-scan-controls"><input ref={productInputRef} disabled={!active || pickingState !== "waiting_for_product"} value={productCode} onChange={(event) => setProductCode(event.target.value)} placeholder="Scan product barcode" autoComplete="off" /><input className="concept-quantity-input" disabled={!active || pickingState !== "waiting_for_product"} inputMode="numeric" value={pickQuantity} onChange={(event) => setPickQuantity(event.target.value.replace(/\D/g, ""))} /><button type="button" disabled={!active || pickingState !== "waiting_for_product"} onClick={() => setCameraMode("product")}><Camera size={18} /></button></div><button className="sr-only" type="submit">Confirm product</button></form>
        </section>

        {!instruction && work && pickingState === "completed" && <section className="concept-complete-card"><strong>Picking completed</strong><span>All required work for this cart is complete.</span><Link to="/scanner/control">Go to Control</Link></section>}

        <section className="concept-manifest">
          <header><div><span>Manifest</span><strong>{tasks.length} products</strong></div></header>
          {locationGroups.length === 0 ? <div className="concept-manifest-empty"><PackageSearch size={32} /><strong>No manifest loaded</strong><span>Select a task to start picking.</span></div> : locationGroups.map((group) => {
            const expanded = expandedLocation === group.location;
            const complete = group.remaining <= 0;
            return <article className={`concept-location-group ${complete ? "concept-location-group--complete" : ""}`} key={group.location}><button className="concept-location-toggle" onClick={() => setExpandedLocation(expanded ? null : group.location)} type="button"><div><strong>{group.location}</strong><span>{group.name}</span></div><div><span>{group.tasks.length} products</span><span>To pick: {formatQuantity(group.toPick)}</span><span>Picked: {formatQuantity(group.picked)}</span><span>Remaining: {formatQuantity(group.remaining)}</span></div><ChevronDown className={expanded ? "is-open" : ""} size={20} /></button>{expanded && <div className="concept-manifest-products">{group.tasks.map((task) => {
              const isCurrent = task.id === instruction?.picking_task_id;
              const isComplete = toNumber(task.remaining_quantity) <= 0;
              const statusLabel = isCurrent ? "Current" : isComplete ? "Completed" : task.status === "open" ? "Open" : "Next";
              return <div className={`concept-manifest-row ${isComplete ? "concept-manifest-row--complete" : ""}`} key={task.id}><ProductImage compact alt={task.product_name} imageUrl={task.product_image_url} /><div className="concept-manifest-product"><strong>{task.product_sku}</strong><span>{task.product_name}</span><small>{task.product_brand || "Brand unavailable"} · {task.order_reference}</small></div><dl><div><dt>To pick</dt><dd>{formatQuantity(task.quantity_to_pick)}</dd></div><div><dt>Picked</dt><dd>{formatQuantity(task.quantity_picked)}</dd></div><div><dt>Prepared</dt><dd>{formatQuantity(task.quantity_prepared)}</dd></div><div><dt>Remaining</dt><dd>{formatQuantity(task.remaining_quantity)}</dd></div></dl><span className={`concept-row-status concept-row-status--${statusLabel.toLowerCase()}`}>{statusLabel}</span></div>;
            })}</div>}</article>;
          })}
        </section>

        <details className="concept-recent-actions"><summary>Recent actions</summary>{(work ? recentActions.data?.results ?? [] : []).slice(0, 5).map((event) => <div key={event.id}><time>{new Date(event.created_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</time><span>{event.message}</span></div>)}{(!work || (recentActions.data?.results.length ?? 0) === 0) && <p>No picking actions yet.</p>}<Link to={`/wms/current-events?event_type=pick${work ? `&cart=${encodeURIComponent(work.cart_code)}` : ""}`}>View all</Link></details>
      </DataState>

      <nav className="concept-mobile-nav"><Link to="/scanner/tasks"><ClipboardList size={20} /><span>Tasks</span></Link><Link className="active" to="/scanner/picking"><Archive size={20} /><span>Picking</span></Link><Link to="/wms/inventory"><PackageSearch size={20} /><span>Inventory</span></Link><Link to="/wms/current-events"><History size={20} /><span>History</span></Link><Link to="/scanner"><Menu size={20} /><span>More</span></Link></nav>
    </div>
  );
}
