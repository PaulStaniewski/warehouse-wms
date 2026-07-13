import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import {
  Archive,
  ArrowLeft,
  Camera,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  History,
  Menu,
  PackageSearch,
} from "lucide-react";
import { Link } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useCurrentAuditLogs, useScannerCartWork, useScannerConfirmLocation, useScannerPickingPick } from "../api/queries";
import { useStoredScannerSession } from "../api/scannerSession";
import { CameraBarcodeScanner } from "../components/scanner/CameraBarcodeScanner";
import { DataState } from "../components/DataState";
import type { AuditLog, PickingTask, PickInstruction } from "../types/api";

type ScanMessage = {
  type: "success" | "error" | "warning";
  title: string;
  detail?: string;
};

type LocationGroup = {
  location: string;
  name: string;
  tasks: PickingTask[];
  toPick: number;
  picked: number;
  prepared: number;
  remaining: number;
};

function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

function toNumber(value: string | number | null | undefined) {
  return Number.parseFloat(String(value ?? 0));
}

function formatQuantity(value: string | number | null | undefined) {
  const numberValue = toNumber(value);
  if (!Number.isFinite(numberValue)) {
    return String(value);
  }

  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(numberValue);
}

function clampQuantity(value: number, max: number) {
  if (!Number.isFinite(value)) {
    return 1;
  }

  return Math.max(1, Math.min(Math.max(1, max), Math.floor(value)));
}

function ProductImage({ alt, imageUrl, compact = false }: { alt: string; imageUrl?: string; compact?: boolean }) {
  const [failed, setFailed] = useState(false);

  if (!imageUrl || failed) {
    return (
      <div className={`concept-product-placeholder ${compact ? "concept-product-placeholder--compact" : ""}`}>
        Product image unavailable
      </div>
    );
  }

  return (
    <img
      className={compact ? "concept-product-thumb" : "concept-product-image"}
      src={imageUrl}
      alt={alt}
      onError={() => setFailed(true)}
    />
  );
}

function getStepLabel(pickingState: string, active: boolean) {
  if (!active) {
    return "No active picking work";
  }

  if (pickingState === "waiting_for_product") {
    return "Waiting for product";
  }

  if (pickingState === "completed") {
    return "Pick recorded";
  }

  return "Waiting for location";
}

