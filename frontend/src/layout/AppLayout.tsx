import { Boxes, ClipboardList, LayoutDashboard, MapPin, PackageSearch, Warehouse } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";


const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/products", label: "Products", icon: PackageSearch },
  { to: "/inventory", label: "Inventory", icon: Boxes },
  { to: "/orders", label: "Orders", icon: ClipboardList },
  { to: "/locations", label: "Locations", icon: MapPin },
];

const pageTitles: Record<string, string> = {
  "/": "Dashboard",
  "/products": "Products",
  "/inventory": "Inventory",
  "/orders": "Orders",
  "/locations": "Locations",
};

export function AppLayout() {
  const location = useLocation();
  const title = pageTitles[location.pathname] ?? "Warehouse WMS";

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
          {navItems.map((item) => {
            const Icon = item.icon;

            return (
              <NavLink className="nav-link" end={item.to === "/"} key={item.to} to={item.to}>
                <Icon size={18} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
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
