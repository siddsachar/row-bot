import {
  ProviderSettingsSession,
  useProviderSettingsValue,
} from '../settings/provider-settings-sessions';
import { useEffect, useRef, type ReactNode } from 'react';
import {
  Button,
  ErrorState,
  Hint,
  IconButton,
  Input,
  Segmented,
  Select,
  Toggle,
} from '../../ui/primitives';
import { useNotify } from '../../ui/overlays';
import {
  SettingsAdvanced,
  SettingsGroup,
  SettingsItem,
  SettingsStatus,
  StatusLine,
} from '../settings/anatomy';
import { Check, PictureInPicture2, RefreshCw, Sparkles } from 'lucide-react';

export type BuddyPreferences = {
  visible: boolean;
  collapsed: boolean;
  display_name: string;
  personality: string;
  personality_description: string;
  bubble_verbosity: 'quiet' | 'normal' | 'chatty';
  animation_intensity: 'quiet' | 'normal' | 'expressive';
  pack_id: string;
};
export type BuddySnapshot = {
  schema_version: 1;
  revision: string;
  preferences: BuddyPreferences;
  status: {
    mood: string;
    animation: string;
    energy: number;
    focus: number;
    alert: number;
    event_id: number;
    label: string;
  };
  placement: 'docked';
  native_placement_retained: boolean;
  conversation_id?: string | null;
  activity?:
    | 'idle'
    | 'thinking'
    | 'streaming'
    | 'tool'
    | 'approval'
    | 'stopping'
    | 'completed'
    | 'stopped'
    | 'error'
    | 'disconnected';
};
export type BuddyPack = {
  id: string;
  name: string;
  revision: string;
  runtime: string;
  available: boolean;
  generated: boolean;
  assets: { id: string; content_type: string }[];
  animation_map: Record<string, string>;
};
export type BuddyPackPage = {
  revision: string;
  total: number;
  packs: BuddyPack[];
  next_cursor: string | null;
};
export type BuddyControlsProps = {
  editor?: ProviderSettingsSession;
  scopeKey: string;
  snapshot: BuddySnapshot | null;
  settingsOpen: boolean;
  companionVisible?: boolean;
  packs: BuddyPackPage | null;
  currentRunId: string | null;
  renderAvatar?(snapshot: BuddySnapshot): ReactNode;
  renderPackPreview?(pack: BuddyPack): ReactNode;
  previewUrl?(pack: BuddyPack): string | null;
  onSettings(): void;
  onUndock?(): void;
  /** Settings: the last look tile, "New look…", opens the generation flow. */
  onNewLook?(): void;
  /** The New look tile's second line (a generation in progress). */
  newLookStatus?: string;
  save(
    changes: Partial<BuddyPreferences>,
    revision: string,
  ): Promise<BuddySnapshot>;
  loadPacks(cursor: string): Promise<BuddyPackPage>;
  reload(): Promise<BuddySnapshot>;
  stop(runId: string): Promise<void>;
};

const personalities = {
  warm_mystical: 'Warm mystical',
  calm_focus: 'Calm focus',
  playful_helper: 'Playful helper',
  quiet_guardian: 'Quiet guardian',
  curious_scholar: 'Curious scholar',
};

/** What the floating notice calls each saved preference (B258). */
const preferenceNames: Record<keyof BuddyPreferences, string> = {
  visible: 'Show Buddy',
  collapsed: 'Compact size',
  display_name: 'Name',
  personality: 'Personality',
  personality_description: 'Style notes',
  bubble_verbosity: 'Bubbles',
  animation_intensity: 'Motion',
  pack_id: 'Look',
};

// Text saves when the field is left (or on Enter); everything else at once.
const TEXT_KEYS = new Set<keyof BuddyPreferences>([
  'display_name',
  'personality_description',
]);

function videoCount(pack: BuddyPack) {
  return pack.assets.filter((asset) => asset.content_type.startsWith('video/'))
    .length;
}

