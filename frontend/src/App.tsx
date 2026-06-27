import { Navigate, Route, Routes } from "react-router-dom";

import "./App.css";
import { AppLayout } from "./layout/AppLayout";
import { ArchiveEventsPage } from "./pages/ArchiveEventsPage";
import { CurrentEventsPage } from "./pages/CurrentEventsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { InventoryPage } from "./pages/InventoryPage";
import { LocationsPage } from "./pages/LocationsPage";
import { OrdersPage } from "./pages/OrdersPage";
import { ProductsPage } from "./pages/ProductsPage";
import { RouteMonitorPage } from "./pages/RouteMonitorPage";
import { ScannerPickingPage } from "./pages/ScannerPickingPage";
import { ScannerRoutesPage } from "./pages/ScannerRoutesPage";

function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/wms/dashboard" replace />} />
        <Route path="wms/dashboard" element={<DashboardPage />} />
        <Route path="wms/products" element={<ProductsPage />} />
        <Route path="wms/inventory" element={<InventoryPage />} />
        <Route path="wms/orders" element={<OrdersPage />} />
        <Route path="wms/locations" element={<LocationsPage />} />
        <Route path="wms/routes-monitor" element={<RouteMonitorPage />} />
        <Route path="wms/events/current" element={<CurrentEventsPage />} />
        <Route path="wms/events/archive" element={<ArchiveEventsPage />} />
        <Route path="scanner/routes" element={<ScannerRoutesPage />} />
        <Route path="scanner/route-runs/:id/picking" element={<ScannerPickingPage />} />
        <Route path="products" element={<Navigate to="/wms/products" replace />} />
        <Route path="inventory" element={<Navigate to="/wms/inventory" replace />} />
        <Route path="orders" element={<Navigate to="/wms/orders" replace />} />
        <Route path="locations" element={<Navigate to="/wms/locations" replace />} />
        <Route path="routes-monitor" element={<Navigate to="/wms/routes-monitor" replace />} />
        <Route path="*" element={<Navigate to="/wms/dashboard" replace />} />
      </Route>
    </Routes>
  );
}

export default App;
