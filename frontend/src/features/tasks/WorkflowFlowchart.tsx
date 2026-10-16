import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import {
  Bell,
  Bot,
  CircleAlert,
  Hourglass,
  MessageSquareText,
  Split,
  UserCheck,
  Workflow,
  type LucideIcon,
} from 'lucide-react';
import type { TaskGraphStepEdit } from '../../api/types';
import {
  arrowPath,
  edgePath,
  flowLinks,
  layoutFlowchart,
  type FlowBranch,
  type FlowTarget,
  type StepIssue,
} from './workflow-flowchart';

const ICONS: Record<string, LucideIcon> = {
  prompt: MessageSquareText,
  condition: Split,
  approval: UserCheck,
  notify: Bell,
  subtask: Workflow,
  delegate_agent: Bot,
  wait_for_agents: Hourglass,
};

/** What a card says when its main text is still empty. */
const EMPTY: Record<string, string> = {
  prompt: 'No prompt yet',
  condition: 'No condition yet',
  approval: 'No message yet',
  notify: 'No message yet',
  subtask: 'No workflow chosen',
  delegate_agent: 'No objective yet',
  wait_for_agents: 'Waits for agents from earlier steps',
};

const BRANCH_WORDS: Record<FlowBranch, string> = {
  yes: 'When true',
  no: 'When false',
  approved: 'When approved',
  denied: 'When denied',
  next: 'Next',
};

export interface WorkflowFlowchartProps {
  steps: readonly TaskGraphStepEdit[];
  selected: string;
  /** `pointer` when a card was clicked rather than chosen from the keyboard. */
  onSelect: (id: string, how: 'pointer' | 'keyboard') => void;
  disabled?: boolean;
  issues: ReadonlyMap<number, StepIssue>;
  kindLabel: (type: string) => string;
  summary: (step: TaskGraphStepEdit) => string;
}

/**
 * The workflow's steps as a flowchart: cards in step order (each a button
 * that opens the step's form), edges drawn behind them. Arrow keys follow the
 * edges; the list view says the same in rows.
 */
