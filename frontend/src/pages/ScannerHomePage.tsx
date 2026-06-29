import { ArrowRight, ClipboardList, Forklift, MapPin, PackageSearch, ScanLine, Truck } from "lucide-react";
import { Link } from "react-router-dom";


const menuItems = [
  { label: "Proformy / Trasy", description: "Picking by route run", to: "/scanner/routes", icon: ClipboardList },
  { label: "Produkt", description: "Lookup SKU or barcode", to: "/scanner/product", icon: PackageSearch },
  { label: "Lokalizacja", description: "Show location contents", to: "/scanner/location", icon: MapPin },
  { label: "Szybkie przeniesienie", description: "Move one item between locations", to: "/scanner/quick-transfer", icon: Forklift },
];

const disabledItems = [
  { label: "Spedycja / MM", icon: Truck },
  { label: "Przyjecie palety", icon: ScanLine },
  { label: "Inwentaryzacja", icon: ClipboardList },
];

export function ScannerHomePage() {
  return (
    <>
      <section className="scanner-home-header">
        <p>Scanner module</p>
        <h1>Warehouse scanner</h1>
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
