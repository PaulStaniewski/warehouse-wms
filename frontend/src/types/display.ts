export function sourceVerificationStatusLabel(status: string, statusLabel?: string) {
  if (statusLabel) {
    return statusLabel;
  }
  if (status === "completed_unresolved") {
    return "Completed with unresolved stock";
  }
  if (status === "pending_verification") {
    return "Pending verification";
  }
  if (status === "investigating") {
    return "Investigating";
  }
  if (status === "completed") {
    return "Completed";
  }
  return status;
}

export function sourceVerificationPageDescription(status?: string) {
  if (status === "investigating" || status === "pending_verification") {
    return "Restore physically found source stock.";
  }
  if (status === "completed") {
    return "Source stock verification completed.";
  }
  if (status === "completed_unresolved") {
    return "Review source stock verification results.";
  }
  return "Source stock verification outcome.";
}
