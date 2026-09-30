import {
  Bot,
  BookOpen,
  Brain,
  Calculator,
  Calendar,
  ChartColumn,
  Clapperboard,
  Clock,
  CloudSun,
  Cpu,
  FilePen,
  FileText,
  Folder,
  FolderCode,
  GitBranch,
  Globe,
  Image,
  Mail,
  MessagesSquare,
  Monitor,
  MousePointerClick,
  Palette,
  Plug,
  Search,
  Terminal,
  Wrench,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import type {
  TranscriptTraceGroup,
  TranscriptTraceItem,
} from '../../api/types';
import { humanizeToken } from '../../ui/format';

/** Public trace status, as the server reports it. */
export type StepStatus = TranscriptTraceItem['status'];

type Verb = {
  /** Past tense, shown once the step has finished. */
  done: string;
  /** Present participle, shown while the step runs ("Searching the web"). */
  running: string;
  icon: LucideIcon;
};

const verb = (done: string, running: string, icon: LucideIcon): Verb => ({
  done,
  running,
  icon,
});

// Exact tool names first; prefix and pattern rules below cover families.
const EXACT: Record<string, Verb> = {
  read_url: verb('Read page', 'Reading page', Globe),
  browser_navigate: verb('Opened page', 'Opening page', Globe),
  browser_click: verb('Clicked in the browser', 'Clicking', MousePointerClick),
  browser_type: verb('Typed in the browser', 'Typing', MousePointerClick),
  browser_scroll: verb('Scrolled the page', 'Scrolling', MousePointerClick),
  browser_snapshot: verb('Read the browser', 'Reading the browser', Globe),
  browser_back: verb('Went back', 'Going back', Globe),
  browser_tab: verb('Switched tab', 'Switching tab', Globe),
  computer_use: verb('Used the computer', 'Using the computer', Monitor),
  wikipedia: verb('Searched Wikipedia', 'Searching Wikipedia', BookOpen),
  arxiv: verb('Searched arXiv', 'Searching arXiv', BookOpen),
  youtube_search: verb('Searched YouTube', 'Searching YouTube', Clapperboard),
  youtube_transcript: verb(
    'Read video transcript',
    'Reading video transcript',
    Clapperboard,
  ),
  calculate: verb('Calculated', 'Calculating', Calculator),
  wolfram_alpha: verb(
    'Asked Wolfram Alpha',
    'Asking Wolfram Alpha',
    Calculator,
  ),
  get_current_datetime: verb('Checked the time', 'Checking the time', Clock),
  get_current_weather: verb(
    'Checked the weather',
    'Checking the weather',
    CloudSun,
  ),
  get_weather_forecast: verb(
    'Checked the forecast',
    'Checking the forecast',
    CloudSun,
  ),
  get_system_info: verb('Checked the system', 'Checking the system', Cpu),
  row_bot_status: verb(
    'Checked Row-Bot status',
    'Checking Row-Bot status',
    Cpu,
  ),
  row_bot_update_setting: verb('Changed a setting', 'Changing a setting', Cpu),
  generate_image: verb('Generated an image', 'Generating an image', Image),
  edit_image: verb('Edited an image', 'Editing an image', Image),
  analyze_image: verb('Looked at an image', 'Looking at an image', Image),
  animate_image: verb('Animated an image', 'Animating an image', Clapperboard),
  generate_video: verb('Generated a video', 'Generating a video', Clapperboard),
  create_chart: verb('Created a chart', 'Creating a chart', ChartColumn),
  create_design: verb('Created a design', 'Creating a design', Palette),
  create_code_folder: verb(
    'Created a code folder',
    'Creating a code folder',
    FolderCode,
  ),
  request_connection: verb('Asked to connect', 'Asking to connect', Plug),
  export_to_pdf: verb('Exported a PDF', 'Exporting a PDF', FileText),
  save_memory: verb('Saved a memory', 'Saving a memory', Brain),
  search_memory: verb('Searched memory', 'Searching memory', Brain),
  update_memory: verb('Updated a memory', 'Updating a memory', Brain),
  delete_memory: verb('Deleted a memory', 'Deleting a memory', Brain),
  link_memories: verb('Linked memories', 'Linking memories', Brain),
  list_memories: verb('Listed memories', 'Listing memories', Brain),
  explore_connections: verb(
    'Explored connections',
    'Exploring connections',
    Brain,
  ),
  search_conversations: verb(
    'Searched conversations',
    'Searching conversations',
    MessagesSquare,
  ),
  list_conversations: verb(
    'Listed conversations',
    'Listing conversations',
    MessagesSquare,
  ),
  send_gmail_message: verb('Sent an email', 'Sending an email', Mail),
  create_gmail_draft: verb('Drafted an email', 'Drafting an email', Mail),
  search_gmail: verb('Searched email', 'Searching email', Mail),
  delegate_work: verb('Delegated work', 'Delegating work', Bot),
  run_command: verb('Ran a command', 'Running a command', Terminal),
  shell: verb('Ran a command', 'Running a command', Terminal),
  read_terminal: verb('Read the terminal', 'Reading the terminal', Terminal),
  read_file: verb('Read a file', 'Reading a file', FileText),
  write_file: verb('Wrote a file', 'Writing a file', FilePen),
  list_directory: verb('Listed files', 'Listing files', Folder),
  file_search: verb('Searched files', 'Searching files', Search),
  copy_file: verb('Copied a file', 'Copying a file', Folder),
  move_file: verb('Moved a file', 'Moving a file', Folder),
  file_delete: verb('Deleted a file', 'Deleting a file', Folder),
  tool_search: verb('Found tools', 'Finding tools', Wrench),
  x_post: verb('Posted on X', 'Posting on X', Globe),
  x_read: verb('Read X', 'Reading X', Globe),
  x_engage: verb('Engaged on X', 'Engaging on X', Globe),
  goal_status: verb('Checked the goal', 'Checking the goal', Zap),
  goal_update: verb('Updated the goal', 'Updating the goal', Zap),
  developer_git_status: verb(
    'Checked git status',
    'Checking git status',
    GitBranch,
  ),
  developer_get_diff: verb('Read the diff', 'Reading the diff', GitBranch),
  developer_commit_changes: verb(
    'Committed changes',
    'Committing changes',
    GitBranch,
  ),
  developer_create_branch: verb(
    'Created a branch',
    'Creating a branch',
    GitBranch,
  ),
  developer_switch_branch: verb(
    'Switched branch',
    'Switching branch',
    GitBranch,
  ),
  developer_push_current_branch: verb(
    'Pushed the branch',
    'Pushing the branch',
    GitBranch,
  ),
  developer_read_file: verb('Read a file', 'Reading a file', FileText),
  workspace_read_file: verb('Read a file', 'Reading a file', FileText),
  developer_write_file: verb('Wrote a file', 'Writing a file', FilePen),
  developer_apply_patch: verb('Edited files', 'Editing files', FilePen),
  developer_list_files: verb('Listed files', 'Listing files', Folder),
  developer_search: verb('Searched the code', 'Searching the code', Search),
  developer_run_command: verb('Ran a command', 'Running a command', Terminal),
  developer_run_detected_test: verb(
    'Ran the tests',
    'Running the tests',
    Terminal,
  ),
};

const PATTERNS: Array<[RegExp, Verb]> = [
  [
    /(^|_)web_search$|^(duckduckgo|tavily|brave|serp|google)(_|$)/,
    verb('Searched the web', 'Searching the web', Globe),
  ],
  [/^browser_/, verb('Used the browser', 'Using the browser', Globe)],
  [
    /^calendar|_calendar/,
    verb('Checked the calendar', 'Checking the calendar', Calendar),
  ],
  [/gmail|email/, verb('Checked email', 'Checking email', Mail)],
  [/^task_/, verb('Updated workflows', 'Updating workflows', Zap)],
  [/^agent_/, verb('Coordinated an agent', 'Coordinating an agent', Bot)],
  [
    /^developer_/,
    verb('Worked in the code folder', 'Working in the code folder', GitBranch),
  ],
  [/^(wiki|tracker)_/, verb('Updated notes', 'Updating notes', BookOpen)],
  [/memor/, verb('Used memory', 'Using memory', Brain)],
  [/search$/, verb('Searched', 'Searching', Search)],
];

// Live steps can carry a display label such as "🖥️ shell"; match on the name.
function bareName(name: string) {
  return name.replace(/^[^\p{L}\p{N}_]+/u, '').trim();
}

function describe(name: string): Verb | null {
  const key = bareName(name).toLowerCase();
  if (Object.hasOwn(EXACT, key)) return EXACT[key];
  // Workspace file tools mirror the plain ones: workspace_write_file.
  const inner = key.replace(/^workspace_/, '');
  if (inner !== key && Object.hasOwn(EXACT, inner)) return EXACT[inner];
  for (const [pattern, value] of PATTERNS) if (pattern.test(key)) return value;
  return null;
}

/** "Searched the web", "Searching the web" or "Web search failed". */
export function stepVerb(name: string, status: StepStatus): string {
  const known = describe(name);
  const fallback = humanizeToken(bareName(name)) || 'Tool';
  if (status === 'pending')
    return known?.running ?? `Running ${midSentence(fallback)}`;
  // A step that failed or never ran must not read as done ("Deleted a file").
  const action = known && midSentence(imperative(known.running));
  if (status === 'failed')
    return action ? `Couldn't ${action}` : `${fallback} failed`;
  if (status === 'blocked' || status === 'cancelled')
    return action ? `Didn't ${action}` : `${fallback} skipped`;
  return known?.done ?? fallback;
}

// A denial's result leads with its approval line; older rows kept only the
// tool's refusal.
const DENIED =
  /^(approval: asked; denied|(action|command) cancelled by user|install cancelled)/i;

/** Why a cancelled step never ran or finished: you denied it, or stopped the turn. */
export function skipReason(
  step: TranscriptTraceItem,
): 'Denied' | 'Stopped' | null {
  if (step.status !== 'cancelled') return null;
  return DENIED.test(step.safe_summary) ? 'Denied' : 'Stopped';
}

/**
 * An approval-gated result leads with one line saying whether approval was
 * needed, given or refused ("Approval: asked; approved by you").
 */
export function splitApproval(text: string): {
  approval: string;
  body: string;
} {
  const line = /^Approval: ([^\n]*)\n?/.exec(text);
  if (!line) return { approval: '', body: text };
  return {
    approval: line[1].charAt(0).toUpperCase() + line[1].slice(1),
    body: text.slice(line[0].length),
  };
}

/** "Frobnicate widget" reads "frobnicate widget" mid-sentence; "MCP" stays. */
function midSentence(label: string) {
  return /^\p{Lu}\p{Ll}/u.test(label)
    ? label[0].toLowerCase() + label.slice(1)
    : label;
}

export function stepIcon(name: string): LucideIcon {
  return describe(name)?.icon ?? Wrench;
}

const KEY_ARGUMENTS = [
  'query',
  'q',
  'search_query',
  'url',
  'path',
  'file_path',
  'relative_path',
  'command',
  'cmd',
  'prompt',
  'title',
  'expression',
  'input',
  'location',
  'city',
  'subject',
  'to',
  'pattern',
  'task',
  'profile',
  'name',
  'text',
  'content',
  'message',
];

function compactUrl(value: string): string {
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return value;
    const path = url.pathname === '/' ? '' : url.pathname;
    return `${url.hostname.replace(/^www\./, '')}${path}`;
  } catch {
    return value;
  }
}

