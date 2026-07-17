import axios from "axios";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { canManageCycleCounts } from "../api/permissions";
import { useCreateCycleCount, useLocationSearch } from "../api/queries";
import { PageHeader } from "../components/PageHeader";

function errorText(error: unknown) {
  if (!axios.isAxiosError(error)) return "Cycle count could not be created.";
  const data = error.response?.data;
  if (data?.detail) return String(data.detail);
  if (!data || typeof data !== "object") return "Cycle count could not be created.";
  return Object.entries(data).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(" ") : String(value)}`).join(" ");
}

export function CycleCountCreatePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { activeBranchCode, activeMembership } = useActiveBranch();
  const [search, setSearch] = useState("");
  const [selectedLocationIds, setSelectedLocationIds] = useState<number[]>([]);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const locations = useLocationSearch(activeBranchCode, search);
  const createCount = useCreateCycleCount();
  const canManage = canManageCycleCounts(activeMembership);

  function toggleLocation(locationId: number) {
    setSelectedLocationIds((current) =>
      current.includes(locationId) ? current.filter((id) => id !== locationId) : [...current, locationId],
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    if (!canManage) {
      setMessage("Leader access is required.");
      return;
    }
    if (selectedLocationIds.length === 0) {
      setMessage("Select at least one location.");
      return;
    }
    try {
      const session = await createCount.mutateAsync({ branch: activeBranchCode, locationIds: selectedLocationIds, name, note });
      await queryClient.invalidateQueries({ queryKey: ["cycle-counts"] });
      navigate(`/wms/cycle-counts/${session.id}`);
    } catch (error) {
      setMessage(errorText(error));
    }
  }

  if (!canManage) {
    return (
      <>
        <PageHeader title="New Cycle Count" description="Cycle count creation requires Leader access." action={<Link className="status-pill" to="/wms/cycle-counts">Back to Cycle Counts</Link>} />
        <div className="state-box">You are not authorized to create cycle counts for this branch.</div>
      </>
    );
  }

  return (
    <>
      <PageHeader title="New Cycle Count" description={`Create a draft location-based count for ${activeBranchCode}.`} action={<Link className="status-pill" to="/wms/cycle-counts">Back to Cycle Counts</Link>} />
      {message && <div className="scanner-message scanner-message--error">{message}</div>}
      <form className="adjustment-form" onSubmit={submit}>
        <section className="filter-panel">
          <label>
            <span>Name</span>
            <input onChange={(event) => setName(event.target.value)} placeholder="Optional session name" value={name} />
          </label>
          <label>
            <span>Location search</span>
            <input onChange={(event) => setSearch(event.target.value)} placeholder="Location code or name" value={search} />
          </label>
        </section>
        <label className="adjustment-note-field">
          <span>Note</span>
          <textarea onChange={(event) => setNote(event.target.value)} placeholder="Optional count instructions." rows={3} value={note} />
        </label>
        <section className="detail-grid">
          {(locations.data?.results ?? []).map((location) => (
            <button
              className={selectedLocationIds.includes(location.id) ? "detail-card status-pill--ok" : "detail-card"}
              key={location.id}
              onClick={() => toggleLocation(location.id)}
              type="button"
            >
              <span>{location.code}</span>
              <strong>{location.name}</strong>
            </button>
          ))}
        </section>
        <div className="pagination-bar">
          <span>{selectedLocationIds.length} selected locations</span>
          <button disabled={createCount.isPending || selectedLocationIds.length === 0} type="submit">
            {createCount.isPending ? "Creating..." : "Create draft session"}
          </button>
        </div>
      </form>
    </>
  );
}
