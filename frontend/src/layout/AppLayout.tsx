import {
  Archive,
  Barcode,
  Boxes,
  ClipboardList,
  History,
  LayoutDashboard,
  MapPin,
  PackageSearch,
  Route,
  Warehouse,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";


const wmsNavItems = [
  { to: "/wms/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/wms/routes-monitor", label: "Routes Monitor", icon: Route },
  { to: "/wms/orders", label: "Orders", icon: ClipboardList },
  { to: "/wms/inventory", label: "Inventory", icon: Boxes },
  { to: "/wms/products", label: "Products", icon: PackageSearch },
  { to: "/wms/locations", label: "Locations", icon: MapPin },
  { to: "/wms/events/current", label: "Current Events", icon: History },
  { to: "/wms/events/archive", label: "Archive Events", icon: Archive },
];

const scannerNavItems = [
  { to: "/scanner/routes", label: "Routes / Picking", icon: Barcode },
];

const pageTitles: Record<string, string> = {
  "/wms/dashboard": "Dashboard",
  "/wms/products": "Products",
  "/wms/inventory": "Inventory",
  "/wms/orders": "Orders",
  "/wms/locations": "Locations",
  "/wms/routes-monitor": "Route Monitor",
  "/wms/events/current": "Current Events",
  "/wms/events/archive": "Archive Events",
  "/scanner/routes": "Scanner",
};

export function AppLayout() {
  const location = useLocation();
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
          <span className="topbar-meta">API: /api</span>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