function displayPackName(name: string) {
  return name.replace(/^Buddy\s+/i, '').trim() || name;
}

export default function BuddyControls(props: BuddyControlsProps) {
  const localEditor = useRef<ProviderSettingsSession | null>(null);
  if (!localEditor.current)
    localEditor.current = new ProviderSettingsSession('buddy');
  const editor = props.editor ?? localEditor.current;

  const [draft, setDraft] = useProviderSettingsValue<BuddyPreferences | null>(
    editor,
    'draft',
    null,
  );
  const [baseRevision, setBaseRevision] = useProviderSettingsValue(
    editor,
    'baseRevision',
    '',
  );
  const [dirty, setDirty] = useProviderSettingsValue(editor, 'dirty', false);
  const dirtyRef = useRef(editor.get('dirty', false));
  dirtyRef.current = dirty;
  const [busy, setBusy] = useProviderSettingsValue(editor, 'busy', false);
  const [notice, setNotice] = useProviderSettingsValue(editor, 'notice', '');
  const [error, setError] = useProviderSettingsValue(editor, 'error', '');
  const [page, setPage] = useProviderSettingsValue<BuddyPackPage | null>(
    editor,
    'page',
    null,
  );
  const notify = useNotify();
  const [saving, setSaving] = useProviderSettingsValue(editor, 'saving', false);
  // One change saves after another, so quick edits never overlap.
  const chain = useRef<Promise<void>>(Promise.resolve());
  const operation = useRef<symbol | null>(null);
  const current = useRef(props);
  useEffect(() => {
    current.current = props;
  }, [props]);
  useEffect(() => {
    if (props.editor) return;
    setDraft(null);
    setBaseRevision('');
    setDirty(false);
    dirtyRef.current = false;
    setError('');
    setNotice('');
    setPage(null);
  }, [
    props.scopeKey,
    props.editor,
    setDraft,
    setBaseRevision,
    setDirty,
    setError,
    setNotice,
    setPage,
  ]);
  useEffect(() => {
    if (!dirtyRef.current && props.snapshot) {
      setDraft(props.snapshot.preferences);
      setBaseRevision(props.snapshot.revision);
    }
  }, [props.snapshot, setDraft, setBaseRevision]);
  useEffect(() => {
    setPage(props.packs);
  }, [props.packs, setPage]);

  function edit<K extends keyof BuddyPreferences>(
    key: K,
    value: BuddyPreferences[K],
  ) {
    setDraft((previous) =>
      previous ? { ...previous, [key]: value } : previous,
    );
    setNotice('');
    if (TEXT_KEYS.has(key)) {
      setDirty(true);
      dirtyRef.current = true;
      return;
    }
    void commit({ [key]: value } as Partial<BuddyPreferences>);
  }
  function commitText(key: keyof BuddyPreferences) {
    const value = editor.get<BuddyPreferences | null>('draft', null)?.[key];
    const saved = current.current.snapshot?.preferences;
    if (value === undefined || !saved) return;
    if (value === saved[key]) {
      const draft = editor.get<BuddyPreferences | null>('draft', null);
      const pending =
        draft && [...TEXT_KEYS].some((name) => draft[name] !== saved[name]);
      setDirty(Boolean(pending));
      dirtyRef.current = Boolean(pending);
      return;
    }
    void commit({ [key]: value } as Partial<BuddyPreferences>);
  }
  function commit(changes: Partial<BuddyPreferences>, undoing = false) {
    const scope = current.current.scopeKey;
    const task = async () => {
      const before = current.current.snapshot;
      if (current.current.scopeKey !== scope || !before || !editor.active)
        return;
      const previous = Object.fromEntries(
        Object.keys(changes).map((key) => [
          key,
          before.preferences[key as keyof BuddyPreferences],
        ]),
      ) as Partial<BuddyPreferences>;
      setSaving(true);
      setError('');
      try {
        const saved = await current.current.save(
          changes,
          editor.get('baseRevision', ''),
        );
        if (current.current.scopeKey !== scope || !current.current.snapshot)
          return;
        setBaseRevision(saved.revision);
        // Keep text still being typed in another field.
        const typed = editor.get<BuddyPreferences | null>('draft', null);
        const next = { ...saved.preferences };
        let pending = false;
        for (const key of TEXT_KEYS)
          if (
            typed &&
            !(key in changes) &&
            typed[key] !== before.preferences[key]
          ) {
            Object.assign(next, { [key]: typed[key] });
            pending = true;
          }
        setDraft(next);
        setDirty(pending);
        dirtyRef.current = pending;
        const name =
          preferenceNames[Object.keys(changes)[0] as keyof BuddyPreferences] ??
          'Buddy';
        // Saves are confirmed by the floating notice, with Undo (B258).
        if (undoing) notify(`${name} changed back`);
        else
          notify(`${name} saved`, undefined, {
            label: 'Undo',
            onAction: () => void commit(previous, true),
          });
      } catch {
        if (current.current.scopeKey === scope && current.current.snapshot)
          setError(
            "Buddy couldn't save that change. Reload the saved preferences, then try again.",
          );
      } finally {
        if (current.current.scopeKey === scope) setSaving(false);
      }
    };
    chain.current = chain.current.then(task, task);
    return chain.current;
  }
  async function run(action: 'next' | 'stop' | 'reload') {
    if (
      operation.current ||
      editor.get('busy', false) ||
      !editor.active ||
      !props.snapshot
    )
      return;
    const identity = Symbol('buddy');
    operation.current = identity;
    setBusy(true);
    setError('');
    setNotice('');
    const scope = props.scopeKey;
    try {
      if (action === 'next' && page?.next_cursor) {
        const next = await props.loadPacks(page.next_cursor);
        if (current.current.scopeKey !== scope || !current.current.snapshot)
          return;
        if (next.revision !== page.revision)
          throw new Error('buddy_cursor_changed');
        setPage(next);
      } else if (action === 'reload') {
        await props.reload();
        if (current.current.scopeKey === scope && current.current.snapshot)
          notify('Buddy looks refreshed.');
      } else if (action === 'stop' && props.currentRunId) {
        await props.stop(props.currentRunId);
        if (current.current.scopeKey === scope && current.current.snapshot)
          setNotice('Stop requested.');
      }
    } catch {
      if (current.current.scopeKey === scope && current.current.snapshot)
        setError(
          'Buddy could not complete this action. Review current preferences and any retained recovery before retrying.',
        );
    } finally {
      if (operation.current === identity) {
        operation.current = null;
        setBusy(false);
      }
    }
  }

  if (!props.snapshot) return null;
  const snapshot = props.snapshot;
  const conflict = dirty && baseRevision !== snapshot.revision;
  const selectedPack =
    page?.packs.find((pack) => pack.id === draft?.pack_id) ?? null;
  return (
    <>
      {snapshot.preferences.visible && props.companionVisible !== false && (
        <aside
          className="buddy-companion"
          aria-label="Buddy companion"
          aria-busy={busy}
        >
          {/* The avatar's tooltip carries Buddy's name (B225). */}
          <Hint label={snapshot.preferences.display_name || 'Buddy'}>
            <Button
              aria-label="Buddy settings"
              variant="ghost"
              onClick={props.onSettings}
            >
              {props.renderAvatar?.(snapshot) ?? (
                <span aria-hidden="true">✦</span>
              )}
            </Button>
          </Hint>
          <span className="buddy-companion-text">
            <span className="buddy-companion-name">
              {snapshot.preferences.display_name || 'Buddy'}
            </span>
            {snapshot.preferences.bubble_verbosity !== 'quiet' &&
              !snapshot.preferences.collapsed && (
                <p role="status">{snapshot.status.label}</p>
              )}
          </span>
          {props.onUndock && (
            <IconButton
              size="sm"
              className="buddy-undock"
              label="Undock Buddy"
              onClick={props.onUndock}
            >
              <PictureInPicture2 size={15} aria-hidden />
            </IconButton>
          )}
          {props.currentRunId && (
            <Button disabled={busy} onClick={() => void run('stop')}>
              Stop current run
            </Button>
          )}
        </aside>
      )}
      {props.settingsOpen && draft && (
        <section
          className="buddy-preferences settings-buddy-preferences"
          aria-label="Buddy preferences"
          aria-busy={busy}
        >
          <SettingsStatus
            tone={draft.visible ? 'success' : 'neutral'}
            more={[
              selectedPack ? displayPackName(selectedPack.name) : draft.pack_id,
              selectedPack?.available ? 'motion ready' : 'motion unavailable',
            ]}
          >
            {draft.visible ? 'Shown' : 'Hidden'}
          </SettingsStatus>
          <SettingsGroup label="Visibility" anchor="buddy-visibility">
            <SettingsItem
              label="Show Buddy"
              help="In the sidebar and as the desktop companion."
              layout="inline"
              status={
                snapshot.native_placement_retained ? (
                  <StatusLine>
                    Your saved desktop placement is retained for the native app.
                  </StatusLine>
                ) : undefined
              }
              control={
                <Toggle
                  label="Show Buddy"
                  checked={draft.visible}
                  disabled={busy}
                  onChange={(e) => edit('visible', e.target.checked)}
                />
              }
            />
          </SettingsGroup>
          <SettingsGroup
            title="Look"
            anchor="buddy-look"
            meta={
              <>
                {page && (
                  <span>
                    {page.total} look{page.total === 1 ? '' : 's'}
                  </span>
                )}
                <IconButton
                  size="sm"
                  label="Refresh looks"
                  disabled={busy}
                  onClick={() => void run('reload')}
                >
                  <RefreshCw size={14} aria-hidden />
                </IconButton>
              </>
            }
          >
            <div
              className="buddy-look-list"
              role="group"
              aria-label="Buddy looks"
            >
              {page?.packs.map((pack) => {
                const chosen = draft.pack_id === pack.id;
                return (
                  <Button
                    key={pack.id}
                    className="buddy-look"
                    aria-label={`${displayPackName(pack.name)} — ${videoCount(pack)} clips · ${pack.available ? 'Ready' : 'Unavailable'}`}
                    aria-pressed={chosen}
                    disabled={busy || !pack.available}
                    onClick={() => edit('pack_id', pack.id)}
                  >
                    {chosen && (
                      <span className="buddy-look-check" aria-hidden>
                        <Check size={12} strokeWidth={3} aria-hidden />
                      </span>
                    )}
                    {props.renderPackPreview?.(pack)}
                    {props.previewUrl?.(pack) && (
                      <img
                        alt=""
                        width={56}
                        height={56}
                        src={props.previewUrl(pack)!}
                      />
                    )}
                    <span className="settings-buddy-pack-name">
                      {displayPackName(pack.name)}
                    </span>
                    <small className="settings-buddy-pack-meta">
                      {pack.available ? 'Ready' : 'Unavailable'}
                    </small>
                  </Button>
                );
              })}
              {props.onNewLook && (
                <Button
                  className="buddy-look buddy-look-new"
                  disabled={busy}
                  onClick={props.onNewLook}
                >
                  <span className="buddy-look-new-icon" aria-hidden>
                    <Sparkles size={18} aria-hidden />
                  </span>
                  <span className="settings-buddy-pack-name">New look…</span>
                  <small className="settings-buddy-pack-meta">
                    {props.newLookStatus ?? 'Made by your image model'}
                  </small>
                </Button>
              )}
            </div>
            {page?.next_cursor && (
              <div className="settings-divided settings-buddy-more">
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void run('next')}
                >
                  More Buddy looks
                </Button>
              </div>
            )}
          </SettingsGroup>
          <SettingsGroup title="Personality">
            <SettingsItem
              label="Personality"
              help="How Buddy’s little status bubbles sound."
              control={
                <Select
                  value={draft.personality}
                  disabled={busy}
                  onChange={(e) => edit('personality', e.target.value)}
                >
                  {Object.entries(personalities).map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
                </Select>
              }
            />
            <SettingsItem
              label="Bubbles"
              help="Quiet hides them; Chatty says more."
              bind={false}
              control={
                <Segmented
                  label="Bubbles"
                  value={draft.bubble_verbosity}
                  onChange={(value) => edit('bubble_verbosity', value)}
                  options={[
                    { value: 'quiet', label: 'Quiet', disabled: busy },
                    { value: 'normal', label: 'Normal', disabled: busy },
                    { value: 'chatty', label: 'Chatty', disabled: busy },
                  ]}
                />
              }
            />
            <SettingsItem
              label="Motion"
              help="Stays still when your system reduces motion."
              bind={false}
              control={
                <Segmented
                  label="Motion"
                  value={draft.animation_intensity}
                  onChange={(value) => edit('animation_intensity', value)}
                  options={[
                    { value: 'quiet', label: 'Calm', disabled: busy },
                    { value: 'normal', label: 'Normal', disabled: busy },
                    { value: 'expressive', label: 'Lively', disabled: busy },
                  ]}
                />
              }
            />
          </SettingsGroup>
          <SettingsAdvanced
            meta={
              snapshot.native_placement_retained
                ? 'Name, style notes, compact size'
                : 'Name, style notes'
            }
          >
            <SettingsGroup label="Advanced Buddy settings">
              <SettingsItem
                label="Name"
                help="Shown as Buddy’s tooltip and in its bubbles."
                control={
                  <Input
                    aria-label="Buddy name"
                    value={draft.display_name}
                    maxLength={128}
                    disabled={busy}
                    onChange={(event) =>
                      edit('display_name', event.target.value)
                    }
                    onBlur={() => commitText('display_name')}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') event.currentTarget.blur();
                    }}
                  />
                }
              />
              <SettingsItem
                label="Style notes"
                help="Optional; they shape how Buddy phrases its bubbles."
                layout="stacked"
                control={
                  <textarea
                    className="input"
                    aria-label="Style notes (optional)"
                    value={draft.personality_description}
                    maxLength={200}
                    disabled={busy}
                    onChange={(event) =>
                      edit('personality_description', event.target.value)
                    }
                    onBlur={() => commitText('personality_description')}
                  />
                }
              />
              {/* Compact is a desktop-window state: a docked Buddy's saved
                  "collapsed" is always normalized off, so only offer it
                  where it takes effect. */}
              {snapshot.native_placement_retained && (
                <SettingsItem
                  label="Compact size"
                  help="Shrink the desktop Buddy window to its avatar."
                  layout="inline"
                  control={
                    <Toggle
                      label="Compact Buddy"
                      checked={draft.collapsed}
                      disabled={busy}
                      onChange={(event) =>
                        edit('collapsed', event.target.checked)
                      }
                    />
                  }
                />
              )}
            </SettingsGroup>
          </SettingsAdvanced>
          {(conflict || error) && (
            <div className="button-row buddy-preference-actions">
              {conflict && (
                <p role="status">
                  Buddy's saved preferences changed elsewhere. What you typed is
                  kept until you reload them.
                </p>
              )}
              <Button
                disabled={busy || saving}
                onClick={() => {
                  setDraft(snapshot.preferences);
                  setBaseRevision(snapshot.revision);
                  setDirty(false);
                  dirtyRef.current = false;
                  setError('');
                }}
              >
                Reload saved preferences
              </Button>
            </div>
          )}
        </section>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && <ErrorState title="Buddy needs attention">{error}</ErrorState>}
    </>
  );
}
