import {
  ArchiveRestore,
  AlertTriangle,
  ArrowLeft,
  Barcode,
  Boxes,
  ChevronDown,
  ClipboardCheck,
  ClipboardList,
  ExternalLink,
  History,
  Home,
  LayoutDashboard,
  ListChecks,
  LogOut,
  MapPin,
  PackageSearch,
  Route,
  ScanLine,
  Truck,
  Warehouse,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useAuth } from "../api/AuthContext";
import { useStoredScannerSession } from "../api/scannerSession";
import type { BranchMembership } from "../types/api";

type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  activePatterns?: string[];
  minimumRole?: BranchMembership["role"];
};

type NavSection = {
  id: string;
  label: string;
  items: NavItem[];
};

const roleRank: Record<BranchMembership["role"], number> = {
  worker: 1,
  leader: 2,
};

const dashboardItem: NavItem = {
  to: "/wms/dashboard",
  label: "Dashboard",
  icon: LayoutDashboard,
};

const wmsNavSections: NavSection[] = [
  {
    id: "operations",
    label: "Operations",
    items: [
      { to: "/wms/discrepancy-actions", label: "Action Queue", icon: ListChecks },
      { to: "/wms/shipments", label: "Shipments", icon: Truck },
      { to: "/wms/orders", label: "Orders", icon: ClipboardList },
      { to: "/wms/replenishment-requests", label: "Replenishment", icon: ArchiveRestore },
    ],
  },
  {
    id: "transport-routes",
    label: "Transport & Routes",
    items: [
      {
        to: "/wms/transport-overview",
        label: "Transport Overview",
        icon: Truck,
      },
      {
        to: "/wms/routes-monitor",
        label: "Routes Monitor",
        icon: Route,
        activePatterns: ["/wms/routes-monitor", "/wms/route-runs"],
      },
      { to: "/wms/transit-investigations", label: "Transit", icon: Route },
      { to: "/wms/routes/archive", label: "Routes Archive", icon: ArchiveRestore },
    ],
  },
  {
    id: "stock-locations",
    label: "Stock & Locations",
    items: [
      { to: "/wms/inventory", label: "Inventory", icon: Boxes },
      { to: "/wms/products", label: "Products", icon: PackageSearch },
    ],
  },
  {
    id: "inventory-operations",
    label: "Inventory Operations",
    items: [
      { to: "/wms/stock-transfers", label: "Stock Transfers", icon: Boxes },
      { to: "/wms/stock-adjustments", label: "Stock Adjustments", icon: ClipboardList },
      { to: "/wms/returns", label: "Returns", icon: ArchiveRestore },
      { to: "/wms/sales-corrections", label: "Sales Corrections", icon: ClipboardCheck },
      { to: "/wms/cycle-counts", label: "Cycle Counts", icon: ClipboardCheck },
      { to: "/wms/cycle-count-review-queue", label: "Review Queue", icon: ListChecks, minimumRole: "leader" },
    ],
  },
  {
    id: "exceptions-investigations",
    label: "Exceptions & Investigations",
    items: [
      { to: "/wms/inventory-exceptions", label: "Inventory Exceptions", icon: AlertTriangle },
      { to: "/wms/picking-shortages", label: "Picking Shortages", icon: PackageSearch },
      { to: "/wms/discrepancies", label: "Discrepancies", icon: ClipboardCheck },
      { to: "/wms/source-discrepancy-reviews", label: "Source Reviews", icon: ClipboardList },
      { to: "/wms/discrepancy-reconciliations", label: "Reconciliations", icon: ClipboardCheck },
      { to: "/wms/source-stock-verifications", label: "Source Stock", icon: Boxes },
    ],
  },
  {
    id: "events-audit",
    label: "Events & Audit",
    items: [
      {
        to: "/wms/events/current",
        label: "Event Register",
        icon: History,
        activePatterns: ["/wms/events", "/wms/current-events"],
      },
      { to: "/wms/reports/correction-activity", label: "Correction Activity Report", icon: History },
    ],
  },
  {
    id: "administration",
    label: "Administration",
    items: [
      { to: "/wms/branches", label: "Branches", icon: Warehouse },
      { to: "/wms/locations", label: "Locations", icon: MapPin },
    ],
  },
];

