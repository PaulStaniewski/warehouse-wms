import {
  Archive,
  Barcode,
  Boxes,
  ClipboardCheck,
  ListChecks,
  ClipboardList,
  Forklift,
  History,
  LayoutDashboard,
  MapPin,
  PackageSearch,
  ArchiveRestore,
  Layers,
  ScanLine,
  Route,
  Warehouse,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useStoredScannerSession } from "../api/scannerSession";


const wmsNavItems = [
  { to: "/wms/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/wms/routes-monitor", label: "Routes Monitor", icon: Route },
  { to: "/wms/routes/archive", label: "Routes Archive", icon: ArchiveRestore },
  { to: "/wms/discrepancy-actions", label: "Action Queue", icon: ListChecks },
  { to: "/wms/discrepancies", label: "Discrepancies", icon: ClipboardCheck },
  { to: "/wms/source-discrepancy-reviews", label: "Source Reviews", icon: ClipboardList },
  { to: "/wms/discrepancy-reconciliations", label: "Reconciliations", icon: ClipboardCheck },
  { to: "/wms/source-stock-verifications", label: "Source Stock", icon: Boxes },
  { to: "/wms/transit-investigations", label: "Transit", icon: Route },
  { to: "/wms/orders", label: "Orders", icon: ClipboardList },
  { to: "/wms/inventory", label: "Inventory", icon: Boxes },
  { to: "/wms/products", label: "Products", icon: PackageSearch },
  { to: "/wms/locations", label: "Locations", icon: MapPin },
  { to: "/wms/events/current", label: "Current Events", icon: History },
  { to: "/wms/events/archive", label: "Archive Events", icon: Archive },
];

const scannerNavItems = [
  { to: "/scanner", label: "Scanner Menu", icon: ScanLine },
  { to: "/scanner/proformas", label: "Proformas", icon: Layers },
  { to: "/scanner/tasks", label: "Tasks", icon: ClipboardList },
  { to: "/scanner/picking", label: "Picking", icon: Barcode },
  { to: "/scanner/control", label: "Control", icon: ClipboardCheck },
  { to: "/scanner/receiving", label: "Receiving", icon: ArchiveRestore },
  { to: "/scanner/product", label: "Product", icon: PackageSearch },
  { to: "/scanner/contents", label: "Contents", icon: Boxes },
  { to: "/scanner/location", label: "Location", icon: MapPin },
  { to: "/scanner/quick-transfer", label: "Quick Transfer", icon: Forklift },
];

const pageTitles: Record<string, string> = {
  "/wms/dashboard": "Dashboard",
  "/wms/products": "Products",
  "/wms/inventory": "Inventory",
  "/wms/orders": "Orders",
  "/wms/locations": "Locations",
  "/wms/routes-monitor": "Route Monitor",
  "/wms/routes/archive": "Routes Archive",
  "/wms/discrepancy-actions": "Discrepancy Action Queue",
  "/wms/discrepancies": "Discrepancies",
  "/wms/source-discrepancy-reviews": "Source Reviews",
  "/wms/discrepancy-reconciliations": "Reconciliations",
  "/wms/source-stock-verifications": "Source Stock",
  "/wms/transit-investigations": "Transit Investigations",
  "/wms/events/current": "Current Events",
  "/wms/events/archive": "Archive Events",
  "/scanner": "Scanner",
  "/scanner/proformas": "Proformas",
  "/scanner/tasks": "Tasks",
  "/scanner/picking": "Picking",
  "/scanner/control": "Control",
  "/scanner/receiving": "Receiving",
  "/scanner/routes": "Picking",
  "/scanner/route-runs": "Scanner",
  "/scanner/product": "Product Lookup",
  "/scanner/contents": "Contents",
  "/scanner/location": "Location Lookup",
  "/scanner/quick-transfer": "Quick Transfer",
};

export function AppLayout() {
  const location = useLocation();
  const scannerSession = useStoredScannerSession();
  const title = pageTitles[location.pathname] ?? (location.pathname.startsWith("/scanner") ? "Scanner" : "Warehouse WMS");

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Warehouse size={22} />
          </div>
          <div>
            <span className="brand-title">Warehouse WMS</span>
            <span className="brand-subtitle">Read-only console</span>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="Main navigation">
          <div className="nav-section">
            <span className="nav-section-title">WMS</span>
            {wmsNavItems.map((item) => {
              const Icon = item.icon;

              return (
                <NavLink className="nav-link" key={item.to} to={item.to}>
                  <Icon size={18} />
                  <span>{item.label}</span>
                </NavLink>
              );
            })}
          </div>

          <div className="nav-section">
            <span className="nav-section-title">Scanner</span>
            {scannerNavItems.map((item) => {
              const Icon = item.icon;

              return (
                <NavLink className="nav-link" key={item.to} to={item.to}>
                  <Icon size={18} />
                  <span>{item.label}</span>
                </NavLink>
              );
            })}
          </div>
        </nav>
      </aside>

      <div className="main-panel">
        <header className="topbar">
          <h2 className="topbar-title">{title}</h2>
          <span className="topbar-meta">
            {location.pathname.startsWith("/scanner") && scannerSession
              ? `Cart: ${scannerSession.cart_code}`
              : "API: /api"}
          </span>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
