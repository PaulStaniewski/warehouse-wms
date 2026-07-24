export function formatQuantity(value: string | number | null | undefined) {
  const numericValue = Number(value ?? 0);
  if (!Number.isFinite(numericValue)) {
    return String(value ?? "");
  }
  return new Intl.NumberFormat("en-GB", {
    maximumFractionDigits: 3,
    minimumFractionDigits: 0,
    useGrouping: true,
  }).format(numericValue);
}