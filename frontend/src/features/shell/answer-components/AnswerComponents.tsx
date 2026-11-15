import { Fragment, useState, type ReactNode } from 'react';
import { Check, CornerDownLeft } from 'lucide-react';
import type { TranscriptRow } from '../../../api/types';
import { useNotify } from '../../../ui/overlays';
import {
  Button,
  Hint,
  Stat,
  StatGroup,
  type Tone,
} from '../../../ui/primitives';
import { sendPrompt, usePromptReady } from '../composer-bridge';
import { SortableTable } from '../SortableTable';

type Block = TranscriptRow['blocks'][number];
type Of<T extends Block['type']> = Extract<Block, { type: T }>;
type Action = { label: string; reply: string };

const CURRENCY = new Set(['£', '$', '€', '¥', '₹', '₩', '₽', '₺', '₪']);
const TONES: Record<string, Tone> = {
  good: 'success',
  bad: 'danger',
  neutral: 'neutral',
};

export function formatNumber(value: number) {
  return value.toLocaleString('en-GB', { maximumFractionDigits: 4 });
}

/** A value with its unit where people write it: "£1,200", "62%", "5 kg". */
export function withUnit(value: number | string, unit: string) {
  const shown = typeof value === 'number' ? formatNumber(value) : value;
  if (!unit) return shown;
  if (CURRENCY.has(unit))
    return shown.startsWith('-')
      ? `-${unit}${shown.slice(1)}`
      : `${unit}${shown}`;
  return unit === '%' ? `${shown}%` : `${shown} ${unit}`;
}

/**
 * Where a reply button's words go: the open conversation's own composer,
 * marked as a tap on this reply (``messageId``).
 */
export type AnswerTarget = { conversationId?: string; messageId?: string };

/**
 * Reply buttons: a tap sends the button's words as the person's message
 * through the composer's normal send path (same checks, no attachments).
 * Nothing runs by itself and no tool is called. Like the composer's own
 * Send, they wait while Row-Bot is still answering. Once one is sent, the
 * block's buttons show which was picked and stay disabled.
 */
function Replies({
  actions,
  target,
  label,
  variant,
}: {
  actions: Action[];
  target: AnswerTarget;
  label: string;
  variant: 'choices' | 'actions';
}) {
  const notify = useNotify();
  const [picked, setPicked] = useState<string | null>(null);
  const ready = usePromptReady(target.conversationId);
  if (!actions.length) return null;
  function send(action: Action) {
    if (picked !== null) return;
    if (
      !target.conversationId ||
      !sendPrompt(
        target.conversationId,
        action.reply,
        target.messageId
          ? { message_id: target.messageId, label: action.label }
          : undefined,
      )
    ) {
      notify(
        'Row-Bot can’t take that reply right now. Try again when it’s ready.',
        'warning',
      );
      return;
    }
    setPicked(action.label);
  }
  return (
    <div
      className="answer-replies"
      data-variant={variant}
      role="group"
      aria-label={label}
      data-sent={picked !== null ? 'true' : undefined}
    >
      {actions.map((action, index) => {
        const chosen = picked === action.label;
        const button = (
          <Button
            key={`${index}:${action.label}`}
            className="answer-reply"
            variant="secondary"
            disabled={picked !== null || !ready}
            aria-pressed={picked !== null ? chosen : undefined}
            data-picked={chosen ? 'true' : undefined}
            onClick={() => send(action)}
          >
            {chosen ? (
              <Check size={14} aria-hidden />
            ) : (
              variant === 'actions' && <CornerDownLeft size={14} aria-hidden />
            )}
            <span>{action.label}</span>
            {chosen && <span className="answer-reply-sent">Sent</span>}
          </Button>
        );
        // The words a tap sends, when they say more than the button.
        return action.reply === action.label ? (
          <Fragment key={`${index}:${action.label}`}>{button}</Fragment>
        ) : (
          <Hint
            key={`${index}:${action.label}`}
            label={`Sends “${action.reply}”`}
          >
            {button}
          </Hint>
        );
      })}
      {!ready && picked === null && (
        <span className="answer-replies-wait">
          Choices open when Row-Bot is ready for your next message.
        </span>
      )}
    </div>
  );
}

