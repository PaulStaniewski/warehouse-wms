import { CalendarClock, Plus, RefreshCw, Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import type { AxiosError } from "axios";

import { useActiveBranch } from "../api/ActiveBranchContext";
import {
  useBranchDispatchPolicies,
  useCreateRouteRoundSchedule,
  useDeliveryRoutes,
  useRouteRoundSchedules,
  useSaveBranchDispatchPolicy,
  useUpdateRouteRoundSchedule,
} from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { RouteRoundSchedule } from "../types/api";

const WEEKDAYS = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
];

type ScheduleFormState = {
  cutoffTime: string;
  departureTime: string;
  dispatchWave: string;
  id: number | null;
  isActive: boolean;
  operationalLabel: string;
  roundNumber: string;
  route: string;
  weekday: string;
};

function emptyForm(route = ""): ScheduleFormState {
  return {
    cutoffTime: "06:50",
    departureTime: "07:00",
    dispatchWave: "07:00",
    id: null,
    isActive: true,
    operationalLabel: "",
    roundNumber: "1",
    route,
    weekday: "0",
  };
}

function errorMessage(error: unknown) {
  const axiosError = error as AxiosError<{ detail?: string; non_field_errors?: string[] }>;
  return axiosError.response?.data?.detail ?? axiosError.response?.data?.non_field_errors?.join(" ") ?? axiosError.message ?? "Action failed.";
}

function timeLabel(value: string) {
  return value?.slice(0, 5) || "-";
}

