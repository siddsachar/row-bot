import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  DesignFormSession,
  type DesignFormState,
} from './artifact-design-sessions';
import { MoreHorizontal } from 'lucide-react';
import ArtifactFontPicker from './ArtifactFontPicker';
import ArtifactLogoPicker from './ArtifactLogoPicker';
import { useDesignCatalog } from './artifact-design-catalog';
import { humanizeToken } from '../../ui/format';
import { clientError } from '../../api/errors';
import type { ArtifactBrandSuggestion } from '../../api/types';
import {
  Button,
  Disclosure,
  ErrorState,
  Field,
  Input,
  Menu,
  Segmented,
  Select,
  StatusDot,
} from '../../ui/primitives';

export type DesignBrand = {
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  bg_color: string;
  text_color: string;
  heading_font: string;
  body_font: string;
  logo_asset_id: string;
  logo_mode: string;
  logo_scope: string;
  logo_position: string;
  logo_max_height: number;
  logo_padding: number;
};
export type DesignControlItem = {
  id: string;
  label: string;
  kind: string;
  detail: string;
  available: boolean;
};
export type DesignSection =
  'elements' | 'assets' | 'fonts' | 'presets' | 'interactions' | 'blocks';
export type DesignControlsState = {
  resource_id: string;
  resource_revision: string;
  mode: string;
  page_id: string;
  brand: DesignBrand;
  element: {
    id: string;
    tag: string;
    styles: Record<string, string>;
    action: string;
  } | null;
  section: DesignSection;
  items: DesignControlItem[];
  item_count: number;
  next_cursor: string | null;
};
export type DesignReviewFinding = {
  id: string;
  source: string;
  category: string;
  severity: string;
  message: string;
  suggested_fix: string;
  page_id: string;
  auto_fixable: boolean;
};
export type DesignReviewState = {
  resource_id: string;
  resource_revision: string;
  page_id: string;
  scope: 'page' | 'project';
  heuristic: boolean;
  score: number;
  findings: DesignReviewFinding[];
  finding_count: number;
  next_cursor: string | null;
};
export type DesignControlsView = 'properties' | 'library' | 'review';
export type DesignControlsProps = {
  /** Which inspector section this instance shows (all three by default). */
  view?: DesignControlsView;
  resourceId: string;
  session?: DesignFormSession;
  blocked?: boolean;
  resourceRevision: string;
  pageId: string;
  selectedElementId?: string;
  visible: boolean;
  load: (options: {
    page_id: string;
    element_id?: string;
    section: DesignSection;
    cursor?: string;
    limit?: number;
  }) => Promise<DesignControlsState>;
  /** A small picture of one of the design's images. */
  thumbnail: (assetId: string) => Promise<Blob>;
  review: (options: {
    page_id: string;
    scope: 'page' | 'project';
    cursor?: string;
  }) => Promise<DesignReviewState>;
  apply: (
    operation:
      | 'brand'
      | 'preset'
      | 'style'
      | 'hotspot'
      | 'review_fix'
      | 'review_fix_all'
      | 'asset_insert'
      | 'asset_remove'
      | 'asset_forget'
      | 'block_insert',
    payload: Record<string, unknown>,
    expectedRevision: string,
    pageId: string,
    elementId?: string,
  ) => Promise<{ resource_revision: string }>;
  upload: (
    file: File,
    expectedRevision: string,
  ) => Promise<{ resource_revision: string; asset_id?: string }>;
  onSelectElement: (elementId: string) => void;
  /** The selected element is no longer in the saved design. */
  onSelectionLost: (elementId: string) => void;
  /** Read the design shown again (its current revision). */
  onReload: () => void;
  /** Brand › From a website (a guarded read of a public page). */
  suggestBrand?: (url: string) => Promise<ArtifactBrandSuggestion>;
  draftFix?: (
    findingId: string,
    pageId: string,
    expectedRevision: string,
  ) => Promise<string>;
  onDraftText?: (text: string) => void;
  mutatePreset?: (
    options: { action: 'save' | 'delete'; name: string; preset_id?: string },
    expectedRevision: string,
  ) => Promise<{
    resource_id: string;
    resource_revision: string;
    preset_id: string;
    name: string;
    action: 'save' | 'delete';
  }>;
};