function Frame({
  kind,
  title,
  count,
  children,
  actions,
  target,
}: {
  kind: string;
  title: string;
  count?: string;
  children: ReactNode;
  actions?: Action[];
  target: AnswerTarget;
}) {
  return (
    <section
      className="answer-component"
      data-kind={kind}
      aria-label={title || undefined}
    >
      {(title || count) && (
        <header className="answer-header">
          {title && <h4 className="answer-title">{title}</h4>}
          {count && <span className="answer-count">{count}</span>}
        </header>
      )}
      {children}
      {actions && (
        <Replies
          actions={actions}
          target={target}
          label={title ? `Replies for ${title}` : 'Replies'}
          variant="actions"
        />
      )}
    </section>
  );
}

export function AnswerTable({
  block,
  target,
  copyText,
}: {
  block: Of<'table'>;
  target: AnswerTarget;
  copyText?: (value: string) => Promise<boolean>;
}) {
  return (
    <Frame
      kind="table"
      title={block.title}
      actions={block.actions}
      target={target}
    >
      <SortableTable
        headers={block.columns.map((column) => column.label)}
        rows={block.rows}
        copyText={copyText}
      />
      {block.note && <p className="answer-note">{block.note}</p>}
    </Frame>
  );
}

export function AnswerChecklist({
  block,
  target,
}: {
  block: Of<'checklist'>;
  target: AnswerTarget;
}) {
  const [done, setDone] = useState(
    () =>
      new Set(block.items.filter((item) => item.done).map((item) => item.id)),
  );
  const toggle = (id: string, checked: boolean) =>
    setDone((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  return (
    <Frame
      kind="checklist"
      title={block.title}
      count={`${done.size} of ${block.items.length} done`}
      actions={block.actions}
      target={target}
    >
      <ul className="answer-checklist">
        {block.items.map((item) => (
          <li key={item.id} data-done={done.has(item.id) ? 'true' : undefined}>
            <label>
              <input
                type="checkbox"
                checked={done.has(item.id)}
                onChange={(event) =>
                  toggle(item.id, event.currentTarget.checked)
                }
              />
              <span>{item.text}</span>
            </label>
          </li>
        ))}
      </ul>
    </Frame>
  );
}

export function AnswerFigures({
  block,
  target,
}: {
  block: Of<'figures'>;
  target: AnswerTarget;
}) {
  return (
    <Frame
      kind="figures"
      title={block.title}
      actions={block.actions}
      target={target}
    >
      <StatGroup label={block.title || 'Figures'} className="answer-figures">
        {block.items.map((item, index) => {
          const prefixed =
            CURRENCY.has(item.unit) || item.unit === '%' || !item.unit;
          return (
            <Stat
              key={index}
              label={item.label}
              value={
                prefixed
                  ? withUnit(item.value, item.unit)
                  : withUnit(item.value, '')
              }
              unit={prefixed ? undefined : item.unit}
              delta={item.delta || undefined}
              tone={TONES[item.tone]}
            />
          );
        })}
      </StatGroup>
    </Frame>
  );
}

export function AnswerProgress({
  block,
  target,
}: {
  block: Of<'progress'>;
  target: AnswerTarget;
}) {
  return (
    <Frame
      kind="progress"
      title={block.title}
      actions={block.actions}
      target={target}
    >
      <ul className="answer-progress">
        {block.items.map((item, index) => {
          const share = Math.round((100 * item.value) / item.target);
          const over = item.value - item.target;
          const words =
            `${withUnit(item.value, item.unit)} of ${withUnit(item.target, item.unit)}` +
            ` (${share}%)`;
          return (
            <li key={index} data-over={over > 0 ? 'true' : undefined}>
              <div className="answer-progress-line">
                <span className="answer-progress-label">{item.label}</span>
                <span className="answer-progress-value">
                  {over > 0
                    ? `${withUnit(item.value, item.unit)} · +${withUnit(over, item.unit)} over`
                    : words}
                </span>
              </div>
              <div
                className="answer-progress-bar"
                role="progressbar"
                aria-label={item.label}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.min(100, share)}
                aria-valuetext={words}
              >
                <span style={{ width: `${Math.min(100, share)}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </Frame>
  );
}

export function AnswerChoices({
  block,
  target,
}: {
  block: Of<'choices'>;
  target: AnswerTarget;
}) {
  return (
    <section className="answer-component" data-kind="choices">
      {block.prompt && <p className="answer-prompt">{block.prompt}</p>}
      <Replies
        actions={block.options}
        target={target}
        label={block.prompt || 'Choices'}
        variant="choices"
      />
    </section>
  );
}

/** The chart's reply buttons, under the existing chart card. */
export function ChartReplies({
  actions,
  target,
}: {
  actions: Action[];
  target: AnswerTarget;
}) {
  return (
    <Replies
      actions={actions}
      target={target}
      label="Replies for the chart"
      variant="actions"
    />
  );
}
