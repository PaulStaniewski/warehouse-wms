import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "../App";
import { mockApiDefaults, renderWithProviders, setViewport } from "../test/render";

describe("Scanner Home", () => {
  it("groups real scanner workflows and omits placeholder or WMS-only tiles", async () => {
    setViewport(false);
    mockApiDefaults();

    renderWithProviders(<App />, { route: "/scanner" });

    expect(await screen.findByRole("heading", { name: "Warehouse scanner" })).toBeInTheDocument();

    const outbound = screen.getByRole("heading", { name: "Outbound" }).parentElement!;
    expect(within(outbound).getByRole("link", { name: /^Proformas/i })).toBeInTheDocument();
    expect(within(outbound).getByRole("link", { name: /^Tasks/i })).toBeInTheDocument();
    expect(within(outbound).getByRole("link", { name: /^Picking/i })).toBeInTheDocument();
    expect(within(outbound).getByRole("link", { name: /^Control/i })).toBeInTheDocument();

    const inbound = screen.getByRole("heading", { name: "Inbound and transfers" }).parentElement!;
    expect(within(inbound).getByRole("link", { name: /Receiving/i })).toBeInTheDocument();
    expect(within(inbound).getByRole("link", { name: /Pallet Arrivals/i })).toBeInTheDocument();
    expect(within(inbound).getByRole("link", { name: /Quick Transfer/i })).toBeInTheDocument();

    const lookup = screen.getByRole("heading", { name: "Lookup and inventory" }).parentElement!;
    expect(within(lookup).getByRole("link", { name: /Product/i })).toBeInTheDocument();
    expect(within(lookup).getByRole("link", { name: /Contents/i })).toBeInTheDocument();
    expect(within(lookup).getByRole("link", { name: /Cycle Counts/i })).toBeInTheDocument();

    expect(screen.queryByText("Coming soon")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Event Register/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Logout/i })).toBeInTheDocument();
  });
});