export function RouteSchedulesPage() {
  const { activeBranch, activeBranchCode, activeMembership } = useActiveBranch();
  const routes = useDeliveryRoutes(activeBranchCode);
  const schedules = useRouteRoundSchedules(activeBranchCode);
  const policies = useBranchDispatchPolicies(activeBranchCode);
  const savePolicy = useSaveBranchDispatchPolicy();
  const createSchedule = useCreateRouteRoundSchedule();
  const updateSchedule = useUpdateRouteRoundSchedule();
  const [message, setMessage] = useState("");
  const [form, setForm] = useState<ScheduleFormState>(() => emptyForm());
  const policy = policies.data?.results[0] ?? null;
  const isLeader = activeMembership?.role === "leader";

  const sortedSchedules = useMemo(
    () =>
      [...(schedules.data?.results ?? [])].sort((left, right) =>
        `${left.weekday}-${left.departure_time}-${left.route_code}-${left.round_number}`.localeCompare(
          `${right.weekday}-${right.departure_time}-${right.route_code}-${right.round_number}`,
        ),
      ),
    [schedules.data?.results],
  );

  useEffect(() => {
    if (!form.route && routes.data?.results[0]) {
      setForm((current) => ({ ...current, route: String(routes.data.results[0].id) }));
    }
  }, [form.route, routes.data?.results]);

  function editSchedule(schedule: RouteRoundSchedule) {
    setMessage("");
    setForm({
      cutoffTime: timeLabel(schedule.cutoff_time),
      departureTime: timeLabel(schedule.departure_time),
      dispatchWave: schedule.dispatch_wave,
      id: schedule.id,
      isActive: schedule.is_active,
      operationalLabel: schedule.operational_label,
      roundNumber: String(schedule.round_number),
      route: String(schedule.route),
      weekday: String(schedule.weekday),
    });
  }

  function resetForm() {
    setForm(emptyForm(routes.data?.results[0] ? String(routes.data.results[0].id) : ""));
    setMessage("");
  }

  async function handlePolicySubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeBranch) return;
    const data = new FormData(event.currentTarget);
    try {
      await savePolicy.mutateAsync({
        branch: activeBranch.id,
        id: policy?.id,
        maxRoutesPerWave: Number(data.get("max_routes_per_wave") || 3),
        minWaveGapMinutes: Number(data.get("min_wave_gap_minutes") || 10),
      });
      setMessage("Dispatch policy saved.");
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  async function handleScheduleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = {
      route: Number(form.route),
      weekday: Number(form.weekday),
      roundNumber: Number(form.roundNumber),
      cutoffTime: form.cutoffTime,
      departureTime: form.departureTime,
      dispatchWave: form.dispatchWave.trim(),
      operationalLabel: form.operationalLabel.trim(),
      isActive: form.isActive,
    };
    try {
      if (form.id) {
        await updateSchedule.mutateAsync({ ...payload, id: form.id });
        setMessage("Route schedule updated.");
      } else {
        await createSchedule.mutateAsync(payload);
        setMessage("Route schedule created.");
      }
      resetForm();
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  function refresh() {
    void routes.refetch();
    void schedules.refetch();
    void policies.refetch();
  }

  if (!activeBranchCode || !activeBranch) {
    return (
      <div className="route-schedules-page">
        <PageHeader
          title="Route Schedules"
          description="Recurring route round templates. Existing route runs keep their own cutoff and departure snapshots."
        />
        <DataState isError={false} isLoading>{null}</DataState>
      </div>
    );
  }

  const dataError = (routes.error || schedules.error || policies.error) as Error | null;

  return (
    <div className="route-schedules-page">
      <PageHeader
        title="Route Schedules"
        description="Recurring route round templates. Existing route runs keep their own cutoff and departure snapshots."
        action={
          <button onClick={refresh} type="button">
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

      {message && <div className="shipment-message"><span>{message}</span><button onClick={() => setMessage("")} type="button">x</button></div>}

      <DataState
        error={dataError}
        isError={routes.isError || schedules.isError || policies.isError}
        isLoading={routes.isLoading || schedules.isLoading || policies.isLoading}
      >
        <section className="route-schedule-layout">
          <div className="panel route-schedule-policy">
            <h2>Dispatch policy</h2>
            <form onSubmit={handlePolicySubmit}>
              <label>
                <span>Maximum routes per wave</span>
                <input defaultValue={policy?.max_routes_per_wave ?? 3} min="1" name="max_routes_per_wave" type="number" />
              </label>
              <label>
                <span>Minimum wave gap minutes</span>
                <input defaultValue={policy?.min_wave_gap_minutes ?? 10} min="0" name="min_wave_gap_minutes" type="number" />
              </label>
              <button disabled={!isLeader || savePolicy.isPending} type="submit">
                <Save size={14} /> Save policy
              </button>
              {!isLeader && <p>Leader role is required to edit dispatch policy.</p>}
            </form>
          </div>

          <div className="panel route-schedule-form">
            <h2>{form.id ? "Edit schedule slot" : "Add schedule slot"}</h2>
            <form onSubmit={handleScheduleSubmit}>
              <label>
                <span>Route</span>
                <select disabled={!isLeader} onChange={(event) => setForm({ ...form, route: event.target.value })} required value={form.route}>
                  <option value="">Select route</option>
                  {(routes.data?.results ?? []).map((route) => (
                    <option key={route.id} value={route.id}>{route.code} / {route.name}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>Weekday</span>
                <select disabled={!isLeader} onChange={(event) => setForm({ ...form, weekday: event.target.value })} value={form.weekday}>
                  {WEEKDAYS.map((day) => <option key={day.value} value={day.value}>{day.label}</option>)}
                </select>
              </label>
              <label>
                <span>Round</span>
                <input disabled={!isLeader} min="1" onChange={(event) => setForm({ ...form, roundNumber: event.target.value })} required type="number" value={form.roundNumber} />
              </label>
              <label>
                <span>Cutoff</span>
                <input disabled={!isLeader} onChange={(event) => setForm({ ...form, cutoffTime: event.target.value })} required type="time" value={form.cutoffTime} />
              </label>
              <label>
                <span>Departure</span>
                <input disabled={!isLeader} onChange={(event) => setForm({ ...form, departureTime: event.target.value })} required type="time" value={form.departureTime} />
              </label>
              <label>
                <span>Wave</span>
                <input disabled={!isLeader} onChange={(event) => setForm({ ...form, dispatchWave: event.target.value })} required value={form.dispatchWave} />
              </label>
              <label>
                <span>Operational label</span>
                <input disabled={!isLeader} onChange={(event) => setForm({ ...form, operationalLabel: event.target.value })} value={form.operationalLabel} />
              </label>
              <label className="shipment-checkbox-label">
                <input checked={form.isActive} disabled={!isLeader} onChange={(event) => setForm({ ...form, isActive: event.target.checked })} type="checkbox" />
                <span>Active</span>
              </label>
              <footer>
                <button disabled={!isLeader || createSchedule.isPending || updateSchedule.isPending} type="submit">
                  <CalendarClock size={14} /> {form.id ? "Save schedule" : "Add schedule"}
                </button>
                <button onClick={resetForm} type="button">
                  <Plus size={14} /> New
                </button>
              </footer>
              {!isLeader && <p>Leader role is required to edit schedules.</p>}
            </form>
          </div>
        </section>

        <section className="panel route-schedule-table-panel">
          <div className="route-schedule-table-header">
            <div>
              <h2>Weekly schedule</h2>
              <p>{activeBranchCode} / {sortedSchedules.length} schedule slots</p>
            </div>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Route</th>
                  <th>Weekday</th>
                  <th>Round</th>
                  <th>Cutoff</th>
                  <th>Departure</th>
                  <th>Wave</th>
                  <th>Active</th>
                  <th>Validation status</th>
                </tr>
              </thead>
              <tbody>
                {sortedSchedules.map((schedule) => (
                  <tr key={schedule.id}>
                    <td>
                      <button className="table-link" onClick={() => editSchedule(schedule)} type="button">
                        {schedule.route_code} / {schedule.route_name}
                      </button>
                    </td>
                    <td>{schedule.weekday_label}</td>
                    <td>{schedule.round_number}</td>
                    <td>{timeLabel(schedule.cutoff_time)}</td>
                    <td>{timeLabel(schedule.departure_time)}</td>
                    <td>{schedule.dispatch_wave}</td>
                    <td>{schedule.is_active ? "Yes" : "No"}</td>
                    <td>{schedule.is_active ? "Validated by server" : "Inactive"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {sortedSchedules.length === 0 && <div className="state-box">No route schedules found for this branch.</div>}
        </section>
      </DataState>
    </div>
  );
}
