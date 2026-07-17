import { useState } from "react";
import axios from "axios";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useAuth } from "../api/AuthContext";
import { useInterBranchArrivals, useRegisterInterBranchArrival } from "../api/queries";
import { DataState } from "../components/DataState";
import { ScannerScanInput, ScannerStatusMessage } from "../components/scanner/ScannerUi";

function quantity(value: number) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 3 }).format(Number(value));
}

function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

export function ScannerInterBranchArrivalsPage() {
  const { activeBranchCode } = useActiveBranch();
  const { username } = useAuth();
  const [code, setCode] = useState("");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const arrivals = useInterBranchArrivals(activeBranchCode);
  const register = useRegisterInterBranchArrival();

  async function submit(scannedCode: string) {
    try {
      const result = await register.mutateAsync({ palletCode: scannedCode, workerCode: username ?? undefined });
      setMessage({ type: "success", text: `${result.message} Expected units: ${quantity(result.arrival.expected_units)}.` });
      setCode("");
      await arrivals.refetch();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not register pallet arrival.") });
    }
  }

  return (
    <>
      <section className="scanner-home-header">
        <p>Pallet arrivals</p>
        <h1>Register delivered pallets</h1>
      </section>

      {message && <ScannerStatusMessage type={message.type}>{message.text}</ScannerStatusMessage>}

      <section className="scanner-workflow-panel">
        <header>
          <span>1</span>
          <h2>Scan pallet label</h2>
        </header>
        <ScannerScanInput
          autoFocus
          buttonLabel="Register arrival"
          id="arrival-pallet-code"
          isPending={register.isPending}
          label="Pallet label"
          onChange={setCode}
          onSubmit={submit}
          pendingLabel="Registering..."
          placeholder="Scan pallet label"
          value={code}
        />
      </section>

      <section className="scanner-step-card">
        <header>
          <span>2</span>
          <h2>Recent arrivals</h2>
        </header>
        <DataState isLoading={arrivals.isLoading} isError={arrivals.isError} error={arrivals.error}>
          {(arrivals.data?.results ?? []).length === 0 ? (
            <div className="state-box">No pallet arrivals registered for this branch.</div>
          ) : (
            <div className="scanner-compact-list">
              {(arrivals.data?.results ?? []).map((row) => (
                <article className="scanner-compact-row" key={row.pallet_id}>
                  <div>
                    <strong>{row.pallet_code}</strong>
                    <span>{row.transfer_reference}</span>
                    <small>{row.source_branch} to {row.destination_branch}</small>
                  </div>
                  <div>
                    <strong>{quantity(row.expected_units)}</strong>
                    <small>
                      units / {new Date(row.arrived_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
                    </small>
                  </div>
                </article>
              ))}
            </div>
          )}
        </DataState>
      </section>
    </>
  );
}