const scannerPageTitles: Array<[string, string]> = [
  ["/scanner/inter-branch-arrivals", "Inter-branch pallet arrivals"],
  ["/scanner/cycle-count-recounts", "Cycle Count Recounts"],
  ["/scanner/cycle-counts", "Cycle Counts"],
  ["/scanner/quick-transfer", "Quick Transfer"],
  ["/scanner/proformas", "Proformas"],
  ["/scanner/contents", "Contents"],
  ["/scanner/receiving", "Receiving"],
  ["/scanner/location", "Location Lookup"],
  ["/scanner/product", "Product Lookup"],
  ["/scanner/control", "Control"],
  ["/scanner/picking", "Picking"],
  ["/scanner/tasks", "Tasks"],
  ["/scanner", "Scanner"],
];

const wmsPageTitles: Array<[string, string]> = [
  ["/wms/events", "Event Register"],
  ["/wms/transport-overview", "Transport Overview"],
  ["/wms/inventory-exceptions", "Inventory Exceptions"],
  ["/wms/source-stock-verifications", "Source Stock"],
  ["/wms/discrepancy-reconciliations", "Reconciliations"],
  ["/wms/source-discrepancy-reviews", "Source Reviews"],
  ["/wms/replenishment-requests", "Replenishment Requests"],
  ["/wms/transit-investigations", "Transit Investigations"],
  ["/wms/discrepancy-actions", "Discrepancy Action Queue"],
  ["/wms/shipments", "Shipments"],
  ["/wms/picking-shortages", "Picking Shortages"],
  ["/wms/route-runs", "Route Documents"],
  ["/wms/routes-monitor", "Route Monitor"],
  ["/wms/routes/archive", "Routes Archive"],
  ["/wms/discrepancies", "Discrepancies"],
  ["/wms/branches", "Branches"],
  ["/wms/cycle-counts", "Cycle Counts"],
  ["/wms/cycle-count-review-queue", "Cycle Count Review Queue"],
  ["/wms/stock-adjustments", "Stock Adjustments"],
  ["/wms/returns", "Returns"],
  ["/wms/sales-corrections", "Sales Corrections"],
  ["/wms/reports/correction-activity", "Correction Activity Report"],
  ["/wms/stock-transfers", "Stock Transfers"],
  ["/wms/dashboard", "Dashboard"],
  ["/wms/products", "Products"],
  ["/wms/inventory", "Inventory"],
  ["/wms/locations", "Locations"],
  ["/wms/orders", "Orders"],
];

function pathMatches(pathname: string, pattern: string) {
  return pathname === pattern || pathname.startsWith(`${pattern}/`);
}

function itemIsActive(pathname: string, item: NavItem) {
  const patterns = item.activePatterns ?? [item.to];
  return patterns.some((pattern) => pathMatches(pathname, pattern));
}

function visibleNavItems(items: NavItem[], membership: BranchMembership | null) {
  return items.filter((item) => {
    if (!item.minimumRole) {
      return true;
    }
    if (!membership) {
      return false;
    }
    return roleRank[membership.role] >= roleRank[item.minimumRole];
  });
}

function titleForPath(pathname: string, titles: Array<[string, string]>, fallback: string) {
  return titles.find(([pattern]) => pathMatches(pathname, pattern))?.[1] ?? fallback;
}