export default function WorkflowFlowchart({
  steps,
  selected,
  onSelect,
  disabled = false,
  issues,
  kindLabel,
  summary,
}: WorkflowFlowchartProps) {
  const id = useId();
  const layout = useMemo(
    () =>
      layoutFlowchart(steps, {
        nodeHeight: (index) => (issues.has(index) ? 80 : 60),
      }),
    [steps, issues],
  );
  const links = useMemo(() => flowLinks(steps), [steps]);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const frame = useRef<HTMLDivElement>(null);
  const selectedIndex = steps.findIndex((step) => step.id === selected);

  // A chart wider than its frame (a phone) opens on Start, not its left edge.
  const centred = useRef(false);
  useLayoutEffect(() => {
    const element = frame.current;
    const start = layout.terminals.find((item) => item.kind === 'start');
    if (centred.current || !element || !start) return;
    centred.current = true;
    if (element.scrollWidth > element.clientWidth)
      element.scrollLeft = start.x + start.width / 2 - element.clientWidth / 2;
  }, [layout]);

  // Keep the chosen card in view when it changes (a new step, the list).
  const shown = useRef(selected);
  useEffect(() => {
    if (shown.current === selected) return;
    shown.current = selected;
    buttons.current[selectedIndex]?.scrollIntoView?.({
      block: 'nearest',
      inline: 'nearest',
    });
  }, [selected, selectedIndex]);

  const moves = useMemo(() => {
    const down = new Map<number, number>();
    const up = new Map<number, number>();
    for (const edge of layout.edges) {
      if (typeof edge.from !== 'number' || typeof edge.to !== 'number')
        continue;
      if (!down.has(edge.from)) down.set(edge.from, edge.to);
      if (!up.has(edge.to) && !edge.loop) up.set(edge.to, edge.from);
    }
    // Left and right stay in a row: a layer, or a row of "Not reached".
    const rows = new Map<string, number[]>();
    for (const box of layout.steps) {
      const key = box.layer === null ? `row-${box.y}` : `layer-${box.layer}`;
      rows.set(key, [...(rows.get(key) ?? []), box.index]);
    }
    const left = new Map<number, number>();
    const right = new Map<number, number>();
    for (const row of rows.values()) {
      row.sort((a, b) => layout.steps[a].x - layout.steps[b].x);
      row.forEach((index, k) => {
        if (k > 0) left.set(index, row[k - 1]);
        if (k + 1 < row.length) right.set(index, row[k + 1]);
      });
    }
    return { ArrowDown: down, ArrowUp: up, ArrowLeft: left, ArrowRight: right };
  }, [layout]);

  function keyDown(event: ReactKeyboardEvent, index: number) {
    if (!(event.key in moves)) return;
    const target = moves[event.key as keyof typeof moves].get(index);
    event.preventDefault();
    if (target !== undefined) buttons.current[target]?.focus();
  }

  const target = (to: FlowTarget) =>
    to === 'end'
      ? 'the end of the workflow'
      : `step ${to + 1} · ${kindLabel(steps[to].type)}`;
  function describe(index: number) {
    const parts = [summary(steps[index]) || EMPTY[steps[index].type] || ''];
    for (const link of links.filter((item) => item.from === index))
      parts.push(
        link.branch
          ? `${BRANCH_WORDS[link.branch]}, goes to ${target(link.to)}`
          : `Then goes to ${target(link.to)}`,
      );
    if (layout.steps[index].layer === null)
      parts.push('Not reached: no step leads here, so it never runs');
    return parts
      .filter(Boolean)
      .map((part) => (/[.?!…]$/.test(part) ? part : `${part}.`))
      .join(' ');
  }

  const active = (edge: (typeof layout.edges)[number]) =>
    selectedIndex >= 0 &&
    (edge.from === selectedIndex || edge.to === selectedIndex);

  return (
    <div className="workflow-chart" ref={frame}>
      <div
        className="workflow-chart-canvas"
        role="group"
        aria-label="Workflow flowchart"
        style={{ width: layout.width, height: layout.height }}
      >
        <svg
          className="workflow-chart-edges"
          width={layout.width}
          height={layout.height}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          aria-hidden="true"
          focusable="false"
        >
          {layout.edges.map((edge) => (
            <g
              key={edge.id}
              className="workflow-chart-edge"
              data-active={active(edge) ? 'true' : undefined}
              data-loop={edge.loop ? 'true' : undefined}
            >
              <path className="workflow-chart-line" d={edgePath(edge)} />
              <path className="workflow-chart-arrow" d={arrowPath(edge)} />
            </g>
          ))}
        </svg>
        {layout.edges.map(
          (edge) =>
            edge.label &&
            edge.labelAt && (
              <span
                key={`label-${edge.id}`}
                className="workflow-chart-label"
                data-active={active(edge) ? 'true' : undefined}
                style={{ left: edge.labelAt.x, top: edge.labelAt.y }}
                aria-hidden="true"
              >
                {edge.label}
              </span>
            ),
        )}
        {layout.terminals.map((terminal) => (
          <span
            key={terminal.id}
            className="workflow-chart-terminal"
            data-kind={terminal.kind}
            style={{
              left: terminal.x,
              top: terminal.y,
              width: terminal.width,
              height: terminal.height,
            }}
            aria-hidden="true"
          >
            {terminal.kind === 'start' ? 'Start' : 'End'}
          </span>
        ))}
        {layout.unreached && (
          <p
            className="workflow-chart-group"
            style={{ left: layout.unreached.x, top: layout.unreached.y }}
          >
            <strong>Not reached</strong>
            <span>No step leads to these, so they never run.</span>
          </p>
        )}
        {steps.map((step, index) => {
          const box = layout.steps[index];
          const Icon = ICONS[step.type] ?? Workflow;
          const text = summary(step);
          const issue = issues.get(index);
          return (
            <button
              key={step.id}
              ref={(element) => {
                buttons.current[index] = element;
              }}
              type="button"
              className="workflow-chart-node"
              data-type={step.type}
              data-issue={issue ? 'true' : undefined}
              data-unreached={box.layer === null ? 'true' : undefined}
              aria-pressed={step.id === selected}
              aria-label={`Step ${index + 1} · ${kindLabel(step.type)}${step.id.startsWith('draft_') ? ' · new step' : ''}`}
              aria-describedby={`${id}-${index}`}
              disabled={disabled}
              style={{
                left: box.x,
                top: box.y,
                width: box.width,
                height: box.height,
              }}
              onClick={(event: ReactMouseEvent) =>
                onSelect(step.id, event.detail > 0 ? 'pointer' : 'keyboard')
              }
              onKeyDown={(event) => keyDown(event, index)}
            >
              <span className="workflow-chart-icon" aria-hidden="true">
                <Icon size={15} />
              </span>
              <span className="workflow-chart-text" aria-hidden="true">
                <span className="workflow-chart-title">
                  <span className="workflow-chart-number">{index + 1}</span>
                  <span className="workflow-chart-kind">
                    {kindLabel(step.type)}
                  </span>
                  {step.id.startsWith('draft_') && (
                    <span className="workflow-chart-new">New</span>
                  )}
                </span>
                <span
                  className="workflow-chart-summary"
                  data-empty={text ? undefined : 'true'}
                >
                  {text || EMPTY[step.type] || kindLabel(step.type)}
                </span>
                {issue && (
                  <span className="workflow-chart-issue">
                    <CircleAlert size={13} aria-hidden="true" />
                    <span>{issue.short}</span>
                  </span>
                )}
              </span>
              <span id={`${id}-${index}`} className="visually-hidden">
                {describe(index)}
                {issue ? ` Problem: ${issue.message}` : ''}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
