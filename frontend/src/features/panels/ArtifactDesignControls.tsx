import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import {
  DesignFormSession,
  type DesignFormState,
} from './artifact-design-sessions';
import {
  Image as ImageGlyph,
  LayoutGrid,
  MoreHorizontal,
  Sparkles,
  Square,
  Type,
  X,
} from 'lucide-react';
import ArtifactFontPicker from './ArtifactFontPicker';
import ArtifactLogoPicker from './ArtifactLogoPicker';
import ArtifactDesignLibrary from './ArtifactDesignLibrary';
import ArtifactDesignStyles, {
  DesignGroup,
  DesignRow,
  SETTLE_DELAY,
} from './ArtifactDesignStyles';
import { useDesignCatalog } from './artifact-design-catalog';
import type { DesignLook } from './artifact-design-values';
import { elementWord } from './DesignSelection';
import { humanizeToken } from '../../ui/format';
import { clientError } from '../../api/errors';
import type { ArtifactBrandSuggestion } from '../../api/types';
import {
  Button,
  ErrorState,
  Field,
  IconButton,
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
/** The selected element, and what it is (so its controls can match). */
export type DesignElementView = {
  id: string;
  tag: string;
  styles: Record<string, string>;
  action: string;
  kind: 'text' | 'image' | 'shape' | 'layout';
  text: string;
  alt: string;
  asset_id: string;
};
export type DesignControlsState = {
  resource_id: string;
  resource_revision: string;
  mode: string;
  page_id: string;
  brand: DesignBrand;
  element: DesignElementView | null;
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
export type DesignControlsView = 'selection' | 'brand' | 'library' | 'review';
export type DesignControlsProps = {
  /** Which inspector view this instance shows. */
  view: DesignControlsView;
  resourceId: string;
  session?: DesignFormSession;
  blocked?: boolean;
  resourceRevision: string;
  pageId: string;
  selectedElementId?: string;
  /** How the selected element looks on the canvas now (display only). */
  look?: DesignLook;
  /** The selected text's own text field, shown under its heading. */
  textEditor?: ReactNode;
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
      | 'image'
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
  /** Leave the selection (the page's own controls show). */
  onClearSelection?: () => void;
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
/** Edit as CSS…: every saved style the design controls accept, as text. */
const cssFields = [
  'color',
  'background-color',
  'font-family',
  'font-size',
  'font-weight',
  'line-height',
  'letter-spacing',
  'text-align',
  'padding',
  'margin',
  'gap',
  'border-radius',
  'border-style',
  'border-width',
  'border-color',
  'opacity',
  'width',
  'height',
  'object-fit',
  'object-position',
] as const;
const KIND_ICONS = {
  text: Type,
  image: ImageGlyph,
  shape: Square,
  layout: LayoutGrid,
} as const;
const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;
/** Brand colours apply this long after the last change (auto-save). */
const BRAND_DELAY = 700;
/** At most this many elements show under "On this page". */
const PAGE_CHIPS = 12;
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
/** The selected element in words: what it is and what it says. */
function describe(element: DesignElementView) {
  const quote = (text: string) =>
    `“${text.length > 40 ? `${text.slice(0, 39)}…` : text}”`;
  if (element.kind === 'image')
    return { title: 'Image', detail: element.alt || 'No description yet' };
  if (element.kind === 'text') {
    const word = elementWord(element.tag);
    return {
      title: ['Block', 'Section'].includes(word) ? 'Text' : word,
      detail: element.text ? quote(element.text) : '',
    };
  }
  return {
    title: element.kind === 'shape' ? 'Shape' : 'Group',
    detail: element.text ? quote(element.text) : elementWord(element.tag),
  };
}
export default function ArtifactDesignControls(props: DesignControlsProps) {
  const {
    resourceId,
    resourceRevision,
    pageId,
    selectedElementId,
    visible,
    load,
    view,
  } = props;
  const [local] = useState(() => new DesignFormSession());
  const session = props.session ?? local;
  const retained = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const {
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
    // A newer version of the same selection keeps its controls in place
    // (and focus in them) while it is read; anything else starts afresh.
    const held = session.getSnapshot().state;
    if (
      !held ||
      held.resource_id !== resourceId ||
      held.page_id !== pageId ||
      (held.element?.id ?? '') !== (selectedElementId ?? '')
    )
      session.set('state', null);
    session.set('review', null);
    session.set('error', '');
    if (!visible) return;
    session.set('loading', true);
    void load({
      page_id: pageId,
      element_id: selectedElementId,
      section: 'elements',
      limit: 50,
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
        else {
          session.set('state', null);
          session.set('error', problem.message);
        }
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
  // Edit as CSS… stays open for one selection.
  const [css, setCss] = useState({ open: false, element: '' });
  const cssOpen = css.open && css.element === (selectedElementId ?? '');

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
          ? 'Style removed. The saved copy is kept for recovery.'
          : 'Style saved.',
      );
      setReload((value) => value + 1);
    } catch {
      if (
        current.current.resourceId === sourceId &&
        current.current.resourceRevision === sourceRevision
      )
        setError(
          "The style change wasn't confirmed. The previous copy is kept; check the saved styles before trying again.",
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }

  // An uploaded logo (or replacement picture) is used once the version with
  // it is loaded.
  const pending = useRef<{ use: 'logo' | 'image'; asset: string } | null>(null);
  async function upload(
    chosen: File | null = file,
    use: 'asset' | 'logo' | 'image' = 'asset',
  ) {
    if (!chosen || !state || operation.current || !props.visible) return;
    if (chosen.size === 0 || chosen.size > 25 * 1024 * 1024) {
      setError('Choose a file that is not empty and up to 25 MB.');
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
      if (use !== 'asset')
        pending.current = result.asset_id
          ? { use, asset: result.asset_id }
          : null;
      else {
        session.set('file', null);
        session.committed('asset_upload');
      }
      setNotice(use === 'logo' ? 'Logo added.' : 'Image added.');
      if (result.resource_revision === current.current.resourceRevision)
        setReload((value) => value + 1);
      // The design moved on: controls wait until its new version is read.
      else session.set('loading', true);
    } catch {
      if (current.current.resourceId === sourceId)
        setError(
          "The upload wasn't confirmed. The file is kept for recovery; reload the design before trying again.",
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
    const selected = props.selectedElementId;
    const same = () =>
      current.current.resourceId === sourceId &&
      current.current.pageId === props.pageId &&
      current.current.selectedElementId === selected;
    try {
      const result = await props.apply(
        kind,
        payload,
        state.resource_revision,
        pageId,
        selected,
      );
      if (same()) {
        session.committed(kind);
        setNotice('Changes saved.');
        setReview(null);
        if (result.resource_revision === current.current.resourceRevision)
          setReload((value) => value + 1);
        else session.set('loading', true);
      }
    } catch (reason) {
      if (!same()) return;
      // The element went away meanwhile (an undo, Row-Bot's edit): nothing
      // was changed, so the selection simply clears.
      if (selected && failure(reason).code === 'element_unavailable') {
        session.committed(kind);
        current.current.onSelectionLost(selected);
        return;
      }
      setError(
        "That change wasn't confirmed. Reload the design before trying again.",
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
        setError("The check couldn't run, so there is no result yet.");
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
        setNotice('A fix is drafted in the chat. Review it and send it.');
      }
    } catch {
      if (
        current.current.resourceId === source.resource_id &&
        current.current.resourceRevision === source.resource_revision
      )
        setError("The fix couldn't be drafted. Run the check again.");
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }
  // Auto-save: brand colours and steppers apply shortly after the last
  // change, choices at once, text fields when they lose focus. A change made
  // while another save runs waits for it and then applies against the
  // reloaded revision.
  const timers = useRef<{
    brand: ReturnType<typeof setTimeout> | null;
    styles: ReturnType<typeof setTimeout> | null;
  }>({ brand: null, styles: null });
  const queued = useRef<{ brand: boolean; styles: boolean }>({
    brand: false,
    styles: false,
  });
  useEffect(() => {
    const pendingTimers = timers.current;
    return () => {
      if (pendingTimers.brand) clearTimeout(pendingTimers.brand);
      if (pendingTimers.styles) clearTimeout(pendingTimers.styles);
    };
  }, []);
  function commit(kind: 'brand' | 'styles') {
    const timer = timers.current[kind];
    if (timer) {
      clearTimeout(timer);
      timers.current[kind] = null;
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
    if (operation.current || !snapshot.state || snapshot.loading) {
      queued.current[kind] = true;
      return;
    }
    queued.current[kind] = false;
    void apply(
      kind === 'brand' ? 'brand' : 'style',
      kind === 'brand' ? snapshot.brand! : snapshot.styles,
    );
  }
  function schedule(kind: 'brand' | 'styles', delay: number) {
    const timer = timers.current[kind];
    if (timer) clearTimeout(timer);
    timers.current[kind] = setTimeout(() => commit(kind), delay);
  }
  useEffect(() => {
    if (saving || loading || !state) return;
    if (queued.current.brand) commit('brand');
    else if (queued.current.styles) commit('styles');
    // Only a settled save or a reload releases queued changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saving, loading, state]);
  // The Review view checks the design by itself, and again after every saved
  // change: once per saved version, page and scope, so a failure never loops.
  const checked = useRef('');
  useEffect(() => {
    if (!visible || view !== 'review' || !state || saving || loading) return;
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
  }, [visible, view, state, saving, loading, scope]);
  // Brand › From a website: read the page once, then apply what it uses
  // through the normal brand control (so the panel's Undo brings it back).
  const [website, setWebsite] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  async function fromWebsite() {
    if (!props.suggestBrand || !brand || reading || operation.current) return;
    let address = (website ?? '').trim();
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
      setWebsite(null);
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
    schedule('brand', delay);
  }
  function changeStyle(patch: Record<string, string>, settle = false) {
    setStyles({ ...session.getSnapshot().styles, ...patch });
    schedule('styles', settle ? SETTLE_DELAY : 0);
  }
  useEffect(() => {
    const used = pending.current;
    const saved = session.getSnapshot().brand;
    if (!used || !state || !saved || saving) return;
    pending.current = null;
    if (used.use === 'logo')
      changeBrand({ ...saved, logo_asset_id: used.asset }, 0);
    else void apply('image', { asset_id: used.asset });
    // Only a reload after the upload releases the new picture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, saving]);
  const element = state?.element ?? null;
  // Fonts that need no download, for the font pickers.
  const fonts = useDesignCatalog(
    load,
    pageId,
    'fonts',
    visible &&
      (view === 'brand' || (view === 'selection' && element?.kind === 'text')),
  );
  // The design's pictures, for Replace.
  const pictures = useDesignCatalog(
    load,
    pageId,
    'assets',
    visible && view === 'selection' && element?.kind === 'image',
    resourceRevision,
  );

  if (!props.visible) return null;
  const busy = loading || saving || staleDraft || Boolean(props.blocked);
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
  const hotspots =
    !!state && ['landing', 'app_mockup', 'storyboard'].includes(state.mode);
  const chips = (state?.items ?? []).filter((item) =>
    ['text', 'image', 'shape'].includes(item.detail),
  );
  const described = element ? describe(element) : null;
  const KindIcon = element ? KIND_ICONS[element.kind] : null;
  return (
    <section
      className="design-controls"
      aria-label={
        view === 'library'
          ? 'Design library'
          : view === 'review'
            ? 'Design review'
            : view === 'brand'
              ? 'Design brand'
              : 'Design controls'
      }
      aria-busy={busy}
    >
      {staleDraft && (
        <div className="inspector-callout" role="status">
          <p>
            Your unsaved change belongs to another page, element or version. Go
            back to it, or discard it to change the design as it is now.
          </p>
        </div>
      )}
      {retained.dirtySource && (staleDraft || error) && (
        <Button
          disabled={saving || Boolean(props.blocked)}
          onClick={() => session.reset()}
        >
          Discard unsaved change
        </Button>
      )}
      {error && (
        <ErrorState
          title="That didn't work"
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
      {view === 'selection' && element && described && KindIcon && (
        <>
          <header className="design-what">
            <span className="design-what-tile" data-kind={element.kind}>
              <KindIcon size={16} aria-hidden />
            </span>
            <span className="design-what-text">
              <strong>{described.title}</strong>
              {described.detail && <small>{described.detail}</small>}
            </span>
            <Menu
              label="More for this element"
              iconOnly
              variant="ghost"
              actions={[
                {
                  label: cssOpen ? 'Hide CSS' : 'Edit as CSS…',
                  onSelect: () =>
                    setCss({ open: !cssOpen, element: element.id }),
                },
                ...(props.onClearSelection
                  ? [
                      {
                        label: 'Clear selection',
                        onSelect: props.onClearSelection,
                      },
                    ]
                  : []),
              ]}
            >
              <MoreHorizontal size={15} aria-hidden />
            </Menu>
          </header>
          {element.kind === 'text' && props.textEditor}
          {brand && (
            <ArtifactDesignStyles
              element={element}
              styles={styles}
              look={props.look}
              brand={brand}
              fonts={fonts}
              busy={busy}
              onStyle={changeStyle}
              image={{
                images: pictures.items,
                thumbnail: props.thumbnail,
                onReplace: (asset) => void apply('image', { asset_id: asset }),
                onUpload: (chosen) => void upload(chosen, 'image'),
                onDescribe: (alt) => void apply('image', { alt }),
              }}
            />
          )}
          {cssOpen && (
            <DesignGroup
              title="CSS"
              action={
                <IconButton
                  size="sm"
                  label="Close CSS"
                  onClick={() => setCss({ open: false, element: '' })}
                >
                  <X size={14} aria-hidden />
                </IconButton>
              }
            >
              <div className="inspector-grid">
                {cssFields.map((key) => (
                  <label key={key} className="inspector-field">
                    <span>{key}</span>
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
            </DesignGroup>
          )}
          {hotspots && (
            <DesignGroup title="Interaction">
              <Field label="When clicked">
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
                <Field label="Target">
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
            </DesignGroup>
          )}
          {(element.kind === 'text' || element.kind === 'image') && (
            <p className="design-hint-line">
              <Sparkles size={14} aria-hidden />
              <span>
                {element.kind === 'text'
                  ? 'Double-click the text on the page to edit it. Every change can be undone.'
                  : 'Or ask Row-Bot about it: “make the photo warmer”.'}
              </span>
            </p>
          )}
        </>
      )}
      {view === 'selection' && state && !element && chips.length > 0 && (
        <DesignGroup
          title="On this page"
          action={<small className="design-group-count">{chips.length}</small>}
        >
          <div className="design-chips">
            {chips.slice(0, PAGE_CHIPS).map((item) => {
              const Icon =
                KIND_ICONS[item.detail as keyof typeof KIND_ICONS] ?? Square;
              const name =
                item.label === item.kind ? elementWord(item.kind) : item.label;
              return (
                <button
                  key={item.id}
                  type="button"
                  className="design-chip"
                  aria-label={`Select ${item.label}`}
                  disabled={busy || !item.available}
                  onClick={() => props.onSelectElement(item.id)}
                >
                  <Icon size={12} aria-hidden />
                  <span>
                    {name.length > 24 ? `${name.slice(0, 23)}…` : name}
                  </span>
                </button>
              );
            })}
          </div>
        </DesignGroup>
      )}
      {view === 'brand' && brand && state && (
        <>
          <DesignGroup
            title="Colours"
            action={
              props.suggestBrand && (
                <Button
                  variant="ghost"
                  className="design-group-link"
                  aria-expanded={website !== null}
                  onClick={() => setWebsite(website === null ? '' : null)}
                >
                  From a website…
                </Button>
              )
            }
          >
            {website !== null && (
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
                  placeholder="https://…"
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
            <div className="design-brand-colours">
              {colors.map(([key, label]) => (
                <div key={key} className="design-brand-colour">
                  <input
                    type="color"
                    aria-label={`${label} picker`}
                    disabled={busy}
                    value={pickerValue(brand[key])}
                    onChange={(event) =>
                      changeBrand({ ...brand, [key]: event.target.value })
                    }
                  />
                  <span className="design-brand-name" aria-hidden>
                    {label.replace(' colour', '')}
                  </span>
                  <Input
                    aria-label={label}
                    className="design-brand-hex"
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
          </DesignGroup>
          <DesignGroup title="Fonts">
            {(
              [
                ['heading_font', 'Headings', 'Heading font'],
                ['body_font', 'Body', 'Body font'],
              ] as const
            ).map(([key, caption, label]) => (
              <DesignRow key={key} label={caption}>
                <ArtifactFontPicker
                  label={label}
                  value={brand[key]}
                  catalog={fonts}
                  disabled={busy}
                  onChange={(font) => changeBrand({ ...brand, [key]: font }, 0)}
                />
              </DesignRow>
            ))}
          </DesignGroup>
          <DesignGroup title="Logo">
            <ArtifactLogoPicker
              logo={brand}
              load={load}
              thumbnail={props.thumbnail}
              pageId={pageId}
              resourceRevision={resourceRevision}
              disabled={busy}
              onChange={(change) => changeBrand({ ...brand, ...change }, 0)}
              onUpload={(chosen) => void upload(chosen, 'logo')}
            />
          </DesignGroup>
        </>
      )}
      {(view === 'selection' || view === 'brand') &&
        pendingFields.length > 0 &&
        !saving &&
        !staleDraft && (
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
      {view === 'library' && state && (
        <ArtifactDesignLibrary
          blocks={state.mode === 'deck' || state.mode === 'landing'}
          load={load}
          thumbnail={props.thumbnail}
          pageId={pageId}
          resourceRevision={resourceRevision}
          busy={busy}
          onInsertBlock={(id) =>
            void apply('block_insert', { component_name: id })
          }
          onInsertAsset={(id) => void apply('asset_insert', { asset_id: id })}
          onRemoveAsset={(id) => void apply('asset_remove', { asset_id: id })}
          onForgetAsset={(id) => void apply('asset_forget', { asset_id: id })}
          onApplyPreset={(id) => void apply('preset', { preset_id: id })}
          presetActions={
            props.mutatePreset
              ? (item) => [
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
                ]
              : undefined
          }
          uploader={
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
              {file && <p className="muted">Chosen: {file.name}</p>}
            </div>
          }
          presetForm={
            props.mutatePreset && (
              <>
                <div className="inspector-inline-form">
                  <Input
                    aria-label="New global preset name"
                    placeholder="Style name"
                    value={presetName}
                    maxLength={256}
                    disabled={busy}
                    onChange={(event) => setPresetName(event.target.value)}
                  />
                  <Button
                    disabled={busy || !state || !presetName.trim()}
                    onClick={() =>
                      void changePreset({
                        action: 'save',
                        name: presetName.trim(),
                      })
                    }
                  >
                    Save current brand as preset
                  </Button>
                </div>
                {presetReview?.action === 'delete' && (
                  <div
                    className="inspector-callout"
                    role="group"
                    aria-label="Confirm global preset deletion"
                  >
                    <p>
                      Delete the style “{presetReview.name}”? Every design
                      shares it; brands already saved in designs stay as they
                      are.
                    </p>
                    <div className="action-cluster">
                      <Button
                        disabled={busy}
                        onClick={() => setPresetReview(null)}
                      >
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
              </>
            )
          }
        />
      )}
      {view === 'review' && (
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
            A quick check of layout, text and brand; it checks again after every
            change. It doesn&apos;t replace your own look at the design.
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
