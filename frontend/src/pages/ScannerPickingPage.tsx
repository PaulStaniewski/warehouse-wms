import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import {
  Archive,
  ArrowLeft,
  AlertTriangle,
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
import {
  useCurrentAuditLogs,
  useScannerCartWork,
  useScannerCartWorkClaim,
  useScannerCartWorkJoin,
  useScannerCartWorkLeave,
  useScannerConfirmLocation,
  useScannerPickingPick,
  useScannerPickingReportShortage,
  useScannerPickingShortageChallenge,
} from "../api/queries";
import { clearStoredScannerCartWork, storeScannerSession, useStoredScannerSession } from "../api/scannerSession";
import { CameraBarcodeScanner } from "../components/scanner/CameraBarcodeScanner";
import { DataState } from "../components/DataState";
import type { AuditLog, PickingShortageChallenge, PickingTask, PickInstruction } from "../types/api";

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

  if (pickingState === "waiting_for_available_line") {
    return "Waiting for other workers";
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

  if (task.status === "waiting_replenishment") {
    return "Location shortage";
  }

  if (toNumber(task.remaining_quantity) <= 0) {
    return "Completed";
  }

  if (task.claimed_by_username && !task.is_claimed_by_current_user) {
    return `Handled by ${task.claimed_by_username}`;
  }

  if (task.id === nextOpenTaskId) {
    return "Next";
  }

  return "Open";
}

function statusClassName(statusLabel: string) {
  return statusLabel.toLowerCase().replace(/\s+/g, "-");
}

export function ScannerPickingPage() {
  const { activeBranchCode } = useActiveBranch();
  const queryClient = useQueryClient();
  const activeSession = useStoredScannerSession();
  const [staleSessionMessage, setStaleSessionMessage] = useState(false);
  const cartWork = useScannerCartWork(activeSession?.id, activeSession?.cart_work_session, {
    onStaleSession: () => {
      clearStoredScannerCartWork();
      queryClient.removeQueries({ queryKey: ["scanner-cart-work"] });
      setStaleSessionMessage(true);
    },
  });
  const confirmLocation = useScannerConfirmLocation();
  const joinCartWork = useScannerCartWorkJoin();
  const claimLine = useScannerCartWorkClaim();
  const leaveCartWork = useScannerCartWorkLeave();
  const scannerPick = useScannerPickingPick();
  const shortageChallengeMutation = useScannerPickingShortageChallenge();
  const reportShortageMutation = useScannerPickingReportShortage();
  const [workerCode] = useState(activeSession?.worker_code || "DEMO");
  const [locationCode, setLocationCode] = useState("");
  const [productCode, setProductCode] = useState("");
  const [joinCartCode, setJoinCartCode] = useState("");
  const [pickQuantity, setPickQuantity] = useState("1");
  const [cameraMode, setCameraMode] = useState<"location" | "product" | null>(null);
  const [message, setMessage] = useState<ScanMessage | null>(null);
  const [shortageOpen, setShortageOpen] = useState(false);
  const [shortageQuantity, setShortageQuantity] = useState("1");
  const [shortageChallenge, setShortageChallenge] = useState<PickingShortageChallenge | null>(null);
  const [shortageCode, setShortageCode] = useState("");
  const [shortageNote, setShortageNote] = useState("");
  const [autoExpandedLocation, setAutoExpandedLocation] = useState<string | null>(null);
  const [manualExpandedLocations, setManualExpandedLocations] = useState<Set<string>>(() => new Set());
  const locationInputRef = useRef<HTMLInputElement | null>(null);
  const productInputRef = useRef<HTMLInputElement | null>(null);

  const hasStoredCartWork = Boolean(activeSession?.cart_work_session);
  const tasks = hasStoredCartWork ? cartWork.data?.tasks ?? [] : [];
  const work = activeSession?.cart_work_session ? cartWork.data?.cart_work_session : undefined;
  const participant = hasStoredCartWork ? cartWork.data?.participant ?? null : null;
  const instruction = hasStoredCartWork ? cartWork.data?.current_instruction ?? null : null;
  const pickingState = hasStoredCartWork ? cartWork.data?.state ?? "waiting_for_location" : "waiting_for_location";
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
  const activeWorkers = work?.participants ?? [];

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
    if (!staleSessionMessage) {
      return;
    }
    setMessage({
      type: "warning",
      title: "Previous picking session is no longer available.",
      detail: "Open Tasks to start new work.",
    });
  }, [staleSessionMessage]);

  useEffect(() => {
    const repairs = cartWork.data?.repair_messages ?? [];
    if (repairs.length === 0) {
      return;
    }
    setMessage({
      type: "success",
      title: "Picking location updated.",
      detail: repairs[repairs.length - 1],
    });
    setLocationCode("");
    window.setTimeout(() => locationInputRef.current?.focus(), 0);
  }, [cartWork.data?.repair_messages]);

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
      queryClient.invalidateQueries({ queryKey: ["picking-shortages"] }),
      queryClient.invalidateQueries({ queryKey: ["replenishment-requests"] }),
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

  async function handleJoinCartWork(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cartBarcode = joinCartCode.trim();
    if (!cartBarcode) {
      return;
    }
    setMessage(null);
    try {
      const result = await joinCartWork.mutateAsync({ cartBarcode });
      if (!result.session) {
        throw new Error("Missing scanner session.");
      }
      storeScannerSession({
        ...result.session,
        cart_work_session: result.cart_work_session.id,
        picking_job: result.cart_work_session.picking_job.id,
      });
      setJoinCartCode("");
      setMessage({ type: "success", title: "Cart work joined.", detail: `You joined ${result.cart_work_session.cart_code}.` });
      await refreshPickingData();
    } catch (error) {
      setMessage({ type: "error", title: "Could not join cart work.", detail: getErrorMessage(error, "Try again.") });
    }
  }

  async function handleClaimLine(pickingTaskId?: number, direction?: "beginning" | "end") {
    if (!work) {
      return;
    }
    setMessage(null);
    try {
      const result = await claimLine.mutateAsync({
        cartWorkSessionId: work.id,
        direction,
        mode: pickingTaskId ? "specific" : direction,
        pickingTaskId,
      });
      setMessage({
        type: "success",
        title: "Picking line selected.",
        detail: result.current_instruction
          ? `${result.current_instruction.product.sku} at ${result.current_instruction.location.code}`
          : "No open picking lines remain.",
      });
      await refreshPickingData();
      window.setTimeout(() => locationInputRef.current?.focus(), 0);
    } catch (error) {
      setMessage({ type: "error", title: "Could not select this line.", detail: getErrorMessage(error, "Try again.") });
    }
  }

  async function handleLeaveCartWork() {
    if (!work) {
      return;
    }
    try {
      await leaveCartWork.mutateAsync({ cartWorkSessionId: work.id });
      clearStoredScannerCartWork();
      queryClient.removeQueries({ queryKey: ["scanner-cart-work"] });
      setMessage({ type: "success", title: "Cart work left.", detail: "Open Tasks or join another active cart." });
    } catch (error) {
      setMessage({ type: "error", title: "Could not leave cart work.", detail: getErrorMessage(error, "Try again.") });
    }
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

  function openShortagePanel() {
    setShortageOpen(true);
    setShortageChallenge(null);
    setShortageCode("");
    setShortageNote("");
    setShortageQuantity(String(clampQuantity(Math.floor(currentRemaining), currentRemaining)));
  }

  async function generateShortageChallenge() {
    if (!work || !instruction) {
      return;
    }
    const quantity = clampQuantity(Number.parseInt(shortageQuantity || "1", 10), currentRemaining);
    setShortageQuantity(String(quantity));
    setMessage(null);
    try {
      const challenge = await shortageChallengeMutation.mutateAsync({
        cartWorkSessionId: work.id,
        quantity: String(quantity),
        workerCode,
      });
      setShortageChallenge(challenge);
      setShortageCode("");
    } catch (error) {
      setMessage({ type: "error", title: "Could not generate missing-stock confirmation.", detail: getErrorMessage(error, "Try again.") });
    }
  }

  async function confirmShortage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!shortageChallenge) {
      await generateShortageChallenge();
      return;
    }
    try {
      const response = await reportShortageMutation.mutateAsync({
        challengeToken: shortageChallenge.challenge_token,
        confirmationCode: shortageCode,
        clientOperationId: crypto.randomUUID(),
        note: shortageNote,
      });
      setMessage({
        type: "success",
        title: "Missing stock recorded.",
        detail: response.message,
      });
      setShortageOpen(false);
      setShortageChallenge(null);
      setShortageCode("");
      setShortageNote("");
      await refreshPickingData();
      window.setTimeout(() => locationInputRef.current?.focus(), 0);
    } catch (error) {
      setMessage({ type: "error", title: "Could not record missing stock.", detail: getErrorMessage(error, "Try again.") });
    }
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
              <strong>No active picking work</strong>
              <span>Join work already in progress or open Tasks to start new work.</span>
            </div>
            <form className="scanner-join-work" onSubmit={handleJoinCartWork}>
              <label>
                <span>Scan a cart already being picked by another operator.</span>
                <input
                  autoComplete="off"
                  onChange={(event) => setJoinCartCode(event.target.value)}
                  placeholder="Scan cart barcode"
                  value={joinCartCode}
                />
              </label>
              <button disabled={!joinCartCode.trim() || joinCartWork.isPending} type="submit">
                Join cart work
              </button>
            </form>
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
          {participant && <span>Picking mode: <strong>{participant.picking_direction_label}</strong></span>}
          {work && (
            <button disabled={leaveCartWork.isPending} onClick={() => void handleLeaveCartWork()} type="button">
              Leave cart work
            </button>
          )}
        </section>

        {work && (
          <section className="scanner-workers-panel">
            <strong>Active workers: {activeWorkers.length}</strong>
            <div>
              {activeWorkers.map((worker) => (
                <span className={worker.is_current_user ? "is-current-worker" : ""} key={worker.id}>
                  {worker.is_current_user ? "You" : worker.username}
                  {worker.current_product_sku ? ` - ${worker.current_product_sku} / ${worker.current_location_code ?? "-"}` : " - no line selected"}
                </span>
              ))}
            </div>
          </section>
        )}

        {work && !instruction && pickingState === "waiting_for_available_line" && (
          <section className="concept-complete-card">
            <strong>Waiting for other workers</strong>
            <span>No unclaimed picking lines are currently available. Shared cart work is still in progress.</span>
          </section>
        )}

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
            <div><dt>Missing at location</dt><dd>{formatQuantity(currentTask?.shortage_quantity ?? 0)}</dd></div>
            <div><dt>Prepared</dt><dd>{formatQuantity(currentTask?.quantity_prepared ?? 0)}</dd></div>
            <div><dt>Remaining</dt><dd>{formatQuantity(currentTask?.remaining_quantity ?? 0)}</dd></div>
            <button
              className="concept-shortage-button"
              disabled={!active || pickingState !== "waiting_for_product" || currentRemaining <= 0}
              onClick={openShortagePanel}
              type="button"
            >
              <AlertTriangle size={18} />
              Report missing at location
            </button>
          </dl>
        </section>

        {shortageOpen && instruction && work && (
          <section className="concept-shortage-panel">
            <header>
              <div>
                <span>REPORT MISSING STOCK AT LOCATION</span>
                <h2>{instruction.product.sku} - {instruction.product.name}</h2>
                <p>Record missing stock at the expected location. The system will check alternative locations before customer replenishment.</p>
              </div>
              <button type="button" onClick={() => setShortageOpen(false)}>Close</button>
            </header>
            <form onSubmit={confirmShortage}>
              <dl>
                <div><dt>Brand</dt><dd>{instruction.product.brand || "Brand unavailable"}</dd></div>
                <div><dt>Location</dt><dd>{activeBranchCode} / {instruction.location.code}</dd></div>
                <div><dt>Order</dt><dd>{instruction.order_reference}</dd></div>
                <div><dt>Customer alias</dt><dd>{instruction.customer_alias || "-"}</dd></div>
                <div><dt>Required</dt><dd>{formatQuantity(instruction.required_quantity)}</dd></div>
                <div><dt>Picked</dt><dd>{formatQuantity(instruction.picked_quantity)}</dd></div>
              </dl>
              <label>
                <span>Missing quantity at location</span>
                <input
                  disabled={Boolean(shortageChallenge)}
                  inputMode="numeric"
                  max={Math.max(1, Math.floor(currentRemaining))}
                  min={1}
                  value={shortageQuantity}
                  onChange={(event) => setShortageQuantity(String(clampQuantity(Number.parseInt(event.target.value || "1", 10), currentRemaining)))}
                />
              </label>
              {!shortageChallenge ? (
                <button disabled={shortageChallengeMutation.isPending} type="button" onClick={generateShortageChallenge}>
                  Generate confirmation code
                </button>
              ) : (
                <div className="concept-shortage-challenge">
                  <span>Confirmation code</span>
                  <strong>{shortageChallenge.confirmation_code}</strong>
                  <label>
                    <span>Enter the displayed code</span>
                    <input
                      inputMode="numeric"
                      maxLength={4}
                      value={shortageCode}
                      onChange={(event) => setShortageCode(event.target.value.replace(/\D/g, "").slice(0, 4))}
                    />
                  </label>
                  <label>
                    <span>Note</span>
                    <input value={shortageNote} onChange={(event) => setShortageNote(event.target.value)} />
                  </label>
                  <button disabled={reportShortageMutation.isPending || shortageCode.length !== 4} type="submit">
                    Confirm missing stock
                  </button>
                </div>
              )}
            </form>
          </section>
        )}

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

        {!instruction && work && pickingState === "completed" && ["picked", "completed"].includes(work.picking_job.status) && (
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
            {work && (
              <div className="scanner-claim-actions">
                <button disabled={claimLine.isPending} onClick={() => void handleClaimLine(undefined, "beginning")} type="button">
                  Pick from beginning
                </button>
                <button disabled={claimLine.isPending} onClick={() => void handleClaimLine(undefined, "end")} type="button">
                  Pick from end
                </button>
              </div>
            )}
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
                        const handledByAnother = Boolean(task.claimed_by_username && !task.is_claimed_by_current_user && !isComplete);
                        const openForClaim = work && !isComplete && !isCurrent && !handledByAnother && toNumber(task.remaining_quantity) > 0;

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
                              <div className={toNumber(task.shortage_quantity) > 0 ? "concept-shortage-cell" : ""}>
                                <dt>Missing</dt><dd>{formatQuantity(task.shortage_quantity)}</dd>
                              </div>
                              <div><dt>Prepared</dt><dd>{formatQuantity(task.quantity_prepared)}</dd></div>
                              <div><dt>Remaining</dt><dd>{formatQuantity(task.remaining_quantity)}</dd></div>
                            </dl>
                            {task.is_replacement_pick && (
                              <span className="concept-replacement-note">
                                Replacement for {task.replacement_shortage_reference} from {task.original_shortage_location_code}
                              </span>
                            )}
                            {task.is_system_reallocated_pick && (
                              <span className="concept-replacement-note">
                                {task.reallocation_reason || `Reallocated from ${task.reallocated_from_location_code}`}
                              </span>
                            )}
                            <span className={`concept-row-status concept-row-status--${statusClassName(statusLabel)}`}>
                              {handledByAnother ? `Handled by ${task.claimed_by_username}` : statusLabel}
                            </span>
                            {openForClaim && (
                              <button
                                className="scanner-line-claim-button"
                                disabled={claimLine.isPending}
                                onClick={() => void handleClaimLine(task.id)}
                                type="button"
                              >
                                Pick this line
                              </button>
                            )}
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
