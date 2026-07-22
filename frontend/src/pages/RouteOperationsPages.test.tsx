import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import App from "../App";
import { mockApiClient } from "../test/apiClientMock";
import { authSession, branchMembership, paginated } from "../test/fixtures";
import { renderWithProviders, setViewport } from "../test/render";

function routeRun(overrides = {}) {
  return {
    id: 501,
    route: 41,
    branch: 1,
    route_code: "ROUTE-05",
    route_name: "Trasa 5",
    branch_code: "GDY",
    service_date: "2026-07-22",
    run_number: 1,
    order_cutoff_time: "06:50:00",
    sync_time: "06:50:00",
    departure_time: "07:00:00",
    cutoff_at: "2026-07-21T04:50:00Z",
    planned_departure_at: "2026-07-21T05:00:00Z",
    dispatch_wave: "07:00",
    operational_identifier: "ROUTE-05_WED-1",
    status: "open",
    orders_count: 2,
    order_lines_count: 4,
    picked_lines_count: 2,
    pending_lines_count: 2,
    has_pending_work: true,
    is_urgent: false,
    is_selectable: true,
    total_picking_tasks: 4,
    open_picking_tasks: 1,
    in_progress_picking_tasks: 1,
    picked_picking_tasks: 1,
    completed_picking_tasks: 1,
    active_workers_count: 2,
    unstarted_lines_count: 1,
    started_lines_count: 1,
    picked_line_bucket_count: 1,
    prepared_line_bucket_count: 1,
    total_active_lines: 4,
    attention_status: "cutoff_warning",
    attention_reason: "Cutoff has passed and work remains before departure.",
    minutes_to_departure: 8,
    minutes_after_cutoff: 2,
    operational_weekday: 1,
    readiness_state: "work_remaining",
    remaining_pickable_quantity: "2.000",
    scanner_can_create_picking_job: true,
    scanner_blocking_reason: "",
    progress_percent: 50,
    last_activity_at: null,
    is_ready_to_close: false,
    is_late: false,
    close_result: "unknown",
    ready_at: null,
    documents_printed_at: null,
    closed_at: null,
    ...overrides,
  };
}

function mockBaseApi(routeRuns = [routeRun()]) {
  mockApiClient.get.mockImplementation(async (path: string) => {
    if (path === "/auth/session/") return { data: authSession("GDY_LEADER") };
    if (path === "/me/branch-memberships/") return { data: [branchMembership("leader", "GDY")] };
    if (path.startsWith("/scanner/proformas/")) {
      return {
        data: {
          results: routeRuns.filter((run) => Number(run.remaining_pickable_quantity) > 0).map((run) => ({
            ...run,
            akt: run.active_workers_count,
            lines: run.unstarted_lines_count,
            started: run.started_lines_count,
            picked: run.picked_line_bucket_count,
            prepared: run.prepared_line_bucket_count,
            blocking_reason: run.scanner_blocking_reason,
          })),
        },
      };
    }
    if (path.startsWith("/route-runs/")) return { data: paginated(routeRuns) };
    if (path.startsWith("/mm-tasks/")) return { data: paginated([]) };
    if (path.startsWith("/delivery-routes/")) {
      return { data: paginated([{ id: 41, branch: 1, branch_code: "GDY", code: "ROUTE-05", name: "Trasa 5", is_active: true, created_at: "", updated_at: "" }]) };
    }
    if (path.startsWith("/route-round-schedules/")) {
      return {
        data: paginated([
          {
            id: 71,
            route: 41,
            route_code: "ROUTE-05",
            route_name: "Trasa 5",
            branch: 1,
            branch_code: "GDY",
            weekday: 1,
            weekday_label: "Tuesday",
            round_number: 1,
            cutoff_time: "06:50:00",
            departure_time: "07:00:00",
            dispatch_wave: "07:00",
            operational_label: "",
            is_active: true,
            created_at: "",
            updated_at: "",
          },
        ]),
      };
    }
    if (path.startsWith("/branch-dispatch-policies/")) {
      return { data: paginated([{ id: 9, branch: 1, branch_code: "GDY", max_routes_per_wave: 3, min_wave_gap_minutes: 10, created_at: "", updated_at: "" }]) };
    }
    return { data: paginated([]) };
  });
}

