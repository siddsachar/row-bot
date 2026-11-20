import { useMemo, useState, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown, Table } from 'lucide-react';
import { copyUnavailableMessage } from '../../platform/clipboard';
import { CopyGlyph, Hint, IconButton } from '../../ui/primitives';
import {
  columnType,
  isNumeric,
  nextSort,
  plainCell,
  sortedOrder,
  tableCsv,
  type TableSort,
} from './sortable-table';

const COPIED = 'Table copied as CSV.';

/**
 * A table in an answer: a scrolling frame with a sticky header, every column
 * sortable from its header (ascending, descending, the answer's own order),
 * numbers right-aligned, and "Copy as CSV" in the order shown.
 */
export function SortableTable({
  headers,
  rows,
  renderCell = (value) => value,
  copyText,
  label,
}: {
  /** Header and cell source text; sorting and CSV read it as plain text. */
  headers: string[];
  rows: string[][];
  renderCell?: (value: string) => ReactNode;
  copyText?: (value: string) => Promise<boolean>;
  /** The table's accessible name, when it has a title. */
  label?: string;
}) {
  const [sort, setSort] = useState<TableSort>(null);
  const [status, setStatus] = useState('');
  const types = useMemo(
    () =>
      headers.map((_, column) =>
        columnType(rows.map((row) => row[column] ?? '')),
      ),
    [headers, rows],
  );
  const order = useMemo(
    () => sortedOrder(rows, types, sort),
    [rows, types, sort],
  );
  const shown = order.map((index) => rows[index]);
  async function copy() {
    try {
      const ok = await (copyText ?? (async () => false))(
        tableCsv(headers, shown),
      );
      setStatus(ok ? COPIED : copyUnavailableMessage());
    } catch {
      setStatus('The table could not be copied.');
    }
  }
  return (
    <div className="markdown-table" data-tools={copyText ? 'true' : undefined}>
      <div
        className="markdown-table-scroll"
        tabIndex={0}
        role={label ? 'region' : undefined}
        aria-label={label || undefined}
      >
        <table>
          <thead>
            <tr>
              {headers.map((header, column) => {
                const name = plainCell(header) || `column ${column + 1}`;
                const direction =
                  sort?.column === column ? sort.direction : undefined;
                const Glyph =
                  direction === 'ascending'
                    ? ArrowUp
                    : direction === 'descending'
                      ? ArrowDown
                      : ArrowUpDown;
                return (
                  <th
                    key={column}
                    aria-sort={direction ?? 'none'}
                    data-numeric={isNumeric(types[column]) || undefined}
                  >
                    <Hint label={`Sort by ${name}`}>
                      <button
                        type="button"
                        className="table-sort"
                        data-active={direction ? 'true' : undefined}
                        onClick={() =>
                          setSort((current) => nextSort(current, column))
                        }
                      >
                        <span>{renderCell(header)}</span>
                        <Glyph
                          size={13}
                          aria-hidden
                          className="table-sort-glyph"
                        />
                      </button>
                    </Hint>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, rowIndex) => (
              <tr key={order[rowIndex]}>
                {headers.map((_, column) => (
                  <td
                    key={column}
                    data-numeric={isNumeric(types[column]) || undefined}
                  >
                    {renderCell(row[column] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {copyText && (
        <span className="markdown-table-tools">
          <IconButton size="sm" label="Copy as CSV" onClick={() => void copy()}>
            <CopyGlyph copied={status === COPIED} idle={Table} />
          </IconButton>
        </span>
      )}
      {status && (
        <small role="status" className="visually-hidden">
          {status}
        </small>
      )}
    </div>
  );
}
