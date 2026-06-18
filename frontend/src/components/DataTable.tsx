import type { ReactNode } from "react";


type DataTableProps<T> = {
  rows: T[];
  columns: Array<{
    key: string;
    header: string;
    render: (row: T) => ReactNode;
  }>;
  emptyMessage: string;
};

export function DataTable<T>({ rows, columns, emptyMessage }: DataTableProps<T>) {
  if (rows.length === 0) {
    return <div className="state-box">{emptyMessage}</div>;
  }

  return (
    <div className="panel">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key}>{column.header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {columns.map((column) => (
                  <td key={column.key}>{column.render(row)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
