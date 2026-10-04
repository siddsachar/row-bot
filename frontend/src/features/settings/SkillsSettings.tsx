import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import PublicSkillHub, { type PublicSkillHubIO } from './PublicSkillHub';
import PublicSkillMaintenance, {
  type PublicSkillMaintenanceIO,
} from './PublicSkillMaintenance';
import {
  ChevronRight,
  FileUp,
  Globe2,
  MoreHorizontal,
  Pin,
  Plus,
  RefreshCw,
  Search,
} from 'lucide-react';
import { humanizeToken } from '../../ui/format';
import {
  SettingsAdvanced,
  SettingsSummary,
  SettingsTabs,
  SummaryChip,
  useTabForAnchor,
} from './anatomy';
import {
  Button,
  CompactAction,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Menu,
  Select,
  Skeleton,
  Surface,
  Toggle,
} from '../../ui/primitives';

export type SkillAction =
  | 'skill.preference'
  | 'skill.create'
  | 'skill.import'
  | 'skill.edit'
  | 'skill.duplicate'
  | 'skill.delete'
  | 'skill.proposal.apply'
  | 'skill.proposal.reject';
export type SkillSummary = {
  id: string;
  display_name: string;
  icon: string;
  description: string;
  source: 'user' | 'bundled';
  public?: boolean;
  version: string;
  tags: string[];
  activation: Record<string, string[]>;
  available: boolean;
  pinned: boolean;
  editable: boolean;
  tool_guide: boolean;
  revision: string;
  instructions_preview: string;
  truncated: boolean;
};
export type SkillPage = {
  schema_version: 1;
  revision: string;
  availability: string;
  items: SkillSummary[];
  total: number | null;
  next_cursor: string | null;
};
export type SkillDetail = {
  schema_version: 1;
  library_revision: string;
  skill: SkillSummary & { instructions: string };
};
export type SkillProposal = {
  id: string;
  type: 'create_skill' | 'patch_skill' | 'consolidate_skills';
  title: string;
  rationale: string;
  risk: 'low' | 'medium' | 'high';
  status: string;
  preview: Record<string, unknown>;
};
export type SkillProposalPage = {
  schema_version: 1;
  revision: string;
  items: SkillProposal[];
  truncated: boolean;
};
export type SkillFields = {
  display_name: string;
  icon: string;
  description: string;
  instructions: string;
  tags: string[];
  activation: Record<string, string[]>;
  version: string;
};
export type SkillCommand = {
  command_id: string;
  type: SkillAction;
  payload: Record<string, unknown>;
};
export type SkillReview = {
  schema_version: 1;
  action: SkillAction;
  revision: string;
  target: string;
  before_revision: string | null;
  after: Record<string, unknown> | null;
  action_digest: string;
  review_id: string;
};
export type SkillReceipt = {
  command_id: string;
  status: 'completed' | 'partial' | 'rejected';
  action: SkillAction;
  skill_id: string | null;
  revision: string | null;
  code: string | null;
};
type Attempt = { command: SkillCommand; review: SkillReview };
type Editor = { mode: 'create' | 'edit'; name: string; fields: SkillFields };
type State = {
  active: boolean;
  page: SkillPage | null;
  proposals: SkillProposalPage | null;
  detail: SkillDetail | null;
  query: string;
  source: '' | 'user' | 'bundled' | 'public';
  filter: 'all' | 'available' | 'pinned' | 'custom' | 'public';
  sort: 'name' | 'recent' | 'tokens' | 'source';
  editor: Editor | null;
  importText: string;
  duplicateName: string;
  reviewed: Attempt | null;
  pending: Attempt | null;
  busy: string;
  message: string;
};

// The server accepts only these names; say so before the review, not after.
const SKILL_NAME = /^[a-z][a-z0-9_]{1,63}$/;
const SKILL_NAME_HELP =
  'Lowercase letters, numbers and underscores, starting with a letter (e.g. weekly_review).';
const skillNameInvalid = (name: string) =>
  name.length > 0 && !SKILL_NAME.test(name);

function SkillNameHelp({ id, name }: { id: string; name: string }) {
  const invalid = skillNameInvalid(name);
  return (
    <small
      id={id}
      className={`settings-help${invalid ? ' is-invalid' : ''}`}
      aria-live="polite"
    >
      {invalid ? `Not a valid name. ${SKILL_NAME_HELP}` : SKILL_NAME_HELP}
    </small>
  );
}