describe("Route operations pages", () => {
  beforeEach(() => {
    setViewport(false);
    mockBaseApi();
    mockApiClient.post.mockResolvedValue({ data: {} });
    mockApiClient.patch.mockResolvedValue({ data: {} });
    mockApiClient.put.mockResolvedValue({ data: {} });
  });

  it("renders Route Monitor with route buckets and attention state", async () => {
    renderWithProviders(<App />, { route: "/wms/routes-monitor" });

    expect((await screen.findAllByText("ROUTE-05_WED-1", {}, { timeout: 5000 })).length).toBeGreaterThan(0);
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Lines")).toBeInTheDocument();
    expect(screen.getByText("Started")).toBeInTheDocument();
    expect(screen.getByText("Picked")).toBeInTheDocument();
    expect(screen.getByText("Prepared")).toBeInTheDocument();
    expect(screen.getByText("Cutoff")).toBeInTheDocument();
    expect(screen.getAllByText(/Cutoff has passed/i).length).toBeGreaterThan(0);
  });

  it("renders canonical identifiers without generic route subtitles or close controls", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/wms/routes-monitor" });

    await user.click((await screen.findAllByText("ROUTE-05_WED-1"))[0]);
    expect(screen.queryByText("Trasa 5")).not.toBeInTheDocument();
    expect(screen.queryByText(/_Sr-/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close route" })).not.toBeInTheDocument();
    expect(screen.getByText("Route is not ready yet.")).toBeInTheDocument();
    expect(mockApiClient.post).not.toHaveBeenCalledWith(expect.stringMatching(/\/route-runs\/\d+\/close\//));
  });
  it("renders neutral, cutoff warning, ready, and delayed route tones", async () => {
    mockBaseApi([
      routeRun({ id: 501, operational_identifier: "ROUTE-neutral", attention_status: "neutral", attention_reason: "Cutoff has not passed.", is_ready_to_close: false }),
      routeRun({ id: 502, operational_identifier: "ROUTE-warning", attention_status: "cutoff_warning", is_ready_to_close: false }),
      routeRun({ id: 503, operational_identifier: "ROUTE-ready", attention_status: "ready", attention_reason: "All active work is prepared.", is_ready_to_close: true }),
      routeRun({ id: 504, operational_identifier: "ROUTE-delayed-ready", attention_status: "delayed", attention_reason: "Departure time has been reached.", is_ready_to_close: true }),
    ]);

    renderWithProviders(<App />, { route: "/wms/routes-monitor" });

    expect((await screen.findAllByText("ROUTE-neutral"))[0].closest("button")).toHaveClass("monitor-route-row--normal");
    expect(screen.getAllByText("ROUTE-warning")[0].closest("button")).toHaveClass("monitor-route-row--attention");
    expect(screen.getAllByText("ROUTE-ready")[0].closest("button")).toHaveClass("monitor-route-row--complete");
    expect(screen.getAllByText("ROUTE-delayed-ready")[0].closest("button")).toHaveClass("monitor-route-row--delayed");
    expect(screen.getByText("1 delayed / 1 cutoff warnings")).toBeInTheDocument();
  });
  it("renders Scanner Proformas in backend order with canonical counters and identifiers", async () => {
    mockBaseApi([
      routeRun({ id: 502, operational_identifier: "ROUTE-05_WED-2", run_number: 2, active_workers_count: 3, unstarted_lines_count: 2 }),
      routeRun({ id: 501, operational_identifier: "ROUTE-05_WED-1", run_number: 1, active_workers_count: 1, unstarted_lines_count: 4 }),
    ]);

    renderWithProviders(<App />, { route: "/scanner/proformas" });

    const identifiers = await screen.findAllByText(/ROUTE-05_WED-[12]/);
    expect(identifiers.map((node) => node.textContent)).toEqual(["ROUTE-05_WED-2", "ROUTE-05_WED-1"]);
    expect(screen.getAllByText("3").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
  });

  it("omits fully prepared routes and preserves API order for remaining picking work", async () => {
    mockBaseApi([
      routeRun({ id: 502, operational_identifier: "PICK-A", remaining_pickable_quantity: "3.000", active_workers_count: 2 }),
      routeRun({
        id: 777,
        operational_identifier: "READY-HIDDEN",
        attention_status: "delayed",
        readiness_state: "ready_to_close",
        remaining_pickable_quantity: "0.000",
        scanner_can_create_picking_job: false,
        scanner_blocking_reason: "Route fully prepared",
        is_selectable: false,
        prepared_line_bucket_count: 1,
        progress_percent: 100,
      }),
      routeRun({ id: 503, operational_identifier: "PICK-C", remaining_pickable_quantity: "1.000", active_workers_count: 4 }),
    ]);

    renderWithProviders(<App />, { route: "/scanner/proformas" });

    const identifiers = await screen.findAllByText(/PICK-[AC]/);
    expect(identifiers.map((node) => node.textContent)).toEqual(["PICK-A", "PICK-C"]);
    expect(screen.queryByText("READY-HIDDEN")).not.toBeInTheDocument();
    expect(screen.queryByText("Route fully prepared")).not.toBeInTheDocument();
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4").length).toBeGreaterThan(0);
  });

  it("shows a friendly no-work state when all active routes are fully picked", async () => {
    mockBaseApi([
      routeRun({
        id: 777,
        operational_identifier: "READY-HIDDEN",
        remaining_pickable_quantity: "0.000",
        scanner_can_create_picking_job: false,
        is_selectable: false,
        progress_percent: 100,
      }),
    ]);

    renderWithProviders(<App />, { route: "/scanner/proformas" });

    expect(await screen.findByText("No routes have remaining picking work for the working branch.")).toBeInTheDocument();
    expect(screen.queryByText("READY-HIDDEN")).not.toBeInTheDocument();
  });

  it("renders Route Schedule Editor and saves policy changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/wms/route-schedules" });

    expect(await screen.findByRole("heading", { name: "Dispatch policy" })).toBeInTheDocument();
    expect((await screen.findAllByText(/Trasa 5/)).length).toBeGreaterThan(0);
    await user.clear(screen.getByLabelText("Maximum routes per wave"));
    await user.type(screen.getByLabelText("Maximum routes per wave"), "2");
    await user.click(screen.getByRole("button", { name: /Save policy/i }));

    await waitFor(() => expect(mockApiClient.patch).toHaveBeenCalledWith(
      "/branch-dispatch-policies/9/",
      expect.objectContaining({ max_routes_per_wave: 2, min_wave_gap_minutes: 10 }),
    ));
  });

  it("submits a new route schedule slot", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/wms/route-schedules" });

    await screen.findByLabelText("Round");
    await user.clear(screen.getByLabelText("Round"));
    await user.type(screen.getByLabelText("Round"), "2");
    await user.clear(screen.getByLabelText("Cutoff"));
    await user.type(screen.getByLabelText("Cutoff"), "10:50");
    await user.clear(screen.getByLabelText("Departure"));
    await user.type(screen.getByLabelText("Departure"), "11:00");
    await user.clear(screen.getByLabelText("Wave"));
    await user.type(screen.getByLabelText("Wave"), "11:00");
    await user.click(screen.getByRole("button", { name: /Add schedule/i }));

    await waitFor(() => expect(mockApiClient.post).toHaveBeenCalledWith(
      "/route-round-schedules/",
      expect.objectContaining({ route: 41, round_number: 2, cutoff_time: "10:50", departure_time: "11:00", dispatch_wave: "11:00" }),
    ));
  });
});