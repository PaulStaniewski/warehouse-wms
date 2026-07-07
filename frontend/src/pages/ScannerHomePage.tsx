import {
  ArrowRight,
  Boxes,
  ClipboardCheck,
  ClipboardList,
  Forklift,
  Layers,
  PackageSearch,
  ScanLine,
  Truck,
} from "lucide-react";
import { Link } from "react-router-dom";

import { useStoredScannerSession } from "../api/scannerSession";


const menuItems = [
  { label: "Proformas", description: "Select routes and create picking jobs", to: "/scanner/proformas", icon: Layers },
  { label: "Tasks", description: "Choose a job and scan a cart", to: "/scanner/tasks", icon: ClipboardList },
  { label: "Picking", description: "Pick items for the active cart", to: "/scanner/picking", icon: ScanLine },
  { label: "Control", description: "Prepare picked items for customers", to: "/scanner/control", icon: ClipboardCheck },
  { label: "Receiving", description: "Receive inter-branch transfer pallets", to: "/scanner/receiving", icon: Truck },
  { label: "Product", description: "Lookup SKU or barcode", to: "/scanner/product", icon: PackageSearch },
  { label: "Contents", description: "Scan a location, cart, or label", to: "/scanner/contents", icon: Boxes },
  { label: "Location", description: "Lookup location stock", to: "/scanner/location", icon: PackageSearch },
  { label: "Quick Transfer", description: "Move one item between locations", to: "/scanner/quick-transfer", icon: Forklift },
];

const disabledItems = [
  { label: "Floor", icon: Layers },
  { label: "Inventory Tasks", icon: ClipboardList },
];

export function ScannerHomePage() {
  const activeSession = useStoredScannerSession();

  return (
    <>
      <section className="scanner-home-header">
        <p>Scanner module</p>
        <h1>Warehouse scanner</h1>
      </section>

      <section className="scanner-cart-panel scanner-cart-panel--compact">
        <div>
          <span>Active work</span>
          <strong>{activeSession ? activeSession.cart_code : "No active cart"}</strong>
          {activeSession?.cart_work_session && <small>Cart work #{activeSession.cart_work_session}</small>}
        </div>
      </section>

      <section className="scanner-menu-grid">
        {menuItems.map((item) => {
          const Icon = item.icon;

          return (
            <Link className="scanner-menu-card" key={item.to} to={item.to}>
              <Icon size={32} />
              <div>
                <strong>{item.label}</strong>
                <span>{item.description}</span>
              </div>
              <ArrowRight size={24} />
            </Link>
          );
        })}

        {disabledItems.map((item) => {
          const Icon = item.icon;

          return (
            <article className="scanner-menu-card scanner-menu-card--disabled" key={item.label}>
              <Icon size={32} />
              <div>
                <strong>{item.label}</strong>
                <span>Coming soon</span>
              </div>
            </article>
          );
        })}
      </section>
    </>
  );
}