// A refused field is fixed in the form, not by reloading.
function reviewFailure(cause: unknown) {
  const code =
    cause && typeof cause === 'object'
      ? (cause as { code?: unknown }).code
      : undefined;
  if (code === 'invalid_skill_fields')
    return 'Check the fields: a display name, icon and instructions are required, and each value must fit its limit.';
  if (code === 'invalid_skill_target')
    return 'Choose another skill name: it is not valid or is already in use.';
  return 'This change could not be reviewed. Reload and try again.';
}

const blankFields = (): SkillFields => ({
  display_name: '',
  icon: '✨',
  description: '',
  instructions: '',
  tags: [],
  activation: {},
  version: '1.0',
});

export function createSkillsSettingsSession() {
  let state: State = {
    active: true,
    page: null,
    proposals: null,
    detail: null,
    query: '',
    source: '',
    filter: 'all',
    sort: 'name',
    editor: null,
    importText: '',
    duplicateName: '',
    reviewed: null,
    pending: null,
    busy: '',
    message: '',
  };
  const listeners = new Set<() => void>();
  const reads = new Set<AbortController>();
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    update,
    beginRead: () => {
      const abort = new AbortController();
      if (state.active) reads.add(abort);
      else abort.abort();
      return abort;
    },
    endRead: (abort: AbortController) => reads.delete(abort),
    hasRetained: () =>
      state.active &&
      Boolean(
        state.editor ||
        state.importText ||
        state.duplicateName ||
        state.reviewed ||
        state.pending,
      ),
    dispose: () => {
      reads.forEach((abort) => abort.abort());
      reads.clear();
      state = {
        ...state,
        active: false,
        page: null,
        proposals: null,
        detail: null,
        editor: null,
        reviewed: null,
        pending: null,
        busy: '',
        message: 'Sign in again to manage skills.',
      };
      listeners.forEach((listener) => listener());
    },
  };
}
export type SkillsSettingsSession = ReturnType<
  typeof createSkillsSettingsSession
>;

export type SkillsSettingsIO = {
  list: (
    query: string,
    source: string | undefined,
    cursor: string | undefined,
    filter: State['filter'],
    sort: State['sort'],
    signal: AbortSignal,
  ) => Promise<SkillPage>;
  detail: (id: string, signal: AbortSignal) => Promise<SkillDetail>;
  proposals: (signal: AbortSignal) => Promise<SkillProposalPage>;
  review: (
    action: SkillAction,
    payload: Record<string, unknown>,
    signal: AbortSignal,
  ) => Promise<SkillReview>;
  execute: (
    command: SkillCommand,
    review: SkillReview,
  ) => Promise<SkillReceipt>;
  receipt: (commandId: string, signal: AbortSignal) => Promise<SkillReceipt>;
};

function validPage(value: SkillPage) {
  return (
    value.schema_version === 1 &&
    ['available', 'missing', 'unavailable'].includes(value.availability) &&
    Array.isArray(value.items) &&
    value.items.length <= 50
  );
}

function skillSourceLabel(skill: SkillSummary) {
  if (skill.public) return 'Public';
  return skill.source === 'user' ? 'Custom' : 'Bundled';
}