function clip(value: string, limit = 140): string {
  const flat = value.replace(/\s+/g, ' ').trim();
  return flat.length > limit ? `${flat.slice(0, limit - 1)}…` : flat;
}

/** The one argument worth showing inline, from the bounded public input. */
export function keyArgument(safeInput: string | undefined): string {
  const source = (safeInput ?? '').trim();
  if (!source) return '';
  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    return clip(source);
  }
  if (typeof parsed === 'string') return clip(compactUrl(parsed));
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return '';
  const record = parsed as Record<string, unknown>;
  for (const key of KEY_ARGUMENTS) {
    const value = record[key];
    if (typeof value === 'string' && value.trim())
      return clip(
        key === 'url'
          ? compactUrl(value)
          : key === 'query' || key === 'q'
            ? `“${value.trim()}”`
            : value,
      );
    if (typeof value === 'number') return String(value);
  }
  const strings = Object.values(record).filter(
    (value): value is string =>
      typeof value === 'string' && Boolean(value.trim()),
  );
  return strings.length === 1 ? clip(compactUrl(strings[0])) : '';
}

export type ActivitySummary = {
  total: number;
  /** Steps that failed or whose outcome is uncertain. */
  failed: number;
  /** Steps that never ran: denied, blocked or cancelled. */
  skipped: number;
  pending: number;
  /** Distinct tool glyphs in call order, at most four. */
  icons: LucideIcon[];
  /** The step that is running now, if any. */
  current: TranscriptTraceItem | null;
};

