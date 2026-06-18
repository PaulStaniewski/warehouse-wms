export type PaginatedResponse<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type HealthResponse = {
  status: string;
};

export type Branch = {
  id: number;
  code: string;
  name: string;
  city: string;
  country: string;
  is_active: boolean;
};

export type Location = {
  id: number;
  branch: number;
  branch_code: string;
  code: string;
  name: string;
  location_type: string;
  is_active: boolean;
};

export type Product = {
  id: number;
  sku: string;
  name: string;
  barcode: string | null;
  unit_of_measure: string;
  is_active: boolean;
};

export type InventoryItem = {
  id: number;
  branch: number;
  branch_code: string;
  location: number;
  location_code: string;
  product: number;
  product_sku: string;
  quantity_on_hand: string;
  quantity_reserved: string;
};

export type Order = {
  id: number;
  branch: number;
  branch_code: string;
  external_reference: string;
  customer_name: string;
  status: string;
  requested_ship_date: string | null;
};
