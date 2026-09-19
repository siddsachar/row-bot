import {
  ProviderSettingsSession,
  useProviderSettingsValue,
} from '../settings/provider-settings-sessions';
import { useEffect, useRef, type ReactNode } from 'react';
import { Button, ErrorState, Field, Input, Select } from '../../ui/primitives';

export type BuddyPreferences = {
  visible: boolean;
  collapsed: boolean;
  display_name: string;
  personality: string;
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
  save(
    changes: Partial<BuddyPreferences>,
    revision: string,
  ): Promise<BuddySnapshot>;
  loadPacks(cursor: string): Promise<BuddyPackPage>;
  stop(runId: string): Promise<void>;
};

const personalities = {
  warm_mystical: 'Warm mystical',
  calm_focus: 'Calm focus',
  playful_helper: 'Playful helper',
  quiet_guardian: 'Quiet guardian',
  curious_scholar: 'Curious scholar',
};

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
    setDirty(true);
    dirtyRef.current = true;
    setNotice('');
  }
  async function run(action: 'save' | 'next' | 'stop') {
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
      if (action === 'save' && draft) {
        const changes: Partial<BuddyPreferences> = {};
        for (const key of Object.keys(draft) as (keyof BuddyPreferences)[]) {
          if (draft[key] !== props.snapshot.preferences[key])
            Object.assign(changes, { [key]: draft[key] });
        }
        if (!Object.keys(changes).length) {
          setDirty(false);
          dirtyRef.current = false;
          return;
        }
        const saved = await props.save(changes, baseRevision);
        if (current.current.scopeKey !== scope || !current.current.snapshot)
          return;
        setDraft(saved.preferences);
        setBaseRevision(saved.revision);
        setDirty(false);
        dirtyRef.current = false;
        setNotice('Buddy preferences saved.');
      } else if (action === 'next' && page?.next_cursor) {
        const next = await props.loadPacks(page.next_cursor);
        if (current.current.scopeKey !== scope || !current.current.snapshot)
          return;
        if (next.revision !== page.revision)
          throw new Error('buddy_cursor_changed');
        setPage(next);
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
          <Button
            aria-label="Buddy settings"
            variant="ghost"
            onClick={props.onSettings}
          >
            {props.renderAvatar?.(snapshot) ?? (
              <span aria-hidden="true">✦</span>
            )}
          </Button>
          {snapshot.preferences.bubble_verbosity !== 'quiet' &&
            !snapshot.preferences.collapsed && (
              <p role="status">{snapshot.status.label}</p>
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
          <div className="settings-buddy-intro">
            <p>Companion behavior, look, and generated motion.</p>
            <div
              className="settings-summary-strip"
              role="group"
              aria-label="Buddy status"
            >
              <span className="status-chip">
                {draft.visible ? 'Enabled' : 'Hidden'}
              </span>
              <span className="status-chip">
                {selectedPack?.available
                  ? 'Motion pack ready'
                  : 'Motion pack unavailable'}
              </span>
            </div>
          </div>
          <section
            className="settings-buddy-section"
            aria-labelledby="settings-buddy-visibility"
          >
            <div className="settings-buddy-section-heading">
              <div>
                <h3 id="settings-buddy-visibility">Visibility</h3>
                <p>Buddy is docked here or hidden from the workspace.</p>
              </div>
            </div>
            <div className="settings-buddy-visibility-grid">
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  aria-label="Show Buddy"
                  checked={draft.visible}
                  disabled={busy}
                  onChange={(e) => edit('visible', e.target.checked)}
                />
                <span>
                  <strong>Show Buddy</strong>
                  <small>Show the companion in this client.</small>
                </span>
              </label>
            </div>
            {snapshot.native_placement_retained && (
              <p className="settings-help">
                Your saved desktop placement is retained for the native app.
              </p>
            )}
          </section>
          <section
            className="settings-buddy-section"
            aria-labelledby="settings-buddy-behavior"
          >
            <div className="settings-buddy-section-heading">
              <div>
                <h3 id="settings-buddy-behavior">Behavior</h3>
                <p>Bubble tone and runtime personality for status text.</p>
              </div>
              <span className="status-chip">Runtime</span>
            </div>
            <div className="settings-buddy-behavior-grid">
              <Field label="Companion personality">
                <Select
                  aria-label="Companion personality"
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
              </Field>
              <Field label="Bubble style">
                <Select
                  aria-label="Bubble style"
                  value={draft.bubble_verbosity}
                  disabled={busy}
                  onChange={(e) =>
                    edit(
                      'bubble_verbosity',
                      e.target.value as BuddyPreferences['bubble_verbosity'],
                    )
                  }
                >
                  <option value="quiet">Quiet</option>
                  <option value="normal">Normal</option>
                  <option value="chatty">Chatty</option>
                </Select>
              </Field>
            </div>
            <p className="settings-help">
              Quiet hides bubbles, Normal mirrors current state, and Chatty
              rewrites short status labels in the selected personality.
            </p>
          </section>
          <details className="settings-buddy-advanced">
            <summary>
              <span>
                <strong>Advanced companion</strong>
                <small>Name, compact display, and motion intensity.</small>
              </span>
            </summary>
            <div className="settings-buddy-advanced-content">
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  aria-label="Compact Buddy"
                  checked={draft.collapsed}
                  disabled={busy}
                  onChange={(event) => edit('collapsed', event.target.checked)}
                />
                <span>
                  <strong>Compact Buddy</strong>
                  <small>Hide the status bubble while Buddy is docked.</small>
                </span>
              </label>
              <Field label="Buddy name">
                <Input
                  aria-label="Buddy name"
                  value={draft.display_name}
                  maxLength={128}
                  disabled={busy}
                  onChange={(event) => edit('display_name', event.target.value)}
                />
              </Field>
              <Field label="Animation intensity">
                <Select
                  aria-label="Animation intensity"
                  value={draft.animation_intensity}
                  disabled={busy}
                  onChange={(event) =>
                    edit(
                      'animation_intensity',
                      event.target
                        .value as BuddyPreferences['animation_intensity'],
                    )
                  }
                >
                  <option value="quiet">Quiet</option>
                  <option value="normal">Normal</option>
                  <option value="expressive">Expressive</option>
                </Select>
              </Field>
            </div>
          </details>
          <section
            className="settings-buddy-section"
            aria-labelledby="settings-buddy-look"
          >
            <div className="settings-buddy-section-heading">
              <div>
                <h3 id="settings-buddy-look">Look &amp; Motion</h3>
                <p>Select the active pack used everywhere.</p>
              </div>
              {selectedPack && (
                <span className="status-chip">
                  {videoCount(selectedPack)} clip
                  {videoCount(selectedPack) === 1 ? '' : 's'}
                </span>
              )}
            </div>
            <p className="settings-buddy-selection">
              Selected:{' '}
              {selectedPack
                ? displayPackName(selectedPack.name)
                : draft.pack_id}
              . Motion pack {selectedPack?.available ? 'ready' : 'unavailable'}.
            </p>
            <div
              className="buddy-look-list"
              role="group"
              aria-label="Buddy looks"
            >
              {page?.packs.map((pack) => (
                <Button
                  key={pack.id}
                  className="buddy-look"
                  aria-label={`${displayPackName(pack.name)} — ${videoCount(pack)} clips · ${pack.available ? 'Ready' : 'Unavailable'}`}
                  aria-pressed={draft.pack_id === pack.id}
                  disabled={busy || !pack.available}
                  onClick={() => edit('pack_id', pack.id)}
                >
                  {props.renderPackPreview?.(pack)}
                  {props.previewUrl?.(pack) && (
                    <img
                      alt=""
                      width={72}
                      height={72}
                      src={props.previewUrl(pack)!}
                    />
                  )}
                  <span className="settings-buddy-pack-name">
                    {displayPackName(pack.name)}
                  </span>
                  <small className="settings-buddy-pack-meta">
                    {videoCount(pack)} clips ·{' '}
                    {pack.available ? 'Ready' : 'Unavailable'}
                  </small>
                </Button>
              ))}
            </div>
            {page && (
              <p className="settings-help">
                {page.packs.length} of {page.total} looks on this page.
              </p>
            )}
            {page?.next_cursor && (
              <Button disabled={busy} onClick={() => void run('next')}>
                More Buddy looks
              </Button>
            )}
          </section>
          {conflict && (
            <p role="status">
              Saved preferences changed. Your draft is retained; reload before
              saving.
            </p>
          )}
          <div className="button-row buddy-preference-actions">
            <Button
              disabled={busy || !dirty || conflict}
              onClick={() => void run('save')}
            >
              Save Buddy preferences
            </Button>
            <Button
              disabled={busy}
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
        </section>
      )}
      {notice &&
        (props.settingsOpen || notice !== 'Buddy preferences saved.') && (
          <p role="status">{notice}</p>
        )}
      {error && <ErrorState title="Buddy needs attention">{error}</ErrorState>}
    </>
  );
}
