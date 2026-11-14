import {
  Bot,
  BookOpen,
  Brain,
  Calculator,
  Calendar,
  Camera,
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
  GitPullRequest,
  Globe,
  Image,
  LayoutTemplate,
  Mail,
  MessageCircleQuestion,
  MessagesSquare,
  Monitor,
  MousePointerClick,
  Palette,
  Plug,
  Scissors,
  Search,
  Send,
  Smartphone,
  Terminal,
  Video,
  Wrench,
  Zap,
  type LucideIcon,
  PackageOpen,
  Upload,
  Download,
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
  // Each says what it does, so an approval does too ("Delete tracker entries?", not "Update notes?").
  tracker_log: verb('Logged to a tracker', 'Logging to a tracker', ChartColumn),
  tracker_query: verb('Read a tracker', 'Reading a tracker', ChartColumn),
  tracker_delete: verb(
    'Deleted tracker entries',
    'Deleting tracker entries',
    ChartColumn,
  ),
  task_create: verb('Created a workflow', 'Creating a workflow', Zap),
  task_update: verb('Changed a workflow', 'Changing a workflow', Zap),
  task_delete: verb('Deleted a workflow', 'Deleting a workflow', Zap),
  task_run_now: verb('Ran a workflow', 'Running a workflow', Zap),
  task_list: verb('Listed workflows', 'Listing workflows', Zap),
  browser_navigate: verb('Opened page', 'Opening page', Globe),
  browser_click: verb('Clicked in the browser', 'Clicking', MousePointerClick),
  browser_type: verb('Typed in the browser', 'Typing', MousePointerClick),
  browser_select: verb(
    'Chose an option',
    'Choosing an option',
    MousePointerClick,
  ),
  browser_press: verb('Pressed a key', 'Pressing a key', MousePointerClick),
  browser_scroll: verb('Scrolled the page', 'Scrolling', MousePointerClick),
  browser_wait_for: verb('Read the page', 'Reading the page', Globe),
  browser_extract: verb('Read the page', 'Reading the page', Globe),
  browser_tabs: verb('Used the tabs', 'Using the tabs', Globe),
  browser_upload: verb('Uploaded a file', 'Uploading a file', Upload),
  browser_download: verb('Downloaded a file', 'Downloading a file', Download),
  browser_screenshot: verb('Took a screenshot', 'Taking a screenshot', Camera),
  browser_viewport: verb(
    'Changed the page size',
    'Changing the page size',
    Smartphone,
  ),
  browser_record: verb('Recorded the browser', 'Recording the browser', Video),
  computer: verb('Used the computer', 'Using the computer', Monitor),
  // A turn's computer steps, grouped ("Computer activity").
  computer_activity: verb('Used the computer', 'Using the computer', Monitor),
  computer_record: verb('Recorded the screen', 'Recording the screen', Video),
  // Conversations from before 5.3.
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
  edit_video: verb('Edited a video', 'Editing a video', Scissors),
  video: verb('Worked on a video', 'Working on a video', Clapperboard),
  send_file: verb('Sent files', 'Sending files', Send),
  create_chart: verb('Created a chart', 'Creating a chart', ChartColumn),
  create_design: verb('Created a design', 'Creating a design', Palette),
  create_code_folder: verb(
    'Created a code folder',
    'Creating a code folder',
    FolderCode,
  ),
  use_code_folder: verb(
    'Opened a code folder',
    'Opening a code folder',
    Folder,
  ),
  clone_repository: verb(
    'Cloned a repository',
    'Cloning a repository',
    GitBranch,
  ),
  request_connection: verb('Asked to connect', 'Asking to connect', Plug),
  suggest_apps: verb('Suggested apps', 'Looking for apps', Plug),
  ask_person: verb(
    'Asked you a question',
    'Waiting for your answer',
    MessageCircleQuestion,
  ),
  export_to_pdf: verb('Exported a PDF', 'Exporting a PDF', FileText),
  page_create: verb('Wrote a page', 'Writing a page', LayoutTemplate),
  page_update: verb('Changed a page', 'Changing a page', LayoutTemplate),
  page_read: verb('Read a page', 'Reading a page', LayoutTemplate),
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
  outlook_search_mail: verb('Searched email', 'Searching email', Mail),
  outlook_read_mail: verb('Read an email', 'Reading an email', Mail),
  outlook_draft_mail: verb('Drafted an email', 'Drafting an email', Mail),
  outlook_send_mail: verb('Sent an email', 'Sending an email', Mail),
  outlook_reply_mail: verb('Replied to an email', 'Replying to an email', Mail),
  outlook_forward_mail: verb('Forwarded an email', 'Forwarding an email', Mail),
  outlook_update_mail: verb('Filed an email', 'Filing an email', Mail),
  outlook_delete_mail: verb('Deleted an email', 'Deleting an email', Mail),
  outlook_list_events: verb(
    'Checked the calendar',
    'Checking the calendar',
    Calendar,
  ),
  outlook_find_free_times: verb(
    'Found free times',
    'Finding free times',
    Calendar,
  ),
  outlook_create_event: verb('Added an event', 'Adding an event', Calendar),
  outlook_update_event: verb('Changed an event', 'Changing an event', Calendar),
  outlook_cancel_event: verb(
    'Cancelled an event',
    'Cancelling an event',
    Calendar,
  ),
  outlook_respond_event: verb(
    'Answered an invitation',
    'Answering an invitation',
    Calendar,
  ),
  delegate_work: verb('Delegated work', 'Delegating work', Bot),
  run_command: verb('Ran a command', 'Running a command', Terminal),
  shell: verb('Ran a command', 'Running a command', Terminal),
  read_terminal: verb('Read the terminal', 'Reading the terminal', Terminal),
  start_process: verb(
    'Started a background process',
    'Starting a background process',
    Terminal,
  ),
  check_process: verb(
    'Checked a background process',
    'Checking a background process',
    Terminal,
  ),
  stop_process: verb(
    'Stopped a background process',
    'Stopping a background process',
    Terminal,
  ),
  read_tool_output: verb(
    'Read more of a result',
    'Reading more of a result',
    FileText,
  ),
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
  developer_create_pull_request: verb(
    'Opened a pull request',
    'Opening a pull request',
    GitPullRequest,
  ),
  developer_import_sandbox_changes: verb(
    'Applied the sandbox changes',
    'Applying the sandbox changes',
    FilePen,
  ),
  developer_prepare_sandbox: verb(
    'Prepared the sandbox',
    'Preparing the sandbox',
    PackageOpen,
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
  [
    /^designer_/,
    verb('Worked on the design', 'Working on the design', Palette),
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
  // Settled steps carry the canonical name ("Browser Navigate" for browser_navigate).
  const key = bareName(name).toLowerCase().replace(/\s+/g, '_');
  if (Object.hasOwn(EXACT, key)) return EXACT[key];
  // Workspace file tools mirror the plain ones: workspace_write_file.
  const inner = key.replace(/^workspace_/, '');
  if (inner !== key && Object.hasOwn(EXACT, inner)) return EXACT[inner];
  for (const [pattern, value] of PATTERNS) if (pattern.test(key)) return value;
  return null;
}

/** The app behind a step or approval, and the tool's readable title in it ("Delete page"). */
type ToolApp = { name: string; tool?: string } | null | undefined;

/** "Searched the web", "Searching the web" or "Web search failed"; an app's tool reads as its own
 * title ("Delete page", shown after the app's name). */
export function stepVerb(
  name: string,
  status: StepStatus,
  app?: ToolApp,
): string {
  const tool = app?.tool;
  if (tool) {
    if (status === 'failed') return `${tool} failed`;
    if (status === 'blocked' || status === 'cancelled')
      return `${tool} skipped`;
    return tool;
  }
  const known = describe(name);
  // An app's tool with no app named reads as its words, never "MCP …" (B391).
  const fallback =
    (!known && unnamedAppTool(name)) || humanizeToken(bareName(name)) || 'Tool';
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

/**
 * Who let a step run without the person (A0): the Auto reviewer, or Full
 * autonomy. Its approval line says so ("Approved by the reviewer: …",
 * "Allowed by Hands-off"); the step's row shows a quiet tag for it.
 */
export function approvedWithoutYou(approval: string): string | null {
  if (/^approved by the reviewer\b/i.test(approval)) return 'Reviewer approved';
  if (/^allowed by hands-off\b/i.test(approval)) return 'Hands-off';
  return null;
}

/** "Frobnicate widget" reads "frobnicate widget" mid-sentence; "MCP" stays. */
function midSentence(label: string) {
  return /^\p{Lu}\p{Ll}/u.test(label)
    ? label[0].toLowerCase() + label.slice(1)
    : label;
}

/**
 * A tool call the model is still writing (B324), in its step's own words:
 * "Writing a page · 3 KB", never the tool's display name or its emoji.
 */
export function writingLabel(name: string, bytes: number): string {
  const size = `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${describe(name)?.running ?? 'Preparing the next step'} · ${size}`;
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
/**
 * The settings Row-Bot may change, in Settings' own words: a step names one
 * while its card is up, never its ID (F5). The finished step's card names it
 * from the change itself (row_bot.self_settings).
 */
const SETTING_NAMES: Record<string, string> = {
  thread_model: "This chat's model",
  default_model: 'Main model',
  vision_model: 'Vision model',
  name: 'Name',
  personality: 'Personality',
  context_size: 'Reading limit',
  cloud_context_size: 'Reading limit',
  dream_cycle: 'Dream Cycle',
  dream_window: 'Dream Cycle hours',
  run_dream_cycle: 'Dream Cycle',
  skill_toggle: 'Skills',
  skill_pin: 'Skills',
  tool_toggle: 'Tools',
  image_gen_model: 'Image model',
  video_gen_model: 'Video model',
  self_improvement: 'Learn new skills',
  read_aloud: 'Read aloud',
  live_captions: 'Live captions',
  log_detail: 'Log detail',
};

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
  if (typeof record.setting === 'string' && record.setting)
    return SETTING_NAMES[record.setting] ?? record.setting.replace(/_/g, ' ');
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
  /** Row-Bot's questions to the person among the steps (N4). */
  questions?: number;
};

/** A call the model got wrong and was told to send again (U11): no failure of the turn's. */
export function isCorrected(step: TranscriptTraceItem) {
  return step.specialization?.kind === 'request_corrected';
}

/**
 * The turn's steps in order, as a person reads them: a corrected call is left
 * out once the model sent that tool's call again, so only the last one the
 * model gave up on shows (one quiet line).
 */
export function orderedSteps(groups: TranscriptTraceGroup[]) {
  const steps = [...groups]
    .sort((first, second) => first.group_order - second.group_order)
    .flatMap((group) =>
      [...group.items].sort(
        (first, second) => first.call_order - second.call_order,
      ),
    );
  return steps.filter(
    (step, index) =>
      !isCorrected(step) ||
      !steps.some(
        (other, at) =>
          other !== step &&
          other.canonical_name === step.canonical_name &&
          (!isCorrected(other) || at > index),
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
      (step) =>
        (step.status === 'failed' && !isCorrected(step)) ||
        step.status === 'uncertain',
    ).length,
    skipped: steps.filter(
      (step) => step.status === 'blocked' || step.status === 'cancelled',
    ).length,
    pending: steps.filter((step) => step.status === 'pending').length,
    icons,
    current:
      [...steps].reverse().find((step) => step.status === 'pending') ?? null,
    questions: steps.filter(
      (step) => step.canonical_name === 'ask_person' && !isCorrected(step),
    ).length,
  };
}

/** "Used 3 tools", "Used 2 tools · 1 failed · 1 skipped", "1 tool skipped". */
export function activityLabel(summary: ActivitySummary): string {
  const noun = summary.total === 1 ? 'tool' : 'tools';
  // Nothing ran: every step was denied, blocked or cancelled.
  if (summary.total && summary.skipped === summary.total)
    return `${summary.total} ${noun} skipped`;
  // A turn that only asked the person reads as what it did (N4).
  if (
    summary.total &&
    summary.questions === summary.total &&
    !summary.failed &&
    !summary.skipped &&
    !summary.pending
  )
    return summary.total === 1 ? 'Asked you a question' : 'Asked you questions';
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
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  // Whole seconds first, so 6m 59.6s reads 7m, never 6m 60s.
  const total = Math.round(ms / 1000);
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

/**
 * How long a step ran, as this client watched it, or null when that is not
 * known. Under a tenth of a second the client only saw the step as it
 * finished (its start and end arrived together), so "0.0s" would be wrong.
 */
export function stepElapsed(
  span: { start: number; end?: number } | undefined,
): number | null {
  if (span?.end === undefined) return null;
  const ms = span.end - span.start;
  return ms >= 100 ? ms : null;
}

// Stems whose present participle dropped a silent "e" ("Saving" → "Save").
const SILENT_E = new Set([
  'analyz',
  'animat',
  'calculat',
  'chang',
  'clon',
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

// How app tools' titles usually begin ("Get commit"). A title that begins otherwise is named, never read as a verb.
const TOOL_VERBS = new Set(
  (
    'add append approve archive assign book cancel change check close comment copy create decrement delete ' +
    'download draft duplicate edit export fetch find generate get import increment insert invite list lock ' +
    'mark merge move open pay post publish query read refund reject remove rename reopen reply request reset ' +
    'resolve restore retrieve run save schedule search send set share start stop submit summarize sync tag ' +
    'transfer unlock update upload upsert write'
  ).split(' '),
);

// Nouns that take no "a" ("get data", not "get a data").
const UNCOUNTED = new Set(
  (
    'access analytics code content data feedback health help history info information mail media ' +
    'metadata money music news software status text usage weather work'
  ).split(' '),
);

/** "create product" → "create a product": a verb and one thing reads as a sentence (B388). */
function withArticle(phrase: string): string {
  const [verb, noun, ...rest] = phrase.split(' ');
  if (rest.length || !noun || !TOOL_VERBS.has(verb.toLowerCase()))
    return phrase;
  if (!/^[a-z]+$/.test(noun) || /s$/.test(noun) || UNCOUNTED.has(noun))
    return phrase;
  return `${verb} ${/^[aeiou]/.test(noun) ? 'an' : 'a'} ${noun}`;
}

/** "Allow Stripe to create a product?" for an app's tool, or "Allow Stripe to use “Balance”?". */
function appQuestion(app: string, tool: string): string {
  const phrase = midSentence(tool);
  return TOOL_VERBS.has(phrase.split(' ')[0].toLowerCase())
    ? `Allow ${app} to ${withArticle(phrase)}?`
    : `Allow ${app} to use “${tool}”?`;
}

/** An app tool's runtime name the server named no app for ("mcp_stripe_create_product"): its words, without
 * claiming an app (two connections can make one name), so never "MCP"; the name itself stays under Details. */
function unnamedAppTool(name: string): string {
  const found = /^mcp_([a-z0-9_]+)$/.exec(bareName(name));
  const words = found ? found[1].replaceAll('_', ' ').trim() : '';
  return words ? words[0].toUpperCase() + words.slice(1) : '';
}

/** "Send an email?" for a canonical tool name awaiting approval; "Allow Granola to delete a page?" for an app's. */
export function approvalQuestion(name: string, app?: ToolApp): string {
  if (app?.tool) return appQuestion(app.name, app.tool);
  if (!describe(name)) {
    const words = unnamedAppTool(name);
    if (words) return `Allow “${words}”?`;
    return `Allow ${humanizeToken(bareName(name)) || 'this action'}?`;
  }
  return `${imperative(stepVerb(name, 'pending'))}?`;
}

/** The runtime name of an app's tool, shown under Details only ("mcp_stripe_create_product"). */
export function rawToolName(name: string): string {
  const bare = bareName(name);
  return /^mcp_[a-z0-9_]+$/.test(bare) ? bare : '';
}

/** The action itself, for a label: "Delete a file", "Fixture action", or "Granola · Delete page". */
export function approvalAction(name: string, app?: ToolApp): string {
  if (app?.tool) return `${app.name} · ${app.tool}`;
  const words = !describe(name) && unnamedAppTool(name);
  if (words) return words;
  if (!describe(name)) return humanizeToken(bareName(name)) || 'This action';
  return imperative(stepVerb(name, 'pending'));
}

// One `key=value` argument as the agent writes it (Python repr).
const ARGUMENT =
  /(\w+)=('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[^,'"]*)(?:, (?=\w+=)|$)/y;

/**
 * A tool argument in words: `[{"sku": "A1", "qty": 2}]` reads "sku A1, qty 2". Every value is kept, an empty
 * one too (it is still sent: it can clear a field), and a list item holding a separator is quoted.
 */
export function readableValue(value: unknown, nested = false): string {
  if (Array.isArray(value)) {
    const structured = value.some((item) => item && typeof item === 'object');
    const text = value
      .map((item) =>
        typeof item === 'string' && /[,;]/.test(item)
          ? `"${item}"`
          : readableValue(item),
      )
      .join(structured ? '; ' : ', ');
    return nested && structured ? `(${text})` : text;
  }
  if (value && typeof value === 'object') {
    const text = Object.entries(value)
      .filter(([, item]) => item !== undefined)
      .map(
        ([key, item]) =>
          `${key.replace(/_/g, ' ')} ${readableValue(item, true)}`,
      )
      .join(', ');
    return nested ? `(${text})` : text;
  }
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return value === null ? 'empty' : String(value ?? '');
}

function unquote(value: string): string {
  // Python's True and False, as the agent writes a yes/no option.
  if (value === 'True' || value === 'False')
    return value === 'True' ? 'yes' : 'no';
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