export default function SkillsSettings({
  integrationId,
  session,
  io,
  hub,
  hubMaintenance,
  ownerKey = '',
}: {
  integrationId?: string;
  session: SkillsSettingsSession;
  io: SkillsSettingsIO;
  hub?: PublicSkillHubIO;
  hubMaintenance?: PublicSkillMaintenanceIO;
  ownerKey?: string;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [hubReload, setHubReload] = useState(0);

  const load = useCallback(
    async (cursor?: string) => {
      const current = session.getSnapshot();
      if (
        !current.active ||
        current.busy ||
        (cursor && !current.page?.next_cursor)
      )
        return;
      const abort = session.beginRead();
      session.update({ busy: cursor ? 'more' : 'load', message: '' });
      try {
        const page = await io.list(
          current.query.trim(),
          current.source || undefined,
          cursor,
          current.filter,
          current.sort,
          abort.signal,
        );
        if (abort.signal.aborted || !validPage(page)) return;
        if (cursor && current.page && page.revision !== current.page.revision) {
          session.update({
            busy: '',
            message: 'The skill library changed. Reload before continuing.',
          });
        } else {
          session.update({
            page:
              cursor && current.page
                ? {
                    ...page,
                    items: [...current.page.items, ...page.items].slice(-200),
                  }
                : page,
            busy: '',
            reviewed: null,
          });
        }
      } catch {
        if (!abort.signal.aborted)
          session.update({
            busy: '',
            message:
              'The saved skill library is unavailable. Reload to try again.',
          });
      } finally {
        session.endRead(abort);
      }
    },
    [session, io],
  );

  const loadProposals = useCallback(async () => {
    const abort = session.beginRead();
    try {
      const proposals = await io.proposals(abort.signal);
      if (
        !abort.signal.aborted &&
        proposals.schema_version === 1 &&
        proposals.items.length <= 100
      )
        session.update({ proposals });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          message:
            'Skill proposals are unavailable. Saved skills are unchanged.',
        });
    } finally {
      session.endRead(abort);
    }
  }, [session, io]);

  useEffect(() => {
    if (!session.getSnapshot().page && !session.getSnapshot().busy) void load();
    if (!session.getSnapshot().proposals) void loadProposals();
    return () => undefined;
  }, [session, io, load, loadProposals]);

  const open = async (id: string) => {
    if (session.getSnapshot().busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'detail', message: '' });
    try {
      const detail = await io.detail(id, abort.signal);
      if (!abort.signal.aborted && detail.schema_version === 1)
        session.update({ detail, busy: '', duplicateName: `${id}_custom` });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message: 'This skill changed or is unavailable. Reload the library.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  useEffect(() => {
    if (
      !integrationId ||
      !state.page ||
      state.busy ||
      state.detail?.skill.id === integrationId
    )
      return;
    if (integrationId === 'create') {
      session.update({
        editor: { mode: 'create', name: '', fields: blankFields() },
        detail: null,
      });
    } else void open(integrationId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [integrationId, state.page]);

  const requestReview = async (
    action: SkillAction,
    payload: Record<string, unknown>,
  ) => {
    const current = session.getSnapshot();
    if (!current.active || current.busy || current.pending || !current.page)
      return;
    const command: SkillCommand = {
      command_id: crypto.randomUUID(),
      type: action,
      payload,
    };
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '' });
    try {
      const review = await io.review(action, payload, abort.signal);
      if (
        abort.signal.aborted ||
        review.action !== action ||
        review.revision !== current.page.revision
      )
        throw Error();
      const attempt = { command, review };
      const needsConfirmation =
        action === 'skill.delete' || action === 'skill.proposal.reject';
      session.update({
        reviewed: attempt,
        busy: '',
        message: needsConfirmation ? 'Confirm this exact removal.' : '',
      });
      if (!needsConfirmation) await apply(attempt);
    } catch (cause) {
      if (!abort.signal.aborted)
        session.update({ busy: '', message: reviewFailure(cause) });
    } finally {
      session.endRead(abort);
    }
  };

  const apply = async (attempt: Attempt | null) => {
    const current = session.getSnapshot();
    if (
      !attempt ||
      !current.active ||
      current.busy ||
      (current.reviewed !== attempt && current.pending !== attempt)
    )
      return;
    session.update({
      pending: attempt,
      reviewed: null,
      busy: 'apply',
      message: '',
    });
    try {
      const result = await io.execute(
        {
          ...attempt.command,
          payload: {
            ...attempt.command.payload,
            review_id: attempt.review.review_id,
          },
        },
        attempt.review,
      );
      if (result.command_id !== attempt.command.command_id) throw Error();
      if (result.status !== 'completed') {
        session.update({
          busy: '',
          message:
            "Row-Bot couldn't confirm that change. Check again before doing anything else.",
        });
        return;
      }
      session.update({
        pending: null,
        busy: '',
        detail: null,
        editor: null,
        importText: '',
        message:
          result.action === 'skill.delete'
            ? 'Skill removed. Previous bytes are retained for recovery.'
            : 'Skill change saved.',
      });
      await load();
      await loadProposals();
      session.update({
        message:
          result.action === 'skill.delete'
            ? 'Skill removed. Previous bytes are retained for recovery.'
            : 'Skill change saved.',
      });
    } catch {
      session.update({
        busy: '',
        message:
          "Row-Bot couldn't confirm that change. Check again before doing anything else.",
      });
    }
  };

  const recover = async () => {
    const pending = session.getSnapshot().pending;
    if (!pending || session.getSnapshot().busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'receipt', message: '' });
    try {
      const result = await io.receipt(pending.command.command_id, abort.signal);
      if (
        abort.signal.aborted ||
        result.command_id !== pending.command.command_id
      )
        throw Error();
      if (result.status === 'completed') {
        session.update({
          pending: null,
          busy: '',
          detail: null,
          editor: null,
          message: 'The original skill change is confirmed.',
        });
        await load();
        session.update({ message: 'The original skill change is confirmed.' });
      } else
        session.update({
          busy: '',
          message:
            'The original change is still unconfirmed. No new change was started.',
        });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            "Row-Bot can't find what happened. No new change was started.",
        });
    } finally {
      session.endRead(abort);
    }
  };

  const startEdit = () => {
    const skill = session.getSnapshot().detail?.skill;
    if (!skill?.editable) return;
    session.update({
      editor: {
        mode: 'edit',
        name: skill.id,
        fields: {
          display_name: skill.display_name,
          icon: skill.icon,
          description: skill.description,
          instructions: skill.instructions,
          tags: skill.tags,
          activation: skill.activation,
          version: skill.version,
        },
      },
    });
  };
  const changeField = (name: keyof SkillFields, value: string) => {
    const editor = session.getSnapshot().editor;
    if (!editor) return;
    const fields = {
      ...editor.fields,
      [name]:
        name === 'tags'
          ? value
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean)
          : value,
    };
    session.update({ editor: { ...editor, fields }, reviewed: null });
  };
  const locked = !state.active || Boolean(state.busy || state.pending);

  function changeList(patch: Partial<State>) {
    session.update(patch);
    void load();
  }
  const revision = state.page?.revision;
  const displayedSkills = state.page?.items ?? [];
  const shownAvailable =
    state.page?.items.filter((skill) => skill.available).length ?? 0;
  const shownPinned =
    state.page?.items.filter((skill) => skill.pinned).length ?? 0;
  const shownCustom =
    state.page?.items.filter((skill) => skill.source === 'user').length ?? 0;
  // Counts describe the loaded page: say "matching" while a search or filter
  // applies, and "shown" only when the page holds part of the library.
  const narrowed = Boolean(
    state.query.trim() || state.source || state.filter !== 'all',
  );
  const complete =
    state.page?.total != null &&
    (state.page?.items.length ?? 0) >= state.page.total;
  const shown = complete ? '' : ' shown';

  const [tab, setTab] = useTabForAnchor<'installed' | 'discover'>('installed', {
    'skill-library': 'installed',
    'public-skills': 'discover',
  });
  const [importOpen, setImportOpen] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  useEffect(() => () => clearTimeout(searchTimer.current), []);
  const installed = (
    <div
      className="stack settings-skill-installed"
      data-setting-anchor="skill-library"
    >
      <form
        className="settings-list-toolbar settings-skill-toolbar"
        role="search"
        aria-label="Search installed skills"
        onSubmit={(event) => {
          event.preventDefault();
          clearTimeout(searchTimer.current);
          void load();
        }}
      >
        <label className="settings-inline-search">
          <span className="visually-hidden">Search skills</span>
          <Search size={14} aria-hidden />
          <Input
            type="search"
            maxLength={256}
            placeholder="Search skills"
            value={state.query}
            onChange={(event) => {
              session.update({ query: event.target.value });
              clearTimeout(searchTimer.current);
              searchTimer.current = setTimeout(() => void load(), 350);
            }}
          />
        </label>
        <Select
          aria-label="Skill source"
          value={state.source}
          disabled={locked}
          onChange={(event) =>
            changeList({ source: event.target.value as State['source'] })
          }
        >
          <option value="">All sources</option>
          <option value="user">My skills</option>
          <option value="bundled">Built in</option>
          <option value="public">Public</option>
        </Select>
        <Select
          aria-label="Filter"
          value={state.filter}
          disabled={locked}
          onChange={(event) =>
            changeList({ filter: event.target.value as State['filter'] })
          }
        >
          <option value="all">All</option>
          <option value="pinned">Pinned</option>
          <option value="available">Available</option>
          <option value="custom">Custom</option>
          <option value="public">Public</option>
        </Select>
        <Select
          aria-label="Sort"
          value={state.sort}
          disabled={locked}
          onChange={(event) =>
            changeList({ sort: event.target.value as State['sort'] })
          }
        >
          <option value="name">Name</option>
          <option value="recent">Recently used</option>
          <option value="tokens">Token cost</option>
          <option value="source">Source</option>
        </Select>
        <Button
          variant="primary"
          disabled={locked}
          onClick={() =>
            session.update({
              editor: { mode: 'create', name: '', fields: blankFields() },
              detail: null,
            })
          }
        >
          <Plus size={15} aria-hidden />
          Create skill
        </Button>
        <Menu
          label="More skill actions"
          iconOnly
          variant="ghost"
          className="icon-action icon-action-md"
          actions={[
            {
              label: 'Reload skills',
              icon: <RefreshCw size={16} />,
              disabled: locked,
              onSelect: () => void load(),
            },
            {
              label: 'Import a skill',
              icon: <FileUp size={16} />,
              onSelect: () => setImportOpen(true),
            },
            {
              label: 'Browse public skills',
              icon: <Globe2 size={16} />,
              onSelect: () => setTab('discover'),
            },
          ]}
        >
          <MoreHorizontal size={18} aria-hidden />
        </Menu>
      </form>
      {state.busy === 'load' && <Skeleton label="Loading saved skills" />}
      {state.message && <p role="status">{state.message}</p>}
      {state.pending && (
        <Surface elevated>
          <p>
            Row-Bot couldn't confirm the last{' '}
            {state.pending.command.type.replaceAll('.', ' ')}. Check it before
            anything else.
          </p>
          <Button disabled={Boolean(state.busy)} onClick={() => void recover()}>
            Check again
          </Button>
        </Surface>
      )}
      {state.page?.availability === 'missing' && (
        <EmptyState title="No saved skill library">
          Create a skill when you have a repeatable workflow to save.
        </EmptyState>
      )}
      {state.page?.availability === 'unavailable' && (
        <ErrorState title="Skills unavailable">
          The saved library could not be read safely.
        </ErrorState>
      )}
      {state.page?.availability === 'available' && (
        <>
          <p role="status" className="settings-list-count">
            {displayedSkills.length} shown of {state.page.total ?? 'unknown'}{' '}
            matching skills
          </p>
          {!displayedSkills.length && (
            <EmptyState title="No matching skills">
              Try another search or source.
            </EmptyState>
          )}
          <ul className="settings-results settings-catalog-list settings-skill-list">
            {displayedSkills.map((skill) => (
              <li className="settings-skill-row" key={skill.id}>
                <span className="settings-skill-icon" aria-hidden>
                  {skill.icon || '✦'}
                </span>
                <div className="settings-skill-summary">
                  <div>
                    <strong>
                      {skill.icon} {skill.display_name}
                    </strong>
                    {skill.pinned && (
                      <span className="settings-skill-badge">Pinned</span>
                    )}
                  </div>
                  <small>{skill.description || 'No description saved.'}</small>
                  <span className="settings-skill-metadata">
                    <span>{skillSourceLabel(skill)}</span>
                    <span>{skill.available ? 'Available' : 'Unavailable'}</span>
                    <span>v{skill.version}</span>
                    {skill.tags.slice(0, 2).map((tag) => (
                      <span key={tag}>#{tag}</span>
                    ))}
                  </span>
                </div>
                <div
                  className="settings-skill-preferences"
                  role="group"
                  aria-label={`${skill.display_name} preferences`}
                >
                  <CompactAction
                    label={skill.pinned ? 'Unpin default' : 'Pin for new work'}
                    disabled={locked || skill.tool_guide}
                    aria-pressed={skill.pinned}
                    onClick={() =>
                      void requestReview('skill.preference', {
                        revision,
                        name: skill.id,
                        preference: 'pin_defaults',
                        value: !skill.pinned,
                      })
                    }
                  >
                    <Pin
                      size={15}
                      fill={skill.pinned ? 'currentColor' : 'none'}
                      aria-hidden
                    />
                  </CompactAction>
                  <Toggle
                    label={`${skill.display_name} available`}
                    checked={skill.available}
                    disabled={locked || skill.tool_guide}
                    onChange={() =>
                      void requestReview('skill.preference', {
                        revision,
                        name: skill.id,
                        preference: 'availability',
                        value: !skill.available,
                      })
                    }
                  />
                </div>
                <CompactAction
                  label="Open"
                  disabled={locked}
                  onClick={() => void open(skill.id)}
                >
                  <ChevronRight size={16} aria-hidden />
                </CompactAction>
              </li>
            ))}
          </ul>
          {state.page.next_cursor && (
            <Button
              disabled={locked}
              onClick={() => void load(state.page?.next_cursor ?? undefined)}
            >
              Load more skills
            </Button>
          )}
        </>
      )}
      {hubMaintenance && (
        <PublicSkillMaintenance
          io={hubMaintenance}
          ownerKey={ownerKey}
          reload={hubReload}
          onChanged={() => void load()}
        />
      )}
      <SettingsAdvanced
        summary="Import a skill"
        meta="Inspect SKILL.md text before saving it locally"
        open={importOpen}
        onOpenChange={setImportOpen}
      >
        <p className="settings-help">
          Paste the text of a SKILL.md file you wrote or trust. Row-Bot checks
          the exact content before saving it locally. Public skills install from
          Discover.
        </p>
        <Field label="Import SKILL.md text">
          <textarea
            className="input"
            rows={5}
            maxLength={65536}
            value={state.importText}
            onChange={(event) =>
              session.update({
                importText: event.target.value,
                reviewed: null,
              })
            }
          />
        </Field>
        <div>
          <Button
            disabled={locked || !revision || !state.importText.trim()}
            onClick={() =>
              void requestReview('skill.import', {
                revision,
                content: state.importText,
              })
            }
          >
            Import skill
          </Button>
        </div>
      </SettingsAdvanced>
    </div>
  );

  return (
    <section
      className="stack settings-skills-page"
      aria-label="Skills settings"
      aria-busy={Boolean(state.busy)}
    >
      {!integrationId && (
        <>
          <SettingsSummary>
            {state.page?.availability === 'available' && (
              <span
                className="settings-summary-group"
                role="group"
                aria-label="Displayed skill totals"
              >
                {narrowed ? (
                  <SummaryChip>
                    {state.page.total ?? 'Some'} matching
                  </SummaryChip>
                ) : (
                  <>
                    <SummaryChip tone="success">
                      {shownAvailable} available{shown}
                    </SummaryChip>
                    <SummaryChip>
                      {shownPinned} pinned{shown}
                    </SummaryChip>
                    <SummaryChip>
                      {shownCustom} custom{shown}
                    </SummaryChip>
                    {!complete && (
                      <SummaryChip>
                        {state.page.total ?? 'Unknown'} total
                      </SummaryChip>
                    )}
                  </>
                )}
              </span>
            )}
          </SettingsSummary>
          <SettingsTabs
            label="Skills"
            value={tab}
            onChange={setTab}
            tabs={[
              {
                id: 'installed',
                label: 'Installed',
                meta: state.page?.total ?? undefined,
                content: installed,
              },
              ...(hub
                ? [
                    {
                      id: 'discover' as const,
                      label: 'Discover',
                      content: (
                        <div data-setting-anchor="public-skills">
                          {hub ? (
                            <PublicSkillHub
                              io={hub}
                              ownerKey={ownerKey}
                              onInstalled={() => {
                                void load();
                                setHubReload((value) => value + 1);
                              }}
                            />
                          ) : (
                            <p className="muted">
                              Public skill sources are unavailable.
                            </p>
                          )}
                        </div>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </>
      )}
      {integrationId && hubMaintenance && (
        <PublicSkillMaintenance
          io={hubMaintenance}
          ownerKey={ownerKey}
          skillName={integrationId}
          onChanged={() => void load()}
        />
      )}
      {integrationId && state.message && <p role="status">{state.message}</p>}
      {integrationId && state.pending && (
        <Button onClick={() => void recover()}>
          Check original skill change
        </Button>
      )}
      {state.detail && (
        <Surface elevated>
          <h2>
            {state.detail.skill.icon} {state.detail.skill.display_name}
          </h2>
          <p>{state.detail.skill.description}</p>
          {integrationId && (
            <div className="button-row">
              <Field label="Available in chats" layout="row">
                <Toggle
                  label="Available in chats"
                  checked={state.detail.skill.available}
                  disabled={locked || state.detail.skill.tool_guide}
                  onChange={() =>
                    void requestReview('skill.preference', {
                      revision,
                      name: state.detail!.skill.id,
                      preference: 'availability',
                      value: !state.detail!.skill.available,
                    })
                  }
                />
              </Field>
              <Button
                disabled={locked || state.detail.skill.tool_guide}
                aria-pressed={state.detail.skill.pinned}
                onClick={() =>
                  void requestReview('skill.preference', {
                    revision,
                    name: state.detail!.skill.id,
                    preference: 'pin_defaults',
                    value: !state.detail!.skill.pinned,
                  })
                }
              >
                {state.detail.skill.pinned
                  ? 'Unpin default'
                  : 'Pin for new work'}
              </Button>
            </div>
          )}
          <pre className="text-preview settings-skill-instructions">
            {state.detail.skill.instructions}
          </pre>
          <div className="button-row">
            <Button
              disabled={locked || !state.detail.skill.editable}
              onClick={startEdit}
            >
              Edit skill
            </Button>
            <Button
              variant="danger"
              disabled={locked || !revision || !state.detail.skill.editable}
              onClick={() =>
                void requestReview('skill.delete', {
                  revision,
                  name: state.detail?.skill.id,
                  skill_revision: state.detail?.skill.revision,
                })
              }
            >
              Delete skill
            </Button>
          </div>
          <div className="settings-skill-duplicate">
            <Field label="Duplicate name">
              <Input
                value={state.duplicateName}
                maxLength={64}
                aria-describedby="skill-duplicate-name-help"
                aria-invalid={
                  skillNameInvalid(state.duplicateName) || undefined
                }
                onChange={(event) =>
                  session.update({
                    duplicateName: event.target.value,
                    reviewed: null,
                  })
                }
              />
            </Field>
            <Button
              disabled={
                locked ||
                !revision ||
                !state.duplicateName ||
                skillNameInvalid(state.duplicateName)
              }
              onClick={() =>
                void requestReview('skill.duplicate', {
                  revision,
                  name: state.detail?.skill.id,
                  new_name: state.duplicateName,
                })
              }
            >
              Duplicate skill
            </Button>
            <SkillNameHelp
              id="skill-duplicate-name-help"
              name={state.duplicateName}
            />
          </div>
        </Surface>
      )}

      {state.editor && (
        <Surface elevated>
          <h2>
            {state.editor.mode === 'create' ? 'Create skill' : 'Edit skill'}
          </h2>
          {state.editor.mode === 'create' && (
            <>
              <Field label="Skill name">
                <Input
                  maxLength={64}
                  pattern="[a-z][a-z0-9_]{1,63}"
                  aria-describedby="skill-name-help"
                  aria-invalid={
                    skillNameInvalid(state.editor.name) || undefined
                  }
                  value={state.editor.name}
                  onChange={(event) =>
                    session.update({
                      editor: state.editor
                        ? { ...state.editor, name: event.target.value }
                        : null,
                      reviewed: null,
                    })
                  }
                />
              </Field>
              <SkillNameHelp id="skill-name-help" name={state.editor.name} />
            </>
          )}
          <Field label="Display name">
            <Input
              maxLength={128}
              value={state.editor.fields.display_name}
              onChange={(event) =>
                changeField('display_name', event.target.value)
              }
            />
          </Field>
          <Field label="Icon">
            <Input
              maxLength={32}
              value={state.editor.fields.icon}
              onChange={(event) => changeField('icon', event.target.value)}
            />
          </Field>
          <Field label="Description">
            <Input
              maxLength={1024}
              value={state.editor.fields.description}
              onChange={(event) =>
                changeField('description', event.target.value)
              }
            />
          </Field>
          <Field label="Instructions">
            <textarea
              className="input"
              rows={10}
              maxLength={49152}
              value={state.editor.fields.instructions}
              onChange={(event) =>
                changeField('instructions', event.target.value)
              }
            />
          </Field>
          <Field label="Tags">
            <Input
              maxLength={4096}
              value={state.editor.fields.tags.join(', ')}
              onChange={(event) => changeField('tags', event.target.value)}
            />
          </Field>
          <div className="button-row">
            <Button
              disabled={
                locked ||
                !revision ||
                !state.editor.name ||
                (state.editor.mode === 'create' &&
                  skillNameInvalid(state.editor.name)) ||
                !state.editor.fields.display_name ||
                !state.editor.fields.instructions
              }
              onClick={() =>
                void requestReview(
                  state.editor?.mode === 'create'
                    ? 'skill.create'
                    : 'skill.edit',
                  state.editor?.mode === 'create'
                    ? {
                        revision,
                        name: state.editor.name,
                        fields: state.editor.fields,
                      }
                    : {
                        revision,
                        name: state.editor?.name,
                        skill_revision: state.detail?.skill.revision,
                        fields: state.editor?.fields,
                      },
                )
              }
            >
              {state.editor.mode === 'create' ? 'Save new skill' : 'Save skill'}
            </Button>
            <Button
              disabled={locked}
              onClick={() => session.update({ editor: null, reviewed: null })}
            >
              Cancel
            </Button>
          </div>
        </Surface>
      )}

      {state.proposals && (
        <details className="settings-supplemental-disclosure">
          <summary>
            <span>
              <strong>Skill proposals</strong>
              <small>{state.proposals.items.length} saved suggestions</small>
            </span>
          </summary>
          <Surface>
            {state.proposals.truncated && (
              <p>Only the first 100 saved proposals are shown.</p>
            )}
            {!state.proposals.items.length && <p>No saved skill proposals.</p>}
            <ul className="settings-results">
              {state.proposals.items.map((proposal) => (
                <li key={proposal.id}>
                  <details>
                    <summary>
                      {proposal.title} ·{' '}
                      {humanizeToken(proposal.status).toLowerCase()}
                    </summary>
                    <p>{proposal.rationale}</p>
                    <p>Risk: {humanizeToken(proposal.risk).toLowerCase()}</p>
                    <div className="button-row">
                      <Button
                        disabled={
                          locked ||
                          !revision ||
                          ['applied', 'verified', 'rejected'].includes(
                            proposal.status,
                          )
                        }
                        onClick={() =>
                          void requestReview('skill.proposal.apply', {
                            revision,
                            proposal_id: proposal.id,
                            reason: '',
                          })
                        }
                      >
                        Apply proposal
                      </Button>
                      <Button
                        variant="danger"
                        disabled={
                          locked ||
                          !revision ||
                          ['applied', 'verified', 'rejected'].includes(
                            proposal.status,
                          )
                        }
                        onClick={() =>
                          void requestReview('skill.proposal.reject', {
                            revision,
                            proposal_id: proposal.id,
                            reason: 'Rejected from Skills settings.',
                          })
                        }
                      >
                        Reject proposal
                      </Button>
                    </div>
                  </details>
                </li>
              ))}
            </ul>
          </Surface>
        </details>
      )}
      {state.reviewed &&
        (state.reviewed.command.type === 'skill.delete' ||
          state.reviewed.command.type === 'skill.proposal.reject') && (
          <Surface elevated>
            <h2>
              {state.reviewed.command.type === 'skill.delete'
                ? 'Delete this skill?'
                : 'Reject this proposal?'}
            </h2>
            <p>
              {state.reviewed.command.type === 'skill.delete'
                ? 'Delete skill'
                : 'Reject proposal'}{' '}
              “
              {state.reviewed.command.type === 'skill.delete' &&
              state.detail?.skill.id === state.reviewed.review.target
                ? state.detail.skill.display_name
                : state.reviewed.review.target}
              ”.
            </p>
            <p>
              The exact saved version shown here will be checked again before
              the effect starts.
            </p>
            <div className="button-row">
              <Button
                variant={
                  state.reviewed.command.type.includes('delete') ||
                  state.reviewed.command.type.includes('reject')
                    ? 'danger'
                    : 'primary'
                }
                disabled={locked}
                onClick={() => void apply(state.reviewed)}
              >
                Confirm removal
              </Button>
              <Button
                disabled={locked}
                onClick={() => session.update({ reviewed: null, message: '' })}
              >
                {state.reviewed.command.type === 'skill.delete'
                  ? 'Keep skill'
                  : 'Keep proposal'}
              </Button>
            </div>
          </Surface>
        )}
    </section>
  );
}