const colors = [
  ['primary_color', 'Primary colour'],
  ['secondary_color', 'Secondary colour'],
  ['accent_color', 'Accent colour'],
  ['bg_color', 'Background colour'],
  ['text_color', 'Text colour'],
] as const;
const styleFields = [
  ['color', 'Text colour'],
  ['background-color', 'Fill'],
  ['font-size', 'Font size'],
  ['font-weight', 'Weight'],
  ['line-height', 'Line height'],
  ['padding', 'Padding'],
  ['margin', 'Margin'],
  ['gap', 'Gap'],
  ['border-radius', 'Radius'],
  ['width', 'Width'],
  ['height', 'Height'],
] as const;
const sectionWords: Record<DesignSection, string> = {
  elements: 'Elements',
  assets: 'Assets',
  fonts: 'Fonts',
  presets: 'Presets',
  interactions: 'Interactions',
  blocks: 'Blocks',
};
const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;
/** Brand colours apply this long after the last change (auto-save). */
const BRAND_DELAY = 700;
/** A failure in words: a server error carries a code, a session guard names it. */
function failure(reason: unknown) {
  return clientError(
    reason instanceof Error && /^[a-z][a-z0-9_]{0,79}$/.test(reason.message)
      ? { code: reason.message }
      : reason,
  );
}
/** A native colour picker needs #rrggbb. */
function pickerValue(value: string) {
  if (/^#[0-9a-f]{6}$/i.test(value)) return value;
  if (/^#[0-9a-f]{3}$/i.test(value))
    return `#${[...value.slice(1)].map((c) => c + c).join('')}`;
  return '#000000';
}
export default function ArtifactDesignControls(props: DesignControlsProps) {
  const {
    resourceId,
    resourceRevision,
    pageId,
    selectedElementId,
    visible,
    load,
  } = props;
  const [local] = useState(() => new DesignFormSession());
  const session = props.session ?? local;
  const retained = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const {
    section,
    state,
    brand,
    styles,
    review,
    scope,
    action,
    target,
    error,
    notice,
    loading,
    saving,
    file,
    presetName,
    presetReview,
    reload,
  } = retained;
  const sourceKey = JSON.stringify([
    resourceId,
    resourceRevision,
    pageId,
    selectedElementId ?? '',
  ]);
  const staleDraft = Boolean(
    retained.dirtySource && retained.dirtySource !== sourceKey,
  );
  const setter =
    <K extends keyof DesignFormState>(key: K, dirty = false) =>
    (
      value:
        | DesignFormState[K]
        | ((previous: DesignFormState[K]) => DesignFormState[K]),
    ) => {
      if (dirty) session.markDirty(key, sourceKey);
      session.set(key, value);
    };
  const setSection = setter('section');
  const setState = setter('state');
  const setBrand = setter('brand', true);
  const setStyles = setter('styles', true);
  const setReview = setter('review');
  const setScope = setter('scope');
  const setAction = setter('action', true);
  const setTarget = setter('target', true);
  const setError = setter('error');
  const setNotice = setter('notice');
  const setSaving = setter('saving');
  const setFile = setter('file', true);
  const setPresetName = setter('presetName', true);
  const setPresetReview = setter('presetReview');
  const setReload = setter('reload');
  useEffect(() => () => local.dispose(), [local]);
  const current = useRef(props);
  const operation = session.operation;
  useEffect(() => {
    current.current = props;
  }, [props]);
  useEffect(() => {
    let active = true;
    session.set('state', null);
    session.set('review', null);
    session.set('error', '');
    if (!visible) return;
    session.set('loading', true);
    void load({
      page_id: pageId,
      element_id: selectedElementId,
      section,
    })
      .then((value) => {
        if (!active) return;
        if (value.resource_id !== resourceId || value.page_id !== pageId)
          throw new Error('resource_changed');
        if (value.resource_revision !== resourceRevision) {
          // The design moved on (usually by one's own edit) before the
          // canvas did: read it again, and these controls follow.
          current.current.onReload();
          return;
        }
        if (value.items.length > 50)
          throw new Error('design_catalog_too_large');
        session.set('state', value);
        session.set('controlPage', false);
        if (!session.getSnapshot().dirtySource) {
          session.set('brand', value.brand);
          session.set('styles', value.element?.styles ?? {});
        }
      })
      .catch((reason: unknown) => {
        if (!active) return;
        const problem = failure(reason);
        // The selected element is gone: the page is read without it.
        if (selectedElementId && problem.code === 'element_unavailable')
          current.current.onSelectionLost(selectedElementId);
        else session.set('error', problem.message);
      })
      .finally(() => {
        if (active) session.set('loading', false);
      });
    return () => {
      active = false;
    };
  }, [
    resourceId,
    resourceRevision,
    pageId,
    selectedElementId,
    visible,
    load,
    section,
    reload,
    session,
  ]);
  useEffect(() => {
    session.set('notice', '');
  }, [session, props.resourceId, props.pageId, props.selectedElementId]);
  useEffect(() => {
    if (!session.getSnapshot().dirtySource) {
      session.set('file', null);
      session.set('presetReview', null);
      session.set('presetName', '');
    }
  }, [session, props.resourceId]);
  useEffect(() => {
    session.set('presetReview', null);
  }, [session, props.resourceRevision]);

  async function changePreset(change = presetReview) {
    if (
      !change ||
      !props.mutatePreset ||
      operation.current ||
      !state ||
      !props.visible
    )
      return;
    const token = Symbol('global-preset');
    operation.current = token;
    setSaving(true);
    setError('');
    const sourceId = props.resourceId;
    const sourceRevision = state.resource_revision;
    try {
      const result = await props.mutatePreset(change, sourceRevision);
      if (
        current.current.resourceId !== sourceId ||
        current.current.resourceRevision !== sourceRevision
      )
        return;
      if (
        result.resource_id !== sourceId ||
        result.resource_revision !== sourceRevision
      )
        throw new Error('Resource changed');
      setPresetReview(null);
      session.committed('preset_' + change.action);
      setNotice(
        result.action === 'delete'
          ? 'Global preset removed. Previous bytes are retained for recovery.'
          : 'Global preset saved.',
      );
      setReload((value) => value + 1);
    } catch {
      if (
        current.current.resourceId === sourceId &&
        current.current.resourceRevision === sourceRevision
      )
        setError(
          'Global preset change was not confirmed. Previous bytes are retained. Review recovery before retrying.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }

  // An uploaded logo becomes the logo once the version with it is loaded.
  const pendingLogo = useRef<string | null>(null);
  async function upload(chosen: File | null = file, logo = false) {
    if (!chosen || !state || operation.current || !props.visible) return;
    if (chosen.size === 0 || chosen.size > 25 * 1024 * 1024) {
      setError('Choose a nonempty asset up to 25 MiB.');
      return;
    }
    const token = Symbol('asset-upload');
    operation.current = token;
    setSaving(true);
    setError('');
    const sourceId = props.resourceId;
    try {
      const result = await props.upload(chosen, state.resource_revision);
      if (current.current.resourceId !== sourceId) return;
      if (logo) pendingLogo.current = result.asset_id ?? null;
      else {
        session.set('file', null);
        session.committed('asset_upload');
      }
      setNotice(logo ? 'Logo added.' : 'Asset added.');
      if (result.resource_revision === current.current.resourceRevision)
        setReload((value) => value + 1);
      else setState(null);
    } catch {
      if (current.current.resourceId === sourceId)
        setError(
          'Asset attachment was not confirmed. Uploaded files are retained for recovery; reload before retrying.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }

  async function apply(
    kind: Parameters<DesignControlsProps['apply']>[0],
    payload: Record<string, unknown>,
    pageId = props.pageId,
  ) {
    if (operation.current || !state || !props.visible) return;
    const token = Symbol('design-control');
    operation.current = token;
    setSaving(true);
    setError('');
    setNotice('');
    const sourceId = props.resourceId;
    try {
      const result = await props.apply(
        kind,
        payload,
        state.resource_revision,
        pageId,
        props.selectedElementId,
      );
      if (
        current.current.resourceId === sourceId &&
        current.current.pageId === props.pageId &&
        current.current.selectedElementId === props.selectedElementId
      ) {
        session.committed(kind);
        setNotice('Changes saved.');
        setReview(null);
        if (result.resource_revision === current.current.resourceRevision)
          setReload((value) => value + 1);
        else setState(null);
      }
    } catch {
      if (
        current.current.resourceId === sourceId &&
        current.current.pageId === props.pageId &&
        current.current.selectedElementId === props.selectedElementId
      )
        setError(
          'Changes were not confirmed. Reload the current design before retrying.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }
  async function scan(cursor?: string) {
    if (operation.current || !state) return;
    const token = Symbol('design-review');
    operation.current = token;
    setSaving(true);
    setError('');
    const source = state;
    try {
      const value = await props.review({
        page_id: source.page_id,
        scope,
        cursor,
      });
      if (
        current.current.resourceId !== source.resource_id ||
        current.current.resourceRevision !== source.resource_revision ||
        current.current.pageId !== source.page_id
      )
        return;
      if (
        value.resource_id !== source.resource_id ||
        value.resource_revision !== source.resource_revision
      )
        throw new Error('Resource changed');
      if (value.findings.length > 50)
        throw new Error('Review page exceeded its bound');
      setReview(value);
      session.set('reviewPage', Boolean(cursor));
    } catch {
      if (
        current.current.resourceId === source.resource_id &&
        current.current.resourceRevision === source.resource_revision &&
        current.current.pageId === source.page_id
      ) {
        setReview(null);
        setError(
          'Review is unavailable. No clean result has been established.',
        );
      }
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }
  async function draft(finding: DesignReviewFinding) {
    if (!props.draftFix || !props.onDraftText || !state || operation.current)
      return;
    const source = state;
    const token = Symbol('review-draft');
    operation.current = token;
    setSaving(true);
    setError('');
    try {
      const text = await props.draftFix(
        finding.id,
        finding.page_id,
        source.resource_revision,
      );
      if (
        current.current.resourceId === source.resource_id &&
        current.current.resourceRevision === source.resource_revision &&
        current.current.pageId === source.page_id
      ) {
        props.onDraftText(text);
        setNotice('AI fix drafted in chat. Review and send it when ready.');
      }
    } catch {
      if (
        current.current.resourceId === source.resource_id &&
        current.current.resourceRevision === source.resource_revision
      )
        setError(
          'The fix draft is unavailable. Run the current design review again.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }
  async function more() {
    if (!state?.next_cursor || operation.current) return;
    const token = Symbol('design-page');
    operation.current = token;
    setSaving(true);
    const source = state;
    try {
      const value = await props.load({
        page_id: props.pageId,
        element_id: props.selectedElementId,
        section,
        cursor: state.next_cursor,
      });
      if (
        current.current.resourceId !== source.resource_id ||
        current.current.resourceRevision !== source.resource_revision ||
        current.current.pageId !== source.page_id
      )
        return;
      if (
        value.resource_id !== source.resource_id ||
        value.resource_revision !== source.resource_revision ||
        value.section !== source.section
      )
        throw new Error('Resource changed');
      if (value.items.length > 50)
        throw new Error('Control page exceeded its bound');
      setState(value);
      session.set('controlPage', true);
    } catch {
      if (
        current.current.resourceId === source.resource_id &&
        current.current.pageId === source.page_id
      )
        setError(
          'More controls could not be loaded. Reload the current design.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }
  // Auto-save: brand colours apply shortly after the last change, other
  // fields when they lose focus. A change made while another save runs waits
  // for it and then applies against the reloaded revision.
  const brandTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queued = useRef<{ brand: boolean; styles: boolean }>({
    brand: false,
    styles: false,
  });
  useEffect(
    () => () => {
      if (brandTimer.current) clearTimeout(brandTimer.current);
    },
    [],
  );
  function commit(kind: 'brand' | 'styles') {
    if (kind === 'brand' && brandTimer.current) {
      clearTimeout(brandTimer.current);
      brandTimer.current = null;
    }
    const snapshot = session.getSnapshot();
    if (!snapshot.dirtyFields.includes(kind) || staleDraft || props.blocked)
      return;
    if (
      kind === 'brand' &&
      (!snapshot.brand ||
        !colors.every(([key]) => HEX.test(snapshot.brand![key])))
    )
      return;
    if (operation.current || !snapshot.state) {
      queued.current[kind] = true;
      return;
    }
    queued.current[kind] = false;
    void apply(
      kind === 'brand' ? 'brand' : 'style',
      kind === 'brand' ? snapshot.brand! : snapshot.styles,
    );
  }
  useEffect(() => {
    if (saving || !state) return;
    if (queued.current.brand) commit('brand');
    else if (queued.current.styles) commit('styles');
    // Only a settled save or a reload releases queued changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saving, state]);
  // The Review tab checks the design by itself, and again after every saved
  // change: once per saved version, page and scope, so a failure never loops.
  const checked = useRef('');
  useEffect(() => {
    if (!visible || props.view !== 'review' || !state || saving || loading)
      return;
    const key = JSON.stringify([
      state.resource_id,
      state.resource_revision,
      state.page_id,
      scope,
    ]);
    if (checked.current === key || operation.current) return;
    checked.current = key;
    void scan();
    // scan reads the current state and scope itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, props.view, state, saving, loading, scope]);
  // Brand › From a website: read the page once, then apply what it uses
  // through the normal brand control (so the panel's Undo brings it back).
  const [website, setWebsite] = useState('');
  const [reading, setReading] = useState(false);
  async function fromWebsite() {
    if (!props.suggestBrand || !brand || reading || operation.current) return;
    let address = website.trim();
    if (!/^https?:\/\//i.test(address)) address = `https://${address}`;
    setReading(true);
    setError('');
    setNotice('');
    try {
      const found = await props.suggestBrand(address);
      const next = { ...brand };
      let used = 0;
      for (const key of [
        'primary_color',
        'secondary_color',
        'accent_color',
      ] as const)
        if (found[key]) {
          next[key] = found[key]!;
          used += 1;
        }
      for (const key of ['heading_font', 'body_font'] as const)
        if (found[key]) next[key] = found[key]!;
      if (!found.found || JSON.stringify(next) === JSON.stringify(brand)) {
        setNotice(`No colours or fonts were found on ${found.site}.`);
        return;
      }
      setBrand(next);
      setWebsite('');
      await apply('brand', next);
      setNotice(
        `Used ${used === 1 ? 'a colour' : `${used} colours`}${found.heading_font || found.body_font ? ' and fonts' : ''} from ${found.site}.`,
      );
    } catch (reason) {
      setError(clientError(reason).message);
    } finally {
      setReading(false);
    }
  }
  function changeBrand(next: DesignBrand, delay = BRAND_DELAY) {
    setBrand(next);
    if (brandTimer.current) clearTimeout(brandTimer.current);
    brandTimer.current = setTimeout(() => commit('brand'), delay);
  }
  useEffect(() => {
    const logo = pendingLogo.current;
    const saved = session.getSnapshot().brand;
    if (!logo || !state || !saved || saving) return;
    pendingLogo.current = null;
    changeBrand({ ...saved, logo_asset_id: logo }, 0);
    // Only a reload after the upload releases the new logo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, saving]);
  // Fonts that need no download, for the Type pickers.
  const fonts = useDesignCatalog(
    load,
    pageId,
    'fonts',
    visible && (!props.view || props.view === 'properties'),
  );
  const [logoOpen, setLogoOpen] = useState(false);

  if (!props.visible) return null;
  const busy = loading || saving || staleDraft || Boolean(props.blocked);
  const view = props.view;
  const showProperties = !view || view === 'properties';
  const showLibrary = !view || view === 'library';
  // The Review tab checks the design by itself (parity row 25).
  const showReview = view === 'review';
  const safeCount =
    review?.findings.filter((finding) => finding.auto_fixable).length ?? 0;
  const pendingFields = retained.dirtyFields.filter((field) =>
    ['brand', 'styles'].includes(field),
  );
  const status = saving
    ? 'Saving…'
    : notice === 'Changes saved.'
      ? 'Saved.'
      : notice;
  return (
    <section
      className="design-controls"
      aria-label={
        view === 'library'
          ? 'Design library'
          : view === 'review'
            ? 'Design review'
            : 'Design controls'
      }
      aria-busy={busy}
    >
      {staleDraft && (
        <div className="inspector-callout" role="status">
          <p>
            Your unsaved design change belongs to an earlier page, element or
            version. Go back to it, or discard the change to edit the current
            design.
          </p>
        </div>
      )}
      {retained.dirtySource && (staleDraft || error) && (
        <Button
          disabled={saving || Boolean(props.blocked)}
          onClick={() => session.reset()}
        >
          Discard design draft
        </Button>
      )}
      {error && (
        <ErrorState
          title="Design controls unavailable"
          action={
            <Button
              disabled={saving}
              onClick={() => {
                // The design shown and these controls are both read again.
                props.onReload();
                setReload((value) => value + 1);
              }}
            >
              Retry
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {showProperties && state?.element && (
        <section className="inspector-section" aria-label="Selection">
          <h4>
            Selection <span className="inspector-tag">{state.element.tag}</span>
          </h4>
          <div className="inspector-grid">
            {styleFields.map(([key, label]) => (
              <label key={key} className="inspector-field">
                <span>{label}</span>
                <Input
                  aria-label={`Element ${key}`}
                  maxLength={256}
                  disabled={busy}
                  value={styles[key] ?? ''}
                  placeholder="—"
                  onChange={(event) =>
                    setStyles({ ...styles, [key]: event.target.value })
                  }
                  onBlur={() => commit('styles')}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') event.currentTarget.blur();
                  }}
                />
              </label>
            ))}
          </div>
          {['landing', 'app_mockup', 'storyboard'].includes(state.mode) && (
            <div className="inspector-subsection">
              <h5>Interaction</h5>
              <Field label="Hotspot action">
                <Select
                  aria-label="Hotspot action"
                  disabled={busy}
                  value={action}
                  onChange={(event) => setAction(event.target.value)}
                >
                  <option value="navigate">Go to screen</option>
                  <option value="toggle_state">Toggle state</option>
                  <option value="play_media">Play media</option>
                  <option value="clear">No interaction</option>
                </Select>
              </Field>
              {action !== 'clear' && (
                <Field label="Hotspot target">
                  <Input
                    aria-label="Hotspot target"
                    disabled={busy}
                    value={target}
                    maxLength={128}
                    onChange={(event) => setTarget(event.target.value)}
                  />
                </Field>
              )}
              <Button
                disabled={busy}
                onClick={() => void apply('hotspot', { action, target })}
              >
                Apply interaction
              </Button>
            </div>
          )}
        </section>
      )}
      {showProperties && brand && state && (
        <>
          <section className="inspector-section" aria-label="Brand">
            <h4>Brand</h4>
            <div className="inspector-swatches">
              {colors.map(([key, label]) => (
                <div key={key} className="inspector-swatch">
                  <input
                    type="color"
                    aria-label={`${label} picker`}
                    disabled={busy}
                    value={pickerValue(brand[key])}
                    onChange={(event) =>
                      changeBrand({ ...brand, [key]: event.target.value })
                    }
                  />
                  <span className="inspector-swatch-name" aria-hidden>
                    {label.replace(' colour', '')}
                  </span>
                  <Input
                    aria-label={label}
                    className="inspector-hex"
                    value={brand[key]}
                    disabled={busy}
                    maxLength={7}
                    aria-invalid={!HEX.test(brand[key]) || undefined}
                    onChange={(event) =>
                      changeBrand(
                        { ...brand, [key]: event.target.value },
                        BRAND_DELAY * 2,
                      )
                    }
                    onBlur={() => commit('brand')}
                  />
                </div>
              ))}
            </div>
            {!colors.every(([key]) => HEX.test(brand[key])) && (
              <p className="muted">Use hex colours such as #2563EB.</p>
            )}
            {props.suggestBrand && (
              <form
                className="inspector-inline-form"
                aria-label="Brand from a website"
                onSubmit={(event) => {
                  event.preventDefault();
                  void fromWebsite();
                }}
              >
                <Input
                  aria-label="Website address"
                  inputMode="url"
                  autoComplete="url"
                  placeholder="From a website: https://…"
                  value={website}
                  maxLength={2048}
                  disabled={busy || reading}
                  onChange={(event) => setWebsite(event.target.value)}
                />
                <Button
                  type="submit"
                  disabled={busy || reading || !website.trim()}
                >
                  {reading ? 'Reading…' : 'Use its colours'}
                </Button>
              </form>
            )}
          </section>
          <section className="inspector-section" aria-label="Type">
            <h4>Type</h4>
            {(
              [
                ['heading_font', 'Heading font'],
                ['body_font', 'Body font'],
              ] as const
            ).map(([key, label]) => (
              <div className="design-font-row" key={key}>
                <span className="design-font-caption" aria-hidden>
                  {label}
                </span>
                <ArtifactFontPicker
                  label={label}
                  value={brand[key]}
                  catalog={fonts}
                  disabled={busy}
                  onChange={(font) => changeBrand({ ...brand, [key]: font }, 0)}
                />
              </div>
            ))}
          </section>
          <Disclosure
            summary="Logo"
            className="inspector-disclosure"
            open={logoOpen}
            onOpenChange={setLogoOpen}
          >
            {logoOpen && (
              <ArtifactLogoPicker
                logo={brand}
                load={load}
                thumbnail={props.thumbnail}
                pageId={pageId}
                resourceRevision={resourceRevision}
                disabled={busy}
                onChange={(change) => changeBrand({ ...brand, ...change }, 0)}
                onUpload={(chosen) => void upload(chosen, true)}
              />
            )}
          </Disclosure>
          {pendingFields.length > 0 && !saving && !staleDraft && (
            <div className="inspector-action">
              <span className="muted">Unsaved change</span>
              <Button
                disabled={busy}
                onClick={() =>
                  pendingFields.includes('brand')
                    ? commit('brand')
                    : commit('styles')
                }
              >
                Apply now
              </Button>
            </div>
          )}
        </>
      )}
      {showLibrary && (
        <section className="inspector-section" aria-label="Library">
          <Segmented
            size="sm"
            label="Library section"
            className="inspector-segmented"
            value={section}
            onChange={(value) => setSection(value)}
            options={(
              [
                'elements',
                'assets',
                'fonts',
                'presets',
                'interactions',
                ...(state?.mode === 'deck' || state?.mode === 'landing'
                  ? (['blocks'] as const)
                  : []),
              ] as DesignSection[]
            ).map((key) => ({
              value: key,
              label: sectionWords[key],
              disabled: busy,
            }))}
          />
          {section === 'presets' && props.mutatePreset && (
            <div className="inspector-inline-form">
              <Input
                aria-label="New global preset name"
                placeholder="Preset name"
                value={presetName}
                maxLength={256}
                disabled={busy}
                onChange={(event) => setPresetName(event.target.value)}
              />
              <Button
                disabled={busy || !state || !presetName.trim()}
                onClick={() =>
                  void changePreset({ action: 'save', name: presetName.trim() })
                }
              >
                Save current brand as preset
              </Button>
            </div>
          )}
          {presetReview?.action === 'delete' && (
            <div
              className="inspector-callout"
              role="group"
              aria-label="Confirm global preset deletion"
            >
              <p>
                Delete global preset “{presetReview.name}”? This changes the
                shared catalog for all projects; saved project brands stay
                unchanged.
              </p>
              <div className="action-cluster">
                <Button disabled={busy} onClick={() => setPresetReview(null)}>
                  Keep preset
                </Button>
                <Button
                  variant="danger"
                  disabled={busy}
                  onClick={() => void changePreset()}
                >
                  Confirm preset deletion
                </Button>
              </div>
            </div>
          )}
          {section === 'assets' && (
            <div className="inspector-inline-form">
              <Input
                type="file"
                aria-label="Choose asset"
                disabled={busy}
                accept=".png,.jpg,.jpeg,.webp,.gif,.svg,.mp4,.m4v,.webm,.wav,.ogg,.mp3"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <Button
                disabled={busy || !file || !state}
                onClick={() => void upload()}
              >
                Add asset
              </Button>
              {file && (
                <p className="muted">Selected local asset: {file.name}</p>
              )}
              <p className="muted">
                Assets stay saved when removed from a page; original files are
                kept for history and recovery.
              </p>
            </div>
          )}
          {state && (
            <>
              <p className="inspector-meta">
                {state.items.length} of {state.item_count}{' '}
                {sectionWords[section].toLowerCase()}
              </p>
              <ul className="inspector-list">
                {state.items.map((item) => (
                  <li key={item.id}>
                    <span className="inspector-list-text">
                      <span className="inspector-list-name">{item.label}</span>
                      {item.detail && (
                        <span className="inspector-list-detail">
                          {item.detail}
                        </span>
                      )}
                    </span>
                    <span className="inspector-list-actions">
                      {section === 'elements' && (
                        <Button
                          disabled={busy || !item.available}
                          aria-label={`Select ${item.label}`}
                          onClick={() => props.onSelectElement(item.id)}
                        >
                          Select
                        </Button>
                      )}
                      {section === 'presets' && (
                        <>
                          <Button
                            disabled={busy || !item.available}
                            aria-label={`Apply ${item.label}`}
                            onClick={() =>
                              void apply('preset', { preset_id: item.id })
                            }
                          >
                            Apply
                          </Button>
                          {props.mutatePreset && (
                            <Menu
                              label={`More actions for ${item.label}`}
                              iconOnly
                              actions={[
                                {
                                  label: `Replace ${item.label} with current brand`,
                                  disabled: busy || !item.available,
                                  onSelect: () =>
                                    void changePreset({
                                      action: 'save',
                                      name: item.label,
                                      preset_id: item.id,
                                    }),
                                },
                                {
                                  label: `Delete ${item.label} preset`,
                                  danger: true,
                                  disabled: busy || !item.available,
                                  onSelect: () =>
                                    setPresetReview({
                                      action: 'delete',
                                      name: item.label,
                                      preset_id: item.id,
                                    }),
                                },
                              ]}
                            >
                              <MoreHorizontal size={15} aria-hidden />
                            </Menu>
                          )}
                        </>
                      )}
                      {section === 'assets' && (
                        <>
                          <Button
                            disabled={busy || !item.available}
                            aria-label={`Insert ${item.label}`}
                            onClick={() =>
                              void apply('asset_insert', { asset_id: item.id })
                            }
                          >
                            Insert
                          </Button>
                          <Menu
                            label={`More actions for ${item.label}`}
                            iconOnly
                            actions={[
                              {
                                label: `Remove ${item.label} from page`,
                                disabled: busy,
                                onSelect: () =>
                                  void apply('asset_remove', {
                                    asset_id: item.id,
                                  }),
                              },
                              {
                                label: `Remove ${item.label} from list`,
                                disabled: busy,
                                onSelect: () =>
                                  void apply('asset_forget', {
                                    asset_id: item.id,
                                  }),
                              },
                            ]}
                          >
                            <MoreHorizontal size={15} aria-hidden />
                          </Menu>
                        </>
                      )}
                      {section === 'blocks' && (
                        <Button
                          disabled={busy || !item.available}
                          aria-label={`Insert ${item.label}`}
                          onClick={() =>
                            void apply('block_insert', {
                              component_name: item.id,
                            })
                          }
                        >
                          Insert
                        </Button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
              {!state.items.length && (
                <p className="muted">
                  Nothing in {sectionWords[section].toLowerCase()} yet.
                </p>
              )}
              <div className="action-cluster">
                {retained.controlPage && (
                  <Button
                    disabled={busy}
                    onClick={() => setReload((value) => value + 1)}
                  >
                    First controls page
                  </Button>
                )}
                {state.next_cursor && (
                  <Button disabled={busy} onClick={() => void more()}>
                    Next controls page
                  </Button>
                )}
              </div>
            </>
          )}
        </section>
      )}
      {showReview && (
        <section className="inspector-section" aria-label="Review">
          <div className="inspector-inline-form">
            <Segmented
              size="sm"
              label="Review scope"
              value={scope}
              onChange={(value) => {
                setScope(value);
                setReview(null);
              }}
              options={[
                { value: 'page', label: 'This page', disabled: busy },
                { value: 'project', label: 'Whole design', disabled: busy },
              ]}
            />
            {safeCount > 0 && (
              <Button
                variant="primary"
                disabled={busy}
                onClick={() =>
                  void apply('review_fix_all', { scope }, review!.page_id)
                }
              >
                Fix all safe issues ({safeCount})
              </Button>
            )}
          </div>
          <p className="muted">
            A quick heuristic check of layout, text and brand; it checks again
            after every change. It doesn't replace your own look at the design.
          </p>
          {!review && !error && state && (
            <p className="muted" role="status">
              Checking the design…
            </p>
          )}
          {review && (
            <div
              className="design-review"
              role="group"
              aria-label="Design review findings"
            >
              <p className="inspector-meta">
                {review.finding_count === 0
                  ? 'No issues found.'
                  : `Score ${review.score} · ${review.finding_count} ${review.finding_count === 1 ? 'issue' : 'issues'}`}
              </p>
              <ul className="inspector-list">
                {review.findings.map((finding) => (
                  <li key={finding.id} className="design-finding">
                    <StatusDot
                      tone={
                        /high|error|critical/i.test(finding.severity)
                          ? 'danger'
                          : /medium|warn/i.test(finding.severity)
                            ? 'warning'
                            : 'neutral'
                      }
                      label={humanizeToken(finding.severity)}
                    />
                    <span className="inspector-list-text">
                      <span className="inspector-list-name">
                        {finding.message}
                      </span>
                      <span className="inspector-list-detail">
                        {finding.suggested_fix}
                      </span>
                    </span>
                    <span className="inspector-list-actions">
                      {finding.auto_fixable ? (
                        <Button
                          disabled={busy}
                          aria-label={`Fix: ${finding.message}`}
                          onClick={() =>
                            void apply(
                              'review_fix',
                              { finding_id: finding.id },
                              finding.page_id,
                            )
                          }
                        >
                          Fix
                        </Button>
                      ) : (
                        props.draftFix &&
                        props.onDraftText && (
                          <Button
                            disabled={busy}
                            aria-label={`Ask Row-Bot to fix: ${finding.message}`}
                            onClick={() => void draft(finding)}
                          >
                            Ask Row-Bot
                          </Button>
                        )
                      )}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="action-cluster">
                {retained.reviewPage && (
                  <Button disabled={busy} onClick={() => void scan()}>
                    First findings page
                  </Button>
                )}
                {review.next_cursor && (
                  <Button
                    disabled={busy}
                    onClick={() => void scan(review.next_cursor ?? undefined)}
                  >
                    Next findings page
                  </Button>
                )}
              </div>
            </div>
          )}
        </section>
      )}
      <p className="inspector-status" role="status">
        {status}
      </p>
    </section>
  );
}
