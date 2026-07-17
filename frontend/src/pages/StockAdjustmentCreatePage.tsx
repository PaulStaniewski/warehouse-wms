import axios from "axios";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { canCreateStockAdjustment } from "../api/permissions";
import {
  useCreateStockAdjustment,
  useInventoryPosition,
  useLocationSearch,
  useProductSearch,
} from "../api/queries";
import { PageHeader } from "../components/PageHeader";
import type { Location, Product } from "../types/api";

const reasonOptions = [
  { value: "count_correction", label: "Count correction" },
  { value: "damaged_stock", label: "Damaged stock" },
  { value: "found_stock", label: "Found stock" },
  { value: "data_entry_correction", label: "Data entry correction" },
  { value: "other", label: "Other" },
];

function decimal(value: string | null | undefined) {
  const parsed = Number(value ?? "0");
  return Number.isFinite(parsed) ? parsed : 0;
}

function backendError(error: unknown) {
  if (!axios.isAxiosError(error)) return "Stock adjustment could not be created.";
  const data = error.response?.data;
  if (!data || typeof data !== "object") return "Stock adjustment could not be created.";
  if ("detail" in data && typeof data.detail === "string") return data.detail;
  return Object.entries(data)
    .map(([field, messages]) => `${field}: ${Array.isArray(messages) ? messages.join(" ") : String(messages)}`)
    .join(" ");
}

export function StockAdjustmentCreatePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { activeBranchCode, activeMembership } = useActiveBranch();
  const isAuthorized = canCreateStockAdjustment(activeMembership);
  const [productSearch, setProductSearch] = useState("");
  const [locationSearch, setLocationSearch] = useState("");
  const [selectedProductId, setSelectedProductId] = useState("");
  const [selectedLocationId, setSelectedLocationId] = useState("");
  const [direction, setDirection] = useState<"increase" | "decrease">("increase");
  const [quantity, setQuantity] = useState("1");
  const [reasonCode, setReasonCode] = useState("count_correction");
  const [note, setNote] = useState("");
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const products = useProductSearch(productSearch);
  const locations = useLocationSearch(activeBranchCode, locationSearch);
  const preview = useInventoryPosition(activeBranchCode, selectedLocationId, selectedProductId);
  const createAdjustment = useCreateStockAdjustment();
  const selectedProduct = products.data?.results.find((product) => String(product.id) === selectedProductId) ?? null;
  const selectedLocation = locations.data?.results.find((location) => String(location.id) === selectedLocationId) ?? null;
  const currentQuantity = decimal(preview.data?.results[0]?.quantity_on_hand);
  const adjustmentQuantity = decimal(quantity);
  const estimatedQuantity = direction === "increase" ? currentQuantity + adjustmentQuantity : currentQuantity - adjustmentQuantity;
  const decreaseTooLarge = direction === "decrease" && adjustmentQuantity > currentQuantity;
  const selectedReason = reasonOptions.find((option) => option.value === reasonCode)?.label ?? reasonCode;
  const canReview = Boolean(
    isAuthorized &&
      activeBranchCode &&
      selectedProduct &&
      selectedLocation &&
      adjustmentQuantity > 0 &&
      reasonCode &&
      note.trim().length >= 5 &&
      !decreaseTooLarge,
  );

  useEffect(() => {
    setSelectedLocationId("");
    setLocationSearch("");
    setShowConfirmation(false);
    setErrorMessage("");
  }, [activeBranchCode]);

  const productOptions = useMemo(() => products.data?.results ?? [], [products.data]);
  const locationOptions = useMemo(() => locations.data?.results ?? [], [locations.data]);

  function submitReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorMessage("");
    if (!canReview) {
      setErrorMessage("Complete all fields and review the quantity before continuing.");
      return;
    }
    setShowConfirmation(true);
  }

  async function confirmCreate() {
    if (!selectedProduct || !selectedLocation) return;
    setErrorMessage("");
    try {
      const adjustment = await createAdjustment.mutateAsync({
        branch: activeBranchCode,
        direction,
        location: selectedLocation.id,
        note,
        product: selectedProduct.id,
        quantity,
        reasonCode,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["stock-adjustments"] }),
        queryClient.invalidateQueries({ queryKey: ["inventory-items", activeBranchCode] }),
        queryClient.invalidateQueries({ queryKey: ["inventory-position", activeBranchCode, selectedLocation.id, selectedProduct.id] }),
        queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
      ]);
      navigate(`/wms/stock-adjustments/${adjustment.id}`);
    } catch (error) {
      setErrorMessage(backendError(error));
      setShowConfirmation(false);
    }
  }

  if (!isAuthorized) {
    return (
      <>
        <PageHeader
          title="New Stock Adjustment"
          description="Manual stock corrections require Leader access in the active branch."
          action={<Link className="status-pill" to="/wms/stock-adjustments">Back to Stock Adjustments</Link>}
        />
        <div className="state-box">You are not authorized to create stock adjustments for this branch.</div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="New Stock Adjustment"
        description={`Create an immutable manual stock correction for ${activeBranchCode || "the active branch"}.`}
        action={<Link className="status-pill" to="/wms/stock-adjustments">Back to Stock Adjustments</Link>}
      />

      {errorMessage && <div className="scanner-message scanner-message--error">{errorMessage}</div>}

      <form className="adjustment-form" onSubmit={submitReview}>
        <section className="filter-panel">
          <label>
            <span>Product search</span>
            <input
              onChange={(event) => {
                setProductSearch(event.target.value);
                setSelectedProductId("");
              }}
              placeholder="SKU, barcode or name"
              value={productSearch}
            />
          </label>
          <label>
            <span>Product</span>
            <select onChange={(event) => setSelectedProductId(event.target.value)} required value={selectedProductId}>
              <option value="">Select product</option>
              {productOptions.map((product: Product) => (
                <option key={product.id} value={product.id}>
                  {product.sku} / {product.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Location search</span>
            <input
              onChange={(event) => {
                setLocationSearch(event.target.value);
                setSelectedLocationId("");
              }}
              placeholder="Code or name"
              value={locationSearch}
            />
          </label>
          <label>
            <span>Location</span>
            <select onChange={(event) => setSelectedLocationId(event.target.value)} required value={selectedLocationId}>
              <option value="">Select location</option>
              {locationOptions.map((location: Location) => (
                <option key={location.id} value={location.id}>
                  {location.code} / {location.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Direction</span>
            <select onChange={(event) => setDirection(event.target.value as "increase" | "decrease")} value={direction}>
              <option value="increase">Increase</option>
              <option value="decrease">Decrease</option>
            </select>
          </label>
          <label>
            <span>Quantity</span>
            <input min="0.001" onChange={(event) => setQuantity(event.target.value)} step="0.001" type="number" value={quantity} />
          </label>
          <label>
            <span>Reason</span>
            <select onChange={(event) => setReasonCode(event.target.value)} value={reasonCode}>
              {reasonOptions.map((reason) => (
                <option key={reason.value} value={reason.value}>
                  {reason.label}
                </option>
              ))}
            </select>
          </label>
        </section>

        <section className="adjustment-create-grid">
          <label className="adjustment-note-field">
            <span>Explanation note</span>
            <textarea
              onChange={(event) => setNote(event.target.value)}
              placeholder="Explain why this manual stock correction is needed."
              required
              rows={5}
              value={note}
            />
          </label>

          <aside className="adjustment-preview-panel">
            <span>Current stock preview</span>
            <strong>{preview.isLoading ? "Loading..." : currentQuantity.toFixed(3)}</strong>
            <p>
              {direction === "increase" ? "Increase" : "Decrease"} by {adjustmentQuantity > 0 ? adjustmentQuantity.toFixed(3) : "-"}
            </p>
            <p>Estimated result: {Number.isFinite(estimatedQuantity) ? estimatedQuantity.toFixed(3) : "-"}</p>
            {decreaseTooLarge && <p className="scanner-inline-error">Decrease exceeds the currently recorded quantity.</p>}
            <small>The backend rechecks this quantity inside the transaction.</small>
          </aside>
        </section>

        <div className="pagination-bar">
          <span>Completed adjustments are immutable operational history.</span>
          <button disabled={!canReview} type="submit">Review adjustment</button>
        </div>
      </form>

      {showConfirmation && selectedProduct && selectedLocation && (
        <section aria-modal="true" className="adjustment-confirmation" role="dialog">
          <div className="adjustment-confirmation-panel">
            <h2>Confirm stock adjustment</h2>
            <dl>
              <div><dt>Branch</dt><dd>{activeBranchCode}</dd></div>
              <div><dt>Product</dt><dd>{selectedProduct.sku} / {selectedProduct.name}</dd></div>
              <div><dt>Location</dt><dd>{selectedLocation.code} / {selectedLocation.name}</dd></div>
              <div><dt>Direction</dt><dd>{direction === "increase" ? "Increase" : "Decrease"}</dd></div>
              <div><dt>Quantity</dt><dd>{adjustmentQuantity.toFixed(3)}</dd></div>
              <div><dt>Current quantity</dt><dd>{currentQuantity.toFixed(3)}</dd></div>
              <div><dt>Estimated result</dt><dd>{estimatedQuantity.toFixed(3)}</dd></div>
              <div><dt>Reason</dt><dd>{selectedReason}</dd></div>
              <div><dt>Note</dt><dd>{note}</dd></div>
            </dl>
            <p>This completed adjustment cannot be edited or deleted.</p>
            <div className="access-denied-actions">
              <button disabled={createAdjustment.isPending} onClick={() => setShowConfirmation(false)} type="button">
                Cancel
              </button>
              <button disabled={createAdjustment.isPending} onClick={confirmCreate} type="button">
                {createAdjustment.isPending ? "Creating..." : "Create adjustment"}
              </button>
            </div>
          </div>
        </section>
      )}
    </>
  );
}
