import { useState } from 'react';
import { EmptyState, Field, Input, Segmented } from '../../../ui/primitives';
import { query, request } from '../../api';
import { useDirectory } from '../../directory';
import { Loaded, useLoad } from '../../load';
import type { UsageBy, UsageRow } from '../../types';
import { amount, count } from '../../words';

const pad = (value: number) => String(value).padStart(2, '0');
const day = (date: Date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

/** This month so far: the first of the month to today (both included). */
function thisMonth(now = new Date()) {
  return {
    start: day(new Date(now.getFullYear(), now.getMonth(), 1)),
    end: day(now),
  };
}

/** The day after `value` (the API's end is exclusive). */
function dayAfter(value: string) {
  const [year, month, date] = value.split('-').map(Number);
  return day(new Date(year, month - 1, date + 1));
}

const HEADINGS: Record<UsageBy, string> = {
  person: 'Person',
  group: 'Group',
  model: 'Model',
};

export function Usage() {
  const { nameOf } = useDirectory();
  const [by, setBy] = useState<UsageBy>('person');
  const [range, setRange] = useState(thisMonth);
  const valid = Boolean(range.start && range.end && range.end >= range.start);
  const filters = query({
    by,
    start: range.start,
    end: valid ? dayAfter(range.end) : undefined,
  });
  const usage = useLoad(
    () =>
      request<{ usage: UsageRow[] }>(`/api/admin/usage${filters}`).then(
        (answer) => answer.usage,
      ),
    filters,
  );
  const label = (row: UsageRow) => {
    const key = row[by] ?? '';
    return by === 'person' ? nameOf(key) : key;
  };

  return (
    <section className="console-section" aria-label="Usage">
      <div className="console-filters">
        <Segmented<UsageBy>
          label="Group usage by"
          value={by}
          onChange={setBy}
          options={[
            { value: 'person', label: 'By person' },
            { value: 'group', label: 'By group' },
            { value: 'model', label: 'By model' },
          ]}
        />
        <Field label="From">
          <Input
            type="date"
            value={range.start}
            onChange={(event) =>
              setRange((current) => ({ ...current, start: event.target.value }))
            }
          />
        </Field>
        <Field label="To">
          <Input
            type="date"
            value={range.end}
            onChange={(event) =>
              setRange((current) => ({ ...current, end: event.target.value }))
            }
          />
        </Field>
      </div>
      {!valid ? (
        <p className="console-form-error" role="alert">
          The end date must be on or after the start date.
        </p>
      ) : (
        <Loaded load={usage} label="Loading usage" what="usage">
          {(rows) =>
            rows.length === 0 ? (
              <EmptyState title="No model calls in this period">
                Usage shows here once people call company models through the
                server.
              </EmptyState>
            ) : (
              <div className="console-table-wrap">
                <table className="console-table">
                  <caption className="visually-hidden">
                    Model usage by {HEADINGS[by].toLowerCase()}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">{HEADINGS[by]}</th>
                      <th scope="col">Calls</th>
                      <th scope="col">Input tokens</th>
                      <th scope="col">Output tokens</th>
                      <th scope="col">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row[by] ?? ''}>
                        <th scope="row">{label(row)}</th>
                        <td>{count(row.calls)}</td>
                        <td>{count(row.input_tokens)}</td>
                        <td>{count(row.output_tokens)}</td>
                        <td>{amount(row.cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </Loaded>
      )}
      <p className="console-footnote">
        Costs use the prices in the catalog, in your company's currency.
      </p>
    </section>
  );
}
