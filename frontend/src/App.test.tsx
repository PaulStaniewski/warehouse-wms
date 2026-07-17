import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "./App";
import { mockApiDefaults, renderWithProviders, setViewport } from "./test/render";
import { branchMembership } from "./test/fixtures";

describe("application routing and layouts", () => {
  it("shows login for unauthenticated root requests", async () => {
    setViewport(false);
    mockApiDefaults({ authenticated: false, memberships: [] });

    renderWithProviders(<App />, { route: "/" });

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("sends authenticated desktop users from root to WMS Dashboard", async () => {
    setViewport(false);
    mockApiDefaults();

    renderWithProviders(<App />, { route: "/" });

    expect(await screen.findByRole("heading", { name: "Warehouse overview" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "WMS navigation" })).toBeInTheDocument();
  });

  it("sends authenticated mobile users from root to Scanner Home", async () => {
    setViewport(true);
    mockApiDefaults();

    renderWithProviders(<App />, { route: "/" });

    expect(await screen.findByRole("heading", { name: "Warehouse scanner" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Scanner/i })).toBeInTheDocument();
  });

  it("keeps explicit WMS routes on mobile", async () => {
    setViewport(true);
    mockApiDefaults();

    renderWithProviders(<App />, { route: "/wms/stock-adjustments" });

    expect((await screen.findAllByRole("heading", { name: "Stock Adjustments" })).length).toBeGreaterThan(0);
    expect(screen.getByRole("navigation", { name: "WMS navigation" })).toBeInTheDocument();
  });

  it("keeps explicit Scanner routes on desktop", async () => {
    setViewport(false);
    mockApiDefaults();

    renderWithProviders(<App />, { route: "/scanner/product" });

    expect(await screen.findByRole("heading", { name: "Scan product" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "WMS navigation" })).not.toBeInTheDocument();
  });

  it("does not switch interfaces after viewport changes", async () => {
    setViewport(false);
    mockApiDefaults();

    renderWithProviders(<App />, { route: "/wms/stock-adjustments" });
    expect((await screen.findAllByRole("heading", { name: "Stock Adjustments" })).length).toBeGreaterThan(0);

    setViewport(true);
    expect(screen.getAllByRole("heading", { name: "Stock Adjustments" }).length).toBeGreaterThan(0);
  });

  it("preserves intended protected route through login", async () => {
    const user = userEvent.setup();
    setViewport(false);
    mockApiDefaults({ authenticated: false, memberships: [branchMembership("leader")] });

    renderWithProviders(<App />, { route: "/wms/events/current?actor=GDY#event-1" });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();

    await user.type(screen.getByLabelText(/Username/i), "GDY_LEADER");
    await user.type(screen.getByLabelText(/Password/i), "demo12345");
    mockApiDefaults({ authenticated: true, memberships: [branchMembership("leader")], username: "GDY_LEADER" });
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect((await screen.findAllByRole("heading", { name: "Event Register" })).length).toBeGreaterThan(0);
    expect(screen.getByText("GDY_LEADER")).toBeInTheDocument();
  });

  it("renders grouped WMS sidebar without Scanner menu entries", async () => {
    setViewport(false);
    mockApiDefaults();

    renderWithProviders(<App />, { route: "/wms/transport-overview" });

    const nav = await screen.findByRole("navigation", { name: "WMS navigation" });
    expect(within(nav).getByText("Transport & Routes")).toBeInTheDocument();
    expect(await within(nav).findByRole("link", { name: /Transport Overview/i })).toBeInTheDocument();
    expect(within(nav).getByText("Exceptions & Investigations")).toBeInTheDocument();
    expect(within(nav).getByText("Events & Audit")).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: /^Picking$/i })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: /Open Scanner/i })).toBeInTheDocument();
  });

  it("uses persisted active branch in WMS and Scanner shells", async () => {
    localStorage.setItem("warehouse-wms-active-branch", "GDA");
    setViewport(false);
    mockApiDefaults({ memberships: [branchMembership("leader", "GDY"), branchMembership("worker", "GDA")] });

    renderWithProviders(<App />, { route: "/scanner" });

    await waitFor(() => expect(screen.getAllByText(/GDA/i).length).toBeGreaterThan(0));
    expect(screen.getByText(/Worker/i)).toBeInTheDocument();
  });

  it("shows access denied when no branch membership is available", async () => {
    setViewport(false);
    mockApiDefaults({ memberships: [] });

    renderWithProviders(<App />, { route: "/wms/dashboard" });

    expect(await screen.findByRole("heading", { name: "Interface unavailable" })).toBeInTheDocument();
  });
});
