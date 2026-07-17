import { useState } from "react";
import axios from "axios";
import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";

import { useScannerProductLookup } from "../api/queries";
import { ScannerScanInput, ScannerStatusMessage } from "../components/scanner/ScannerUi";


function getErrorMessage(error: unknown) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || error.message : "Could not load product.";
}

export function ScannerProductLookupPage() {
  const [inputCode, setInputCode] = useState("");
  const [searchCode, setSearchCode] = useState("");
  const lookup = useScannerProductLookup(searchCode);

  function handleSubmit(value: string) {
    setSearchCode(value);
  }

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner">
          <ArrowLeft size={17} />
          Scanner menu
        </Link>
      </div>

      <section className="scanner-tool-panel">
        <div>
          <p>Product lookup</p>
          <h1>Scan product</h1>
        </div>
        <ScannerScanInput
          autoFocus
          buttonLabel="Search"
          id="product-code"
          isPending={lookup.isFetching}
          label="Scan product SKU, barcode, or code"
          onChange={setInputCode}
          onSubmit={handleSubmit}
          pendingLabel="Searching..."
          placeholder="Scan or type code and press Enter"
          value={inputCode}
        />
      </section>

      {lookup.isError && <ScannerStatusMessage type="error">{getErrorMessage(lookup.error)}</ScannerStatusMessage>}

      {!searchCode && <div className="state-box">Scan a product to see current stock positions.</div>}

      {lookup.data && (
        <>
          <section className="scanner-result-card">
            <div>
              <span>SKU</span>
              <strong>{lookup.data.product.sku}</strong>
            </div>
            <div>
              <span>Name</span>
              <strong>{lookup.data.product.name}</strong>
            </div>
            <div>
              <span>Barcode</span>
              <strong>{lookup.data.product.barcode ?? "-"}</strong>
            </div>
            <div>
              <span>Unit</span>
              <strong>{lookup.data.product.unit_of_measure}</strong>
            </div>
          </section>

          {lookup.data.inventory_positions.length === 0 ? (
            <div className="state-box">Product exists, but no stock positions were found.</div>
          ) : (
            <section className="scanner-list">
              {lookup.data.inventory_positions.map((item) => (
                <article className="scanner-list-row" key={item.id}>
                  <div>
                    <span>{item.branch_code}</span>
                    <strong>{item.location_code}</strong>
                    {item.location_name && <small>{item.location_name}</small>}
                  </div>
                  <div>
                    <span>On hand</span>
                    <strong>{item.quantity_on_hand}</strong>
                  </div>
                </article>
              ))}
            </section>
          )}
        </>
      )}
    </>
  );
}
