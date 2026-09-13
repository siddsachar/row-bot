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
  return (
    <>
      {snapshot.preferences.visible && props.companionVisible !== false && (
        <aside aria-label="Buddy companion" aria-busy={busy}>
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
          aria-label="Buddy preferences"
          aria-busy={busy}
          style={{ display: 'grid', gap: 12, padding: 12 }}
        >
          <p>
            Companion behavior, look, and generated motion. Buddy stays docked
            in this client.
          </p>
          {snapshot.native_placement_retained && (
            <p>Your saved desktop placement is retained for the native app.</p>
          )}
          <label>
            <input
              type="checkbox"
              checked={draft.visible}
              disabled={busy}
              onChange={(e) => edit('visible', e.target.checked)}
            />{' '}
            Show Buddy
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.collapsed}
              disabled={busy}
              onChange={(e) => edit('collapsed', e.target.checked)}
            />{' '}
            Compact Buddy
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
                  event.target.value as BuddyPreferences['animation_intensity'],
                )
              }
            >
              <option value="quiet">Quiet</option>
              <option value="normal">Normal</option>
              <option value="expressive">Expressive</option>
            </Select>
          </Field>
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
          <div
            role="group"
            aria-label="Buddy looks"
            style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}
          >
            {page?.packs.map((pack) => (
              <Button
                key={pack.id}
                className="buddy-look"
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
                {pack.name}
                {!pack.available && ' — Unavailable'}
              </Button>
            ))}
          </div>
          {page && (
            <p>
              {page.packs.length} of {page.total} looks on this page. Selected:{' '}
              {draft.pack_id}.
            </p>
          )}
          {page?.next_cursor && (
            <Button disabled={busy} onClick={() => void run('next')}>
              More Buddy looks
            </Button>
          )}
          {conflict && (
            <p role="status">
              Saved preferences changed. Your draft is retained; reload before
              saving.
            </p>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
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