export function orderedSteps(groups: TranscriptTraceGroup[]) {
  return [...groups]
    .sort((first, second) => first.group_order - second.group_order)
    .flatMap((group) =>
      [...group.items].sort(
        (first, second) => first.call_order - second.call_order,
      ),
    );
}

const ATTENTION = new Set<StepStatus>([
  'failed',
  'blocked',
  'cancelled',
  'uncertain',
]);

export function isAttention(status: StepStatus) {
  return ATTENTION.has(status);
}

export function summarizeActivity(
  groups: TranscriptTraceGroup[],
): ActivitySummary {
  const steps = orderedSteps(groups);
  const icons: LucideIcon[] = [];
  for (const step of steps) {
    const icon = stepIcon(step.canonical_name);
    if (!icons.includes(icon) && icons.length < 4) icons.push(icon);
  }
  return {
    total: steps.length,
    failed: steps.filter(
      (step) => step.status === 'failed' || step.status === 'uncertain',
    ).length,
    skipped: steps.filter(
      (step) => step.status === 'blocked' || step.status === 'cancelled',
    ).length,
    pending: steps.filter((step) => step.status === 'pending').length,
    icons,
    current:
      [...steps].reverse().find((step) => step.status === 'pending') ?? null,
  };
}

/** "Used 3 tools", "Used 2 tools · 1 failed · 1 skipped", "1 tool skipped". */
export function activityLabel(summary: ActivitySummary): string {
  const noun = summary.total === 1 ? 'tool' : 'tools';
  // Nothing ran: every step was denied, blocked or cancelled.
  if (summary.total && summary.skipped === summary.total)
    return `${summary.total} ${noun} skipped`;
  return [
    `Used ${summary.total} ${noun}`,
    summary.failed ? `${summary.failed} failed` : '',
    summary.skipped ? `${summary.skipped} skipped` : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

/** Compact elapsed time: "0.8s", "8.4s", "1m 12s". */
export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '';
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

// Stems whose present participle dropped a silent "e" ("Saving" → "Save").
const SILENT_E = new Set([
  'analyz',
  'animat',
  'calculat',
  'coordinat',
  'creat',
  'delegat',
  'delet',
  'engag',
  'explor',
  'generat',
  'mov',
  'sav',
  'typ',
  'updat',
  'us',
  'writ',
]);

/** "Sending an email" → "Send an email", for approval questions. */
export function imperative(participle: string): string {
  const [first, ...rest] = participle.split(' ');
  if (!/ing$/i.test(first)) return participle;
  let stem = first.slice(0, -3);
  const lower = stem.toLowerCase();
  if (SILENT_E.has(lower)) stem += 'e';
  else if (/([bdgmnprt])\1$/i.test(stem)) stem = stem.slice(0, -1);
  return [stem, ...rest].join(' ');
}

/** "Send an email?" for a canonical tool name awaiting approval. */
export function approvalQuestion(name: string): string {
  if (!describe(name))
    return `Allow ${humanizeToken(bareName(name)) || 'this action'}?`;
  return `${imperative(stepVerb(name, 'pending'))}?`;
}

/** The action itself, for a label: "Delete a file", or "Fixture action". */
export function approvalAction(name: string): string {
  if (!describe(name)) return humanizeToken(bareName(name)) || 'This action';
  return imperative(stepVerb(name, 'pending'));
}

// One `key=value` argument as the agent writes it (Python repr).
const ARGUMENT =
  /(\w+)=('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[^,'"]*)(?:, (?=\w+=)|$)/y;

function unquote(value: string): string {
  const quoted = /^(['"])([\s\S]*)\1$/.exec(value);
  if (!quoted) return value;
  return quoted[2].replace(/\\(.)/g, (_match, character: string) =>
    character === 'n' ? ' ' : character,
  );
}

/**
 * "Delete file: file_path='notes.txt'" reads "Delete file: notes.txt".
 * Anything that is not that shape is shown as it is.
 */
export function plainApprovalReason(reason: string): string {
  const match = /^([^:\n]{1,80}): ([\s\S]+)$/.exec(reason.trim());
  if (!match) return reason;
  const pairs: [string, string][] = [];
  ARGUMENT.lastIndex = 0;
  while (ARGUMENT.lastIndex < match[2].length) {
    const start = ARGUMENT.lastIndex;
    const pair = ARGUMENT.exec(match[2]);
    if (!pair || ARGUMENT.lastIndex === start) return reason;
    pairs.push([pair[1], unquote(pair[2].trim())]);
  }
  if (!pairs.length) return reason;
  const values =
    pairs.length === 1
      ? pairs[0][1]
      : pairs
          .map(([key, value]) => `${midSentence(humanizeToken(key))} ${value}`)
          .join(' · ');
  return `${match[1]}: ${values}`;
}
