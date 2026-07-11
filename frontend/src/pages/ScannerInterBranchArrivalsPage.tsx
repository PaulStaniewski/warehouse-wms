import { type FormEvent, useEffect, useRef, useState } from "react";
import axios from "axios";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useAuth } from "../api/AuthContext";
import { useInterBranchArrivals, useRegisterInterBranchArrival } from "../api/queries";
import { PageHeader } from "../components/PageHeader";

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
  const inputRef = useRef<HTMLInputElement>(null);
  const arrivals = useInterBranchArrivals(activeBranchCode);
  const register = useRegisterInterBranchArrival();

  useEffect(() => inputRef.current?.focus(), []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!code.trim()) return;
    try {
      const result = await register.mutateAsync({ palletCode: code.trim(), workerCode: username ?? undefined });
      setMessage({ type: "success", text: `${result.message} Expected units: ${quantity(result.arrival.expected_units)}.` });
      setCode("");
      await arrivals.refetch();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not register pallet arrival.") });
    } finally {
      inputRef.current?.focus();
    }
  }

  return (
    <>
      <PageHeader title="Inter-branch pallet arrivals" description="Scan pallet labels delivered from another branch." />
      <form className="scanner-workflow-panel" onSubmit={submit}>
        <label>
          <span>Pallet label</span>
          <input ref={inputRef} autoComplete="off" value={code} onChange={(event) => setCode(event.target.value)} />
        </label>
        <button disabled={!code.trim() || register.isPending} type="submit">Register arrival</button>
        {message && <p className={`scanner-message scanner-message--${message.type}`}>{message.text}</p>}
      </form>
      <section className="data-card">
        <h2>Recent scans</h2>
        <div className="table-wrap"><table><thead><tr><th>Pallet</th><th>Transfer</th><th>From</th><th>To</th><th>Expected units</th><th>Arrival result</th><th>Time</th></tr></thead>
          <tbody>{(arrivals.data?.results ?? []).map((row) => <tr key={row.pallet_id}><td>{row.pallet_code}</td><td>{row.transfer_reference}</td><td>{row.source_branch}</td><td>{row.destination_branch}</td><td>{quantity(row.expected_units)}</td><td>Registered</td><td>{new Date(row.arrived_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</td></tr>)}</tbody>
        </table></div>
      </section>
    </>
  );
}
