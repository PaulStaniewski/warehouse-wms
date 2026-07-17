import {
  ArrowRight,
  Boxes,
  ClipboardCheck,
  ClipboardList,
  Forklift,
  Layers,
  PackageSearch,
  RotateCcw,
  ScanLine,
  Truck,
} from "lucide-react";
import { Link } from "react-router-dom";

import { useStoredScannerSession } from "../api/scannerSession";

const menuSections = [
  {
    title: "Outbound",
    items: [
      { label: "Proformas", description: "Select routes and create picking jobs", to: "/scanner/proformas", icon: Layers },
      { label: "Tasks", description: "Choose a job and scan a cart", to: "/scanner/tasks", icon: ClipboardList },
      { label: "Picking", description: "Pick items for the active cart", to: "/scanner/picking", icon: ScanLine },
      { label: "Control", description: "Prepare picked items for customers", to: "/scanner/control", icon: ClipboardCheck },
    ],
  },
  {
    title: "Inbound and transfers",
    items: [
      { label: "Receiving", description: "Receive inter-branch transfer pallets", to: "/scanner/receiving", icon: Truck },
      { label: "Pallet Arrivals", description: "Register inter-branch pallets at destination", to: "/scanner/inter-branch-arrivals", icon: Forklift },
      { label: "Quick Transfer", description: "Move one item between locations", to: "/scanner/quick-transfer", icon: Forklift },
    ],
  },
  {
    title: "Lookup and inventory",
    items: [
      { label: "Product", description: "Lookup SKU or barcode", to: "/scanner/product", icon: PackageSearch },
      { label: "Contents", description: "Scan a location, cart, or label", to: "/scanner/contents", icon: Boxes },
      { label: "Location", description: "Lookup location stock", to: "/scanner/location", icon: PackageSearch },
      { label: "Cycle Counts", description: "Count physical stock by location", to: "/scanner/cycle-counts", icon: ClipboardList },
      { label: "Recounts", description: "Second physical count tasks", to: "/scanner/cycle-count-recounts", icon: RotateCcw },
    ],
  },
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

      <section className="scanner-menu-sections">
        {menuSections.map((section) => (
          <div className="scanner-menu-section" key={section.title}>
            <h2>{section.title}</h2>
            <div className="scanner-menu-grid">
              {section.items.map((item) => {
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
            </div>
          </div>
        ))}
      </section>
    </>
  );
}
