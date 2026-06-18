type StatusBadgeProps = {
  active?: boolean;
  label?: string;
  tone?: "ok" | "error" | "loading";
};

export function StatusBadge({ active, label, tone }: StatusBadgeProps) {
  const statusTone = tone ?? (active ? "active" : "inactive");
  const text = label ?? (active ? "Active" : "Inactive");

  return <span className={`status-pill status-pill--${statusTone}`}>{text}</span>;
}
