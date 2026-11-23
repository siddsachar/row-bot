import { useState } from 'react';
import { Download, ScrollText, ShieldCheck } from 'lucide-react';
import {
  Button,
  EmptyState,
  EntityList,
  EntityRow,
  Field,
  Hint,
  IconButton,
  InlineEmpty,
  Input,
  Select,
} from '../../ui/primitives';
import { When } from '../../ui/When';
import { absoluteTime, humanizeToken } from '../../ui/format';
import { useNotify } from '../../ui/overlays';
import { query, request } from '../api';
import { useDirectory } from '../directory';
import { Loaded, useLoad } from '../load';
import { Notice, Page } from '../page';
import type { AuditLink, ChainReport } from '../types';
import {
  ACTION_LABELS,
  count,
  describeEvent,
  OUTCOMES,
  problem,
} from '../words';

const PAGE = 50;
type Filters = { action: string; actor: string; from: string; to: string };
const NO_FILTERS: Filters = { action: '', actor: '', from: '', to: '' };

/** A local day's start as the API's time ("2026-11-23" → its UTC instant). */
function dayStart(value: string, after = 0) {
  if (!value) return undefined;
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year, month - 1, day + after).toISOString();
}

function filterQuery(filters: Filters) {
  return {
    action: filters.action || undefined,
    actor: filters.actor || undefined,
    from: dayStart(filters.from),
    to: dayStart(filters.to, 1),
  };
}

const BREAKS: Record<string, string> = {
  invalid: "an event there can't be read",
  prev_hash: 'events were removed, added or reordered there',
  seq: 'an event is out of its place',
  hash: 'an event there was changed',
  head: 'events were cut off the end',
};

function Integrity({ report }: { report: ChainReport }) {
  if (report.ok)
    return (
      <Notice title="The audit log is intact">
        All {count(report.checked)} events check out. To notice events cut off
        the end later, keep this fingerprint somewhere else:{' '}
        <code>{report.head.slice(0, 16)}</code>
      </Notice>
    );
  return (
    <Notice
      tone="warning"
      title={`The audit log is broken at event ${report.broken_at}`}
    >
      {report.reason ? `${BREAKS[report.reason]}. ` : ''}
      The {count(report.checked)} events before it check out. Export the log and
      compare it with a copy you kept.
    </Notice>
  );
}

export default function AuditPage() {
  const { people, nameOf } = useDirectory();
  const notify = useNotify();
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [checking, setChecking] = useState(false);
  const [report, setReport] = useState<ChainReport | null>(null);
  const [more, setMore] = useState(false);
  const search = query({ ...filterQuery(filters), limit: PAGE });
  const events = useLoad(
    () =>
      request<{ events: AuditLink[]; next_before: number | null }>(
        `/api/admin/audit${search}`,
      ),
    search,
  );
  const filtered = Object.values(filters).some(Boolean);
  const set = (change: Partial<Filters>) =>
    setFilters((current) => ({ ...current, ...change }));

  async function verify() {
    setChecking(true);
    try {
      setReport(await request<ChainReport>('/api/admin/audit/verify'));
    } catch (error) {
      notify(problem(error), 'danger');
    } finally {
      setChecking(false);
    }
  }

  async function older(before: number) {
    setMore(true);
    try {
      const page = await request<{
        events: AuditLink[];
        next_before: number | null;
      }>(
        `/api/admin/audit${query({ ...filterQuery(filters), limit: PAGE, before })}`,
      );
      events.update((data) => ({
        events: [...data.events, ...page.events],
        next_before: page.next_before,
      }));
    } catch (error) {
      notify(problem(error), 'danger');
    } finally {
      setMore(false);
    }
  }

  return (
    <Page
      title="Audit"
      description="What happened under company rules: sign-ins, admin changes, model calls (counts only, never content), devices and exports."
      action={
        <>
          <IconButton
            label="Check integrity"
            disabled={checking}
            onClick={() => void verify()}
          >
            <ShieldCheck size={18} aria-hidden />
          </IconButton>
          <Hint label="Export as JSON Lines">
            <a
              className="button ghost icon-button icon-action icon-action-md"
              aria-label="Export as JSON Lines"
              href={`/api/admin/audit/export${query(filterQuery(filters))}`}
              download="audit.jsonl"
            >
              <Download size={18} aria-hidden />
            </a>
          </Hint>
        </>
      }
    >
      {report && <Integrity report={report} />}
      <div className="console-filters">
        <Field label="What">
          <Select
            value={filters.action}
            onChange={(event) => set({ action: event.target.value })}
          >
            <option value="">Everything</option>
            {Object.entries(ACTION_LABELS).map(([action, label]) => (
              <option key={action} value={action}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Who">
          <Select
            value={filters.actor}
            onChange={(event) => set({ actor: event.target.value })}
          >
            <option value="">Anyone</option>
            {people.map((person) => (
              <option key={person.id} value={person.id}>
                {person.display_name || person.email}
              </option>
            ))}
            <option value="cli">The server's operator</option>
          </Select>
        </Field>
        <Field label="From">
          <Input
            type="date"
            value={filters.from}
            onChange={(event) => set({ from: event.target.value })}
          />
        </Field>
        <Field label="To">
          <Input
            type="date"
            value={filters.to}
            onChange={(event) => set({ to: event.target.value })}
          />
        </Field>
        {filtered && (
          <Button variant="ghost" onClick={() => setFilters(NO_FILTERS)}>
            Clear filters
          </Button>
        )}
      </div>
      <Loaded load={events} label="Loading the audit log" what="the audit log">
        {({ events: links, next_before }) =>
          links.length === 0 ? (
            filtered ? (
              <InlineEmpty icon={<ScrollText size={16} />}>
                No events match these filters.
              </InlineEmpty>
            ) : (
              <EmptyState title="Nothing recorded yet">
                Events show here from the first sign-in on.
              </EmptyState>
            )
          ) : (
            <>
              <EntityList label="Audit events">
                {links.map(({ seq, event }) => (
                  <EntityRow
                    key={seq}
                    title={describeEvent(event, nameOf)}
                    status={
                      OUTCOMES[event.outcome]
                        ? { tone: 'warning', label: OUTCOMES[event.outcome] }
                        : undefined
                    }
                    meta={<When value={event.occurred_at} />}
                    details={
                      <dl className="console-details">
                        <div>
                          <dt>When</dt>
                          <dd>{absoluteTime(event.occurred_at)}</dd>
                        </div>
                        <div>
                          <dt>Event number</dt>
                          <dd>{seq}</dd>
                        </div>
                        {Object.entries(event.details).map(([key, value]) => (
                          <div key={key}>
                            <dt>{humanizeToken(key)}</dt>
                            <dd>{String(value)}</dd>
                          </div>
                        ))}
                      </dl>
                    }
                  />
                ))}
              </EntityList>
              {next_before !== null && (
                <Button
                  className="console-more"
                  disabled={more}
                  onClick={() => void older(next_before)}
                >
                  {more ? 'Loading…' : 'Show older events'}
                </Button>
              )}
            </>
          )
        }
      </Loaded>
    </Page>
  );
}