function WmsSidebarSection({
  activeMembership,
  defaultOpen,
  pathname,
  section,
}: {
  activeMembership: BranchMembership | null;
  defaultOpen: boolean;
  pathname: string;
  section: NavSection;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const items = visibleNavItems(section.items, activeMembership);
  const hasActiveChild = items.some((item) => itemIsActive(pathname, item));

  useEffect(() => {
    if (hasActiveChild) {
      setIsOpen(true);
    }
  }, [hasActiveChild, pathname]);

  if (items.length === 0) {
    return null;
  }

  return (
    <div className="nav-section nav-section--collapsible">
      <button
        aria-expanded={isOpen}
        className="nav-section-toggle"
        onClick={() => setIsOpen((value) => !value)}
        type="button"
      >
        <span>{section.label}</span>
        <ChevronDown className={isOpen ? "nav-section-chevron is-open" : "nav-section-chevron"} size={16} />
      </button>
      {isOpen && (
        <div className="nav-section-items">
          {items.map((item) => {
            const Icon = item.icon;

            return (
              <NavLink className={() => (itemIsActive(pathname, item) ? "nav-link active" : "nav-link")} key={item.to} to={item.to}>
                <Icon size={18} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function WmsLayout() {
  const location = useLocation();
  const auth = useAuth();
  const { activeBranchCode, activeMembership, branches, setActiveBranchCode } = useActiveBranch();
  const title = titleForPath(location.pathname, wmsPageTitles, "Warehouse WMS");
  const visibleDashboard = visibleNavItems([dashboardItem], activeMembership);

  const activeSectionIds = useMemo(
    () =>
      new Set(
        wmsNavSections
          .filter((section) => section.items.some((item) => itemIsActive(location.pathname, item)))
          .map((section) => section.id),
      ),
    [location.pathname],
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Warehouse size={22} />
          </div>
          <div>
            <span className="brand-title">Warehouse WMS</span>
            <span className="brand-subtitle">Operations console</span>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="WMS navigation">
          <div className="sidebar-nav-scroll">
            {visibleDashboard.length > 0 && (
              <div className="nav-section">
                <span className="nav-section-title">Dashboard</span>
                <NavLink
                  className={() => (itemIsActive(location.pathname, dashboardItem) ? "nav-link active" : "nav-link")}
                  to={dashboardItem.to}
                >
                  <LayoutDashboard size={18} />
                  <span>{dashboardItem.label}</span>
                </NavLink>
              </div>
            )}

            {wmsNavSections.map((section) => (
              <WmsSidebarSection
                activeMembership={activeMembership}
                defaultOpen={activeSectionIds.has(section.id)}
                key={section.id}
                pathname={location.pathname}
                section={section}
              />
            ))}
          </div>

          <a className="nav-link nav-link--scanner" href="/scanner" rel="noopener noreferrer" target="_blank">
            <ScanLine size={18} />
            <span>Open Scanner</span>
            <ExternalLink size={14} />
          </a>
        </nav>
      </aside>

      <div className="main-panel">
        <header className="topbar">
          <h2 className="topbar-title">{title}</h2>
          <span className="topbar-meta">API: /api</span>
          <label className="topbar-branch-selector">
            <span>Working branch:</span>
            <select onChange={(event) => setActiveBranchCode(event.target.value)} value={activeBranchCode}>
              {branches.map((branch) => (
                <option key={branch.code} value={branch.code}>
                  {branch.code} / {branch.name}
                </option>
              ))}
            </select>
            {activeMembership && <span className="branch-role">Role: {activeMembership.role_label}</span>}
          </label>
          {auth.isAuthenticated && (
            <div className="topbar-user">
              <span>{auth.username}</span>
              <button onClick={() => void auth.logout()} type="button">
                Logout
              </button>
            </div>
          )}
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function ScannerLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();
  const { activeBranch, activeBranchCode, activeMembership } = useActiveBranch();
  const scannerSession = useStoredScannerSession();
  const title = titleForPath(location.pathname, scannerPageTitles, "Scanner");
  const isHome = location.pathname === "/scanner";

  return (
    <div className="scanner-shell">
      <header className="scanner-topbar">
        <NavLink className="scanner-topbar-brand" to="/scanner">
          <ScanLine size={20} />
          <span>Scanner</span>
        </NavLink>
        <div className="scanner-topbar-title">
          <h2>{title}</h2>
          <span>
            {activeBranchCode
              ? `${activeBranchCode}${activeBranch?.name ? ` / ${activeBranch.name}` : ""}`
              : "No active branch"}
            {activeMembership?.role_label ? ` / ${activeMembership.role_label}` : ""}
          </span>
        </div>
        <span className="scanner-topbar-meta">{scannerSession ? `Cart: ${scannerSession.cart_code}` : "No active cart"}</span>
        <div className="scanner-topbar-actions">
          {!isHome && (
            <button aria-label="Go back" onClick={() => navigate(-1)} type="button">
              <ArrowLeft size={17} />
              Back
            </button>
          )}
          {!isHome && (
            <NavLink aria-label="Scanner home" to="/scanner">
              <Home size={17} />
              Home
            </NavLink>
          )}
          {auth.isAuthenticated && (
            <button onClick={() => void auth.logout()} type="button">
              <LogOut size={17} />
              Logout
            </button>
          )}
        </div>
      </header>
      <main className="scanner-content">
        <Outlet />
      </main>
    </div>
  );
}
