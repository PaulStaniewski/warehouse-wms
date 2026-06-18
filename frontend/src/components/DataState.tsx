import type { ReactNode } from "react";


type DataStateProps = {
  isLoading: boolean;
  isError: boolean;
  error?: Error | null;
  children: ReactNode;
};

export function DataState({ isLoading, isError, error, children }: DataStateProps) {
  if (isLoading) {
    return <div className="state-box">Loading data...</div>;
  }

  if (isError) {
    return (
      <div className="state-box state-box--error">
        {error?.message || "Could not load data from the API."}
      </div>
    );
  }

  return children;
}