function compactAction(event: AuditLog) {
  const time = new Date(event.created_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const actor = event.actor_display || event.actor_username || "Worker";
  const quantity = event.quantity ? formatQuantity(event.quantity) : "";
  const product = event.product_sku || "product";
  const source = event.source_location_code || event.source_label || "location";
  const cart = event.cart_code || "cart";
  const action = event.event_type === "prepare" ? "prepared" : "picked";

  return {
    id: event.id,
    time,
    line: `${actor} ${action}${quantity ? ` ${quantity} x` : ""} ${product}`,
    route: `${source} -> ${cart}`,
  };
}

function getTaskStatus(task: PickingTask, instruction: PickInstruction | null, nextOpenTaskId?: number) {
  if (task.id === instruction?.picking_task_id) {
    return "Current";
  }

  if (toNumber(task.remaining_quantity) <= 0) {
    return "Completed";
  }

  if (task.id === nextOpenTaskId) {
    return "Next";
  }

  return "Open";
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
  const [message, setMessage] = useState<ScanMessage | null>(null);
  const [autoExpandedLocation, setAutoExpandedLocation] = useState<string | null>(null);
  const [manualExpandedLocations, setManualExpandedLocations] = useState<Set<string>>(() => new Set());
  const locationInputRef = useRef<HTMLInputElement | null>(null);
  const productInputRef = useRef<HTMLInputElement | null>(null);

  const tasks = cartWork.data?.tasks ?? [];
  const work = cartWork.data?.cart_work_session;
  const instruction = cartWork.data?.current_instruction ?? null;
  const pickingState = cartWork.data?.state ?? "waiting_for_location";
  const active = Boolean(work && instruction && pickingState !== "completed");
  const currentTask = tasks.find((task) => task.id === instruction?.picking_task_id);
  const currentRemaining = toNumber(currentTask?.remaining_quantity ?? instruction?.remaining_quantity ?? 0);
  const totalToPick = tasks.reduce((sum, task) => sum + toNumber(task.quantity_to_pick), 0);
  const totalPicked = tasks.reduce((sum, task) => sum + toNumber(task.quantity_picked), 0);
  const totalPrepared = tasks.reduce((sum, task) => sum + toNumber(task.quantity_prepared), 0);
  const totalRemaining = tasks.reduce((sum, task) => sum + toNumber(task.remaining_quantity), 0);
  const progress = work?.picking_job.progress_percent ?? 0;
  const progressText = `${formatQuantity(totalPicked)} / ${formatQuantity(totalToPick)} picked`;
  const currentLocation = instruction?.location.code ?? null;
  const recentActions = useCurrentAuditLogs(activeBranchCode, { cart: work?.cart_code, eventType: "pick" });

  const locationGroups = useMemo<LocationGroup[]>(() => {
    const grouped = new Map<string, PickingTask[]>();
    tasks.forEach((task) => grouped.set(task.source_location_code, [...(grouped.get(task.source_location_code) ?? []), task]));

    return Array.from(grouped, ([location, groupTasks]) => ({
      location,
      name: groupTasks[0]?.source_location_name || "Location",
      tasks: groupTasks,
      toPick: groupTasks.reduce((sum, task) => sum + toNumber(task.quantity_to_pick), 0),
      picked: groupTasks.reduce((sum, task) => sum + toNumber(task.quantity_picked), 0),
      prepared: groupTasks.reduce((sum, task) => sum + toNumber(task.quantity_prepared), 0),
      remaining: groupTasks.reduce((sum, task) => sum + toNumber(task.remaining_quantity), 0),
    }));
  }, [tasks]);

  const nextOpenTaskId = tasks.find((task) => toNumber(task.remaining_quantity) > 0)?.id;
  const compactActions = (work ? recentActions.data?.results ?? [] : []).slice(0, 5).map(compactAction);

  useEffect(() => {
    setAutoExpandedLocation(currentLocation);
  }, [currentLocation]);

  useEffect(() => {
    const max = Math.max(1, Math.floor(currentRemaining));
    setPickQuantity((current) => String(clampQuantity(Number.parseInt(current || "1", 10), max)));
  }, [currentRemaining, instruction?.picking_task_id]);

  const refreshPickingData = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["scanner-cart-work"] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-control-cart-items", activeSession?.id] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-jobs"] }),
      queryClient.invalidateQueries({ queryKey: ["route-runs"] }),
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
    ]);
  }, [activeSession?.id, queryClient]);

  const submitLocation = useCallback(
    async (code: string) => {
      const scanned = code.trim();
      if (!work || !instruction || !scanned) {
        return;
      }

      if (scanned.toUpperCase() !== instruction.location.code.toUpperCase()) {
        setMessage({
          type: "error",
          title: "Wrong location scanned.",
          detail: `Expected: ${instruction.location.code} | Scanned: ${scanned}`,
        });
        setLocationCode("");
        window.setTimeout(() => locationInputRef.current?.focus(), 0);
        return;
      }

      setMessage(null);
      try {
        const result = await confirmLocation.mutateAsync({ cartWorkSessionId: work.id, locationCode: scanned });
        setMessage({ type: "success", title: `Location ${instruction.location.code} confirmed.`, detail: result.message });
        setLocationCode("");
        await refreshPickingData();
        window.setTimeout(() => productInputRef.current?.focus(), 0);
      } catch (error) {
        setMessage({ type: "error", title: "Could not confirm the location.", detail: getErrorMessage(error, "Try again.") });
        window.setTimeout(() => locationInputRef.current?.focus(), 0);
      }
    },
    [confirmLocation, instruction, refreshPickingData, work],
  );

  const submitProduct = useCallback(
    async (code: string) => {
      const scanned = code.trim();
      if (!work || !instruction || pickingState !== "waiting_for_product") {
        setMessage({ type: "warning", title: "Scan the expected location before scanning the product." });
        window.setTimeout(() => locationInputRef.current?.focus(), 0);
        return;
      }

      if (!scanned) {
        return;
      }

      const expectedCodes = [instruction.product.sku, instruction.product.barcode].filter(Boolean).map((value) => value!.toUpperCase());
      if (!expectedCodes.includes(scanned.toUpperCase())) {
        setMessage({
          type: "error",
          title: "Wrong product scanned.",
          detail: `Expected: ${instruction.product.sku} | Scanned: ${scanned}`,
        });
        setProductCode("");
        window.setTimeout(() => productInputRef.current?.focus(), 0);
        return;
      }

      const quantity = clampQuantity(Number.parseInt(pickQuantity || "1", 10), currentRemaining);
      if (quantity > currentRemaining) {
        setMessage({ type: "error", title: "Quantity exceeds the remaining quantity." });
        window.setTimeout(() => productInputRef.current?.focus(), 0);
        return;
      }

      setMessage(null);
      try {
        const result = await scannerPick.mutateAsync({
          cartWorkSessionId: work.id,
          code: scanned,
          quantity: String(quantity),
          workerCode,
        });
        const nextLocation = result.current_instruction?.location.code;
        const changedLocation = nextLocation && nextLocation !== instruction.location.code;
        setMessage({
          type: "success",
          title: changedLocation
            ? `Location completed. Continue to ${nextLocation}.`
            : `Pick recorded: ${formatQuantity(quantity)} x ${instruction.product.sku}.`,
          detail: result.state === "completed" ? "All picking work is complete." : undefined,
        });
        setProductCode("");
        setPickQuantity("1");
        await refreshPickingData();
      } catch (error) {
        setMessage({ type: "error", title: "Could not pick the product.", detail: getErrorMessage(error, "Try again.") });
        window.setTimeout(() => productInputRef.current?.focus(), 0);
      }
    },
    [currentRemaining, instruction, pickQuantity, pickingState, refreshPickingData, scannerPick, work, workerCode],
  );

  function handleConfirmLocation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitLocation(locationCode);
  }

  function handlePick(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitProduct(productCode);
  }

  function handleQuantityChange(value: string) {
    const cleanValue = value.replace(/\D/g, "");
    if (!cleanValue) {
      setPickQuantity("");
      return;
    }

    setPickQuantity(String(clampQuantity(Number.parseInt(cleanValue, 10), currentRemaining)));
  }

  function adjustQuantity(delta: number) {
    setPickQuantity((current) => String(clampQuantity(Number.parseInt(current || "1", 10) + delta, currentRemaining)));
    window.setTimeout(() => productInputRef.current?.focus(), 0);
  }

  function toggleLocation(location: string) {
    setManualExpandedLocations((current) => {
      const next = new Set(current);
      if (next.has(location)) {
        next.delete(location);
      } else {
        next.add(location);
      }
      return next;
    });
  }

  const handleCameraDetected = useCallback(
    async (code: string) => {
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
    },
    [cameraMode, submitLocation, submitProduct],
  );

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
    if (!work || !instruction) {
      return;
    }

    window.setTimeout(() => {
      if (pickingState === "waiting_for_location") {
        locationInputRef.current?.focus();
      }
      if (pickingState === "waiting_for_product") {
        productInputRef.current?.focus();
      }
    }, 0);
  }, [instruction, pickingState, work]);

  return (
    <div className="concept-picking-page">
      <div className="scanner-links">
        <Link to="/scanner"><ArrowLeft size={17} />Scanner menu</Link>
        <Link to="/scanner/tasks">Tasks</Link>
        <Link to="/scanner/control">Control</Link>
      </div>

      {message && (
        <div className={`scanner-message scanner-message--${message.type}`}>
          <strong>{message.title}</strong>
          {message.detail && <span>{message.detail}</span>}
        </div>
      )}

      <CameraBarcodeScanner isOpen={cameraMode !== null} onClose={handleCameraClose} onDetected={handleCameraDetected} />

      <DataState isLoading={Boolean(activeSession) && cartWork.isLoading} isError={Boolean(activeSession) && cartWork.isError} error={cartWork.error}>
        <section className={`concept-current-bar ${active ? "" : "concept-current-bar--empty"}`}>
          <div className="concept-current-identity">
            {instruction ? (
              <ProductImage compact alt={instruction.product.name} imageUrl={instruction.product.image_url} />
            ) : (
              <div className="concept-product-placeholder concept-product-placeholder--compact">-</div>
            )}
            <div>
              <small className="concept-current-badge">Current pick</small>
              <strong>{instruction?.product.sku ?? "No active picking work yet"}</strong>
              <span>{instruction?.product.name ?? "Open Tasks, choose a job, and scan a cart."}</span>
              {instruction?.product.brand && <em>{instruction.product.brand}</em>}
            </div>
          </div>

          <div className="concept-current-location">
            <small>Current location</small>
            <strong>{instruction?.location.code ?? "-"}</strong>
            <span>{getStepLabel(pickingState, active)}</span>
          </div>

          <div className="concept-current-progress">
            <dl className="concept-current-stats">
              <div><dt>To pick</dt><dd>{formatQuantity(instruction?.required_quantity ?? 0)}</dd></div>
              <div><dt>Picked</dt><dd>{formatQuantity(instruction?.picked_quantity ?? 0)}</dd></div>
              <div><dt>Remaining</dt><dd>{formatQuantity(instruction?.remaining_quantity ?? 0)}</dd></div>
              <div><dt>Progress</dt><dd>{progress}%</dd></div>
            </dl>
            <div className="concept-progress-line">
              <div style={{ width: `${progress}%` }} />
            </div>
            <span>{progressText}</span>
          </div>
        </section>

        {!work && (
          <section className="concept-empty-guide">
            <div>
              <strong>Start picking</strong>
              <span>Use the normal scanner flow to load work onto a cart.</span>
            </div>
            <ol>
              <li>Open Tasks</li>
              <li>Choose a picking job</li>
              <li>Scan a cart</li>
              <li>Start picking</li>
            </ol>
            <Link to="/scanner/tasks">Open Tasks</Link>
          </section>
        )}

        <section className="concept-summary-strip">
          <span>Task: <strong>{work ? `#${work.picking_job.id}` : "-"}</strong></span>
          <span>Cart: <strong>{work?.cart_code ?? "-"}</strong></span>
          <span>Progress: <strong>{progress}%</strong></span>
          <span>To pick: <strong>{formatQuantity(totalToPick)}</strong></span>
          <span>Picked: <strong>{formatQuantity(totalPicked)}</strong></span>
          <span>Prepared: <strong>{formatQuantity(totalPrepared)}</strong></span>
          <span>Remaining: <strong>{formatQuantity(totalRemaining)}</strong></span>
        </section>

        <section className="concept-product-card">
          <div className="concept-product-visual">
            <ProductImage alt={instruction?.product.name ?? "Product"} imageUrl={instruction?.product.image_url} />
          </div>
          <div className="concept-product-copy">
            <span className="concept-eyebrow">{instruction?.product.brand || "Brand unavailable"}</span>
            <h1>{instruction?.product.name ?? "No active product"}</h1>
            <strong className="concept-sku">{instruction?.product.sku ?? "-"}</strong>
            <p>{instruction?.product.description || "Product description unavailable."}</p>
            <dl>
              <div><dt>Location</dt><dd>{instruction?.location.code ?? "-"}</dd></div>
              <div><dt>Cart</dt><dd>{work?.cart_code ?? "-"}</dd></div>
              <div><dt>Order</dt><dd>{instruction?.order_reference ?? "-"}</dd></div>
            </dl>
          </div>
          <dl className="concept-product-quantities">
            <div><dt>To pick</dt><dd>{formatQuantity(currentTask?.quantity_to_pick ?? 0)}</dd></div>
            <div><dt>Picked</dt><dd>{formatQuantity(currentTask?.quantity_picked ?? 0)}</dd></div>
            <div><dt>Prepared</dt><dd>{formatQuantity(currentTask?.quantity_prepared ?? 0)}</dd></div>
            <div><dt>Remaining</dt><dd>{formatQuantity(currentTask?.remaining_quantity ?? 0)}</dd></div>
          </dl>
        </section>

        <section className="concept-scan-grid">
          <form
            className={`concept-scan-card ${pickingState === "waiting_for_location" && active ? "concept-scan-card--active" : ""} ${
              pickingState === "waiting_for_product" && active ? "concept-scan-card--confirmed" : ""
            }`}
            onSubmit={handleConfirmLocation}
          >
            <header>
              <span>{pickingState === "waiting_for_product" && active ? "Location confirmed" : "Waiting for location"}</span>
              <strong>Scan the required warehouse location.</strong>
            </header>
            <p>Expected: <b>{instruction?.location.code ?? "-"}</b></p>
            <label>
              <span>Scan location</span>
              <div className="concept-scan-controls">
                <input
                  ref={locationInputRef}
                  disabled={!active || pickingState !== "waiting_for_location"}
                  value={locationCode}
                  onChange={(event) => setLocationCode(event.target.value)}
                  placeholder="Scan location barcode"
                  autoComplete="off"
                />
                <button type="button" disabled={!active || pickingState !== "waiting_for_location"} onClick={() => setCameraMode("location")}>
                  <Camera size={18} />
                </button>
              </div>
            </label>
            {pickingState === "waiting_for_product" && active && <small><CheckCircle2 size={15} />Location confirmed.</small>}
            <button className="sr-only" type="submit">Confirm location</button>
          </form>

          <form
            className={`concept-scan-card ${pickingState === "waiting_for_product" && active ? "concept-scan-card--active" : ""}`}
            onSubmit={handlePick}
          >
            <header>
              <span>{pickingState === "waiting_for_product" && active ? "Waiting for product" : "Scan product locked"}</span>
              <strong>Scan the product barcode or SKU to record the pick.</strong>
            </header>
            <p>Expected: <b>{instruction?.product.sku ?? "-"}</b></p>
            <label>
              <span>Scan product barcode or SKU</span>
              <div className="concept-scan-controls concept-product-scan-controls">
                <input
                  ref={productInputRef}
                  disabled={!active || pickingState !== "waiting_for_product"}
                  value={productCode}
                  onChange={(event) => setProductCode(event.target.value)}
                  placeholder="Scan product barcode"
                  autoComplete="off"
                />
                <button type="button" disabled={!active || pickingState !== "waiting_for_product"} onClick={() => setCameraMode("product")}>
                  <Camera size={18} />
                </button>
              </div>
            </label>
            <div className="concept-quantity-control">
              <span>Quantity</span>
              <div>
                <button type="button" disabled={!active || pickingState !== "waiting_for_product"} onClick={() => adjustQuantity(-1)}>-</button>
                <input
                  disabled={!active || pickingState !== "waiting_for_product"}
                  inputMode="numeric"
                  min={1}
                  max={Math.max(1, Math.floor(currentRemaining))}
                  value={pickQuantity}
                  onBlur={() => setPickQuantity((current) => String(clampQuantity(Number.parseInt(current || "1", 10), currentRemaining)))}
                  onChange={(event) => handleQuantityChange(event.target.value)}
                />
                <button type="button" disabled={!active || pickingState !== "waiting_for_product"} onClick={() => adjustQuantity(1)}>+</button>
              </div>
            </div>
            <button className="sr-only" type="submit">Confirm product</button>
          </form>
        </section>

        {!instruction && work && pickingState === "completed" && (
          <section className="concept-complete-card">
            <strong>Picking completed</strong>
            <span>All required work for this cart is complete.</span>
            <Link to="/scanner/control">Go to Control</Link>
          </section>
        )}

        <section className="concept-manifest">
          <header>
            <div>
              <span>Manifest</span>
              <strong>{tasks.length} products grouped by location</strong>
            </div>
          </header>

          {locationGroups.length === 0 ? (
            <div className="concept-manifest-empty">
              <PackageSearch size={32} />
              <strong>No manifest loaded</strong>
              <span>Select a task to start picking.</span>
              <Link to="/scanner/tasks">Open Tasks</Link>
            </div>
          ) : (
            locationGroups.map((group) => {
              const expanded = autoExpandedLocation === group.location || manualExpandedLocations.has(group.location);
              const complete = group.remaining <= 0;

              return (
                <article className={`concept-location-group ${complete ? "concept-location-group--complete" : ""}`} key={group.location}>
                  <button className="concept-location-toggle" onClick={() => toggleLocation(group.location)} type="button">
                    <div>
                      <strong>{group.location}</strong>
                      <span>{group.name}</span>
                    </div>
                    <div>
                      <span>{group.tasks.length} products</span>
                      <span>To pick: {formatQuantity(group.toPick)}</span>
                      <span>Picked: {formatQuantity(group.picked)}</span>
                      <span>Prepared: {formatQuantity(group.prepared)}</span>
                      <span>Remaining: {formatQuantity(group.remaining)}</span>
                    </div>
                    {complete && <CheckCircle2 size={18} />}
                    <ChevronDown className={expanded ? "is-open" : ""} size={20} />
                  </button>

                  {expanded && (
                    <div className="concept-manifest-products">
                      {group.tasks.map((task) => {
                        const statusLabel = getTaskStatus(task, instruction, nextOpenTaskId);
                        const isComplete = statusLabel === "Completed";
                        const isCurrent = statusLabel === "Current";

                        return (
                          <div
                            className={`concept-manifest-row ${isComplete ? "concept-manifest-row--complete" : ""} ${
                              isCurrent ? "concept-manifest-row--current" : ""
                            }`}
                            key={task.id}
                          >
                            <ProductImage compact alt={task.product_name} imageUrl={task.product_image_url} />
                            <div className="concept-manifest-product">
                              <strong>{task.product_sku}</strong>
                              <span>{task.product_name}</span>
                              <small>{task.product_brand || "Brand unavailable"} - {task.order_reference}</small>
                            </div>
                            <dl>
                              <div><dt>To pick</dt><dd>{formatQuantity(task.quantity_to_pick)}</dd></div>
                              <div><dt>Picked</dt><dd>{formatQuantity(task.quantity_picked)}</dd></div>
                              <div><dt>Prepared</dt><dd>{formatQuantity(task.quantity_prepared)}</dd></div>
                              <div><dt>Remaining</dt><dd>{formatQuantity(task.remaining_quantity)}</dd></div>
                            </dl>
                            <span className={`concept-row-status concept-row-status--${statusLabel.toLowerCase()}`}>{statusLabel}</span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </article>
              );
            })
          )}
        </section>

        <details className="concept-recent-actions">
          <summary>Recent actions</summary>
          {compactActions.map((event) => (
            <div key={event.id}>
              <time>{event.time}</time>
              <span>
                <strong>{event.line}</strong>
                <small>{event.route}</small>
              </span>
            </div>
          ))}
          {compactActions.length === 0 && <p>No picking actions yet.</p>}
          <Link to={`/wms/current-events?event_type=pick${work ? `&cart=${encodeURIComponent(work.cart_code)}` : ""}`}>View all</Link>
        </details>
      </DataState>

      <nav className="concept-mobile-nav">
        <Link to="/scanner/tasks"><ClipboardList size={20} /><span>Tasks</span></Link>
        <Link className="active" to="/scanner/picking"><Archive size={20} /><span>Picking</span></Link>
        <Link to="/wms/inventory"><PackageSearch size={20} /><span>Inventory</span></Link>
        <Link to="/wms/current-events"><History size={20} /><span>History</span></Link>
        <Link to="/scanner"><Menu size={20} /><span>More</span></Link>
      </nav>
    </div>
  );
}
