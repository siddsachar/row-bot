import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  File as PageGlyph,
  History,
  ListChecks,
  Monitor,
  MoreHorizontal,
  MousePointer2,
  PanelRight,
  Play,
  Plus,
  Redo2,
  Share2,
  Smartphone,
  Tablet,
  Trash2,
  Undo2,
  X,
} from 'lucide-react';
import type {
  ArtifactPreview as Preview,
  ArtifactAuthoring,
  DesignerPalette,
} from '../../api/types';
import {
  Button,
  ErrorState,
  IconButton,
  Kbd,
  Menu,
  Segmented,
  Select,
  Skeleton,
  Toolbar,
  ToolbarSeparator,
} from '../../ui/primitives';
import { DesignRow } from './ArtifactDesignStyles';
import type { DesignLook } from './artifact-design-values';
import { ModalTask } from '../../ui/overlays';
import ArtifactEditor, { type ArtifactEditorProps } from './ArtifactEditor';
import { artifactBridgeMessage } from './artifact-bridge';
import ArtifactExports, { type ArtifactExportsProps } from './ArtifactExports';
import ArtifactSharingPanel, {
  type ArtifactSharingPanelProps,
} from './ArtifactSharingPanel';
import ArtifactPresentationPanel, {
  StaticDesignPage,
  type ArtifactPresentationPanelProps,
} from './ArtifactPresentationPanel';
import ArtifactDesignPanel, {
  type ArtifactDesignPanelProps,
} from './ArtifactDesignPanel';
import ArtifactDocumentImport from './ArtifactDocumentImport';
import {
  DesignCapabilities,
  useDesignLifecycle,
  type ArtifactLifecyclePanelProps,
} from './ArtifactLifecyclePanel';
import DesignPageStrip from './DesignPageStrip';
import type { DesignDrafting } from './design-drafting';
import { registerDesignCommands } from './design-commands';
import DesignSelection, {
  askText,
  type AskOutcome,
  type DesignSelectionState,
} from './DesignSelection';
import {
  afterRedo,
  afterUndo,
  redoTarget,
  undoTarget,
  type UndoChain,
} from './design-history';

export type ArtifactPreviewProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  load: (
    pageId?: string,
    knownRevision?: string,
    signal?: AbortSignal,
    authoring?: ArtifactAuthoring,
  ) => Promise<Preview>;
  loadEditing?: ArtifactEditorProps['load'];
  loadPalette?: (
    revision: string,
    query: string,
    signal: AbortSignal,
  ) => Promise<DesignerPalette>;
  onDraftText?: (text: string) => void;
  edit?: ArtifactEditorProps['edit'];
  /** Make a copy of this design beside it (it opens in its own panel). */
  duplicate?: () => Promise<void>;
  /** A turn is drafting this design: what it is doing (U35). */
  drafting?: DesignDrafting | null;
  createExport?: ArtifactExportsProps['create'];
  downloadExport?: ArtifactExportsProps['download'];
  saveExport?: ArtifactExportsProps['save'];
  revealExport?: ArtifactExportsProps['reveal'];
  sharing?: Pick<
    ArtifactSharingPanelProps,
    'prepare' | 'execute' | 'loadChannels'
  >;
  presentation?: Pick<ArtifactPresentationPanelProps, 'load' | 'preview'>;
  lifecycle?: Pick<ArtifactLifecyclePanelProps, 'load'>;
  design?: Pick<ArtifactDesignPanelProps, 'session' | 'onDraftText'>;
  /** The design's name as the workspace knows it. */
  title?: string;
  /** Hands a request about the selected element to the conversation. */
  onAsk?: (text: string) => AskOutcome;
};

/** The inspector's tabs; Review and Versions open from the toolbar. */
type Pane = 'selection' | 'brand' | 'library';
type InspectorView = Pane | 'review' | 'history';
type Side = 'inspector' | 'export' | 'share';
type Device = 'desktop' | 'tablet' | 'phone';

const DEVICES: Record<Exclude<Device, 'desktop'>, [number, number]> = {
  tablet: [834, 1112],
  phone: [390, 844],
};
const ZOOMS = ['fit', 'width', '0.5', '0.75', 'actual', '1.5', '2'] as const;
/**
 * The size menu (parity row 29): common formats that re-fit every page. Only
 * for page-based designs; landing pages and app mockups size by device width.
 * Anything unusual (custom sizes) is asked for in the conversation.
 */
export const DESIGN_SIZES = [
  { ratio: '16:9', label: '16:9 · Widescreen', width: 1920, height: 1080 },
  { ratio: '4:3', label: '4:3 · Standard', width: 1024, height: 768 },
  { ratio: '1:1', label: '1:1 · Square', width: 1080, height: 1080 },
  { ratio: 'A4', label: 'A4 · Document', width: 794, height: 1123 },
  { ratio: '9:16', label: '9:16 · Phone', width: 1080, height: 1920 },
] as const;
type DesignSize = (typeof DESIGN_SIZES)[number]['ratio'];

export function designSize(width: number, height: number) {
  return DESIGN_SIZES.find(
    (size) => size.width === width && size.height === height,
  );
}

function structureFailure(reason: unknown) {
  const code =
    typeof reason === 'object' && reason !== null && 'code' in reason
      ? String(reason.code)
      : '';
  return code === 'resource_revision_conflict'
    ? 'The design changed meanwhile, so nothing was changed. Try again.'
    : 'That change was not saved. The design is unchanged.';
}

/** Panel width up to which the toolbar is one short phone row (panels.css). */
const COMPACT = 460;
const noLifecycle = () => new Promise<never>(() => {});

function failureText(error: unknown): string {
  const code =
    typeof error === 'object' && error !== null && 'code' in error
      ? error.code
      : '';
  if (code === 'not_found' || code === 'resource_unavailable')
    return 'This design is no longer available. Your conversation is preserved.';
  if (
    code === 'action_denied' ||
    code === 'capability_revoked' ||
    code === 'resource_binding_revoked'
  )
    return 'Access to this design changed. Review its binding before continuing.';
  if (code === 'resource_revision_conflict')
    return 'This design changed while loading. Reload it to see its current version.';
  if (code === 'artifact_type_unavailable')
    return 'This design type is not supported by this client.';
  if (code === 'page_unavailable')
    return 'That page is no longer available. Reload the design to continue.';
  return 'The design is bound, but its preview could not load.';
}

function historyFailure(error: unknown): string {
  const code =
    typeof error === 'object' && error !== null && 'code' in error
      ? String(error.code)
      : '';
  if (code === 'resource_revision_conflict' || code === 'revision_conflict')
    return 'The design changed meanwhile. Nothing was undone; try again.';
  if (code === 'history_unavailable')
    return 'That saved version is unavailable. The design is unchanged.';
  return 'The change was not confirmed. The saved design is unchanged or shown as it is now.';
}

export default function ArtifactPreview({
  resourceId,
  resourceRevision,
  visible,
  load,
  loadEditing,
  loadPalette,
  onDraftText,
  edit,
  createExport,
  downloadExport,
  saveExport,
  revealExport,
  sharing,
  presentation,
  lifecycle,
  design,
  title,
  onAsk,
  duplicate,
  drafting,
}: ArtifactPreviewProps) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState('');
  const [palette, setPalette] = useState<DesignerPalette | null>(null);
  const [paletteError, setPaletteError] = useState('');
  const [selection, setSelection] = useState({
    resourceId,
    pageId: undefined as string | undefined,
  });
  const [refresh, setRefresh] = useState(0);
  const [zoom, setZoom] = useState<{ resourceId: string; value: string }>({
    resourceId: '',
    value: 'fit',
  });
  const [device, setDevice] = useState({
    resourceId,
    value: 'desktop' as Device,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [side, setSide] = useState<{ resourceId: string; view: Side | null }>({
    resourceId,
    view: null,
  });
  const sideView = side.resourceId === resourceId ? side.view : null;
  const [pane, setPane] = useState<Pane>('selection');
  const [inspectorMode, setInspectorMode] = useState<
    'panes' | 'review' | 'history'
  >('panes');
  const showing: InspectorView | null =
    sideView !== 'inspector'
      ? null
      : inspectorMode === 'panes'
        ? pane
        : inspectorMode;
  const [presenting, setPresenting] = useState(false);
  const [authoring, setAuthoring] = useState(false);
  const [selectedElementId, setSelectedElementId] = useState<string>();
  const [picked, setPicked] = useState<DesignSelectionState | null>(null);
  // How the element last clicked looks on the canvas, for its controls.
  const [look, setLook] = useState<{
    elementId: string;
    values: DesignLook;
  } | null>(null);
  const [capabilitiesOpen, setCapabilitiesOpen] = useState(false);
  const moreButton = useRef<HTMLButtonElement | null>(null);
  // A phone-width panel: one short toolbar row, the rest in ⋯.
  const [compact, setCompact] = useState(false);
  // The inspector as a sheet on a narrow panel: half height or full.
  const [sheet, setSheet] = useState<'half' | 'full'>('half');
  const [sheetDrag, setSheetDrag] = useState<number | null>(null);
  const dragged = useRef(false);
  const body = useRef<HTMLDivElement>(null);
  // Suggestions from a quick check of the page shown, for the Review button.
  const [suggestions, setSuggestions] = useState<{
    key: string;
    count: number;
  } | null>(null);
  const [notice, setNotice] = useState('');
  const [chain, setChain] = useState<UndoChain | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const [renamed, setRenamed] = useState<{
    resourceId: string;
    base: string | undefined;
    name: string;
  } | null>(null);
  const [nameSaving, setNameSaving] = useState(false);
  // Page and size changes: one at a time; a deleted page offers Undo in place.
  const [structureBusy, setStructureBusy] = useState(false);
  const [undoOffer, setUndoOffer] = useState(false);
  // The saved name shows until the server's title moves on; after that the
  // title leads, so an undo that restores the old name shows it again.
  const [titleSeen, setTitleSeen] = useState(title);
  if (titleSeen !== title) {
    setTitleSeen(title);
    if (renamed) setRenamed(null);
  }
  const frame = useRef<HTMLIFrameElement>(null);
  const inlineOperation = useRef(false);
  const historyOperation = useRef(false);
  const [viewport, setViewport] = useState({ width: 400, height: 225 });
  const measured = useRef(viewport);
  const panel = useRef<HTMLElement>(null);
  const presentButton = useRef<HTMLButtonElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  // Present fills the screen (U36); leaving full screen (Escape) ends it.
  const [fullscreen, setFullscreen] = useState(false);
  const enteredFullscreen = useRef(false);
  const frameHost = useRef<HTMLDivElement>(null);
  const latest = useRef<Preview | null>(null);
  const loader = useRef(load);
  const request = useRef(0);
  const pageId =
    selection.resourceId === resourceId ? selection.pageId : undefined;
  const canEdit = !!edit;
  const authoringScope = useMemo(
    () => ({
      resourceId,
      resourceRevision,
      pageId,
      identity:
        authoring && canEdit
          ? {
              previewId: crypto.randomUUID(),
              capability: crypto.randomUUID(),
            }
          : undefined,
    }),
    [authoring, resourceId, resourceRevision, pageId, canEdit],
  );
  const authoringIdentity = authoringScope.identity;

  useEffect(() => {
    loader.current = load;
  }, [load]);

  useEffect(() => {
    const epoch = ++request.current;
    if (!visible) return;
    const abort = new AbortController();
    const previous = latest.current;
    const known =
      previous?.resource_id === resourceId &&
      (pageId === undefined || previous.page_id === pageId)
        ? previous.preview_revision
        : undefined;
    setLoading(true);
    setError('');
    loader.current(pageId, known, abort.signal, authoringIdentity).then(
      (result) => {
        if (abort.signal.aborted || epoch !== request.current) return;
        if (result.resource_id !== resourceId) {
          setError(
            'The preview returned a different design. Reload this design.',
          );
          setLoading(false);
          return;
        }
        const next = result.unchanged
          ? previous?.resource_id === result.resource_id &&
            previous.page_id === result.page_id &&
            previous.preview_revision === result.preview_revision
            ? { ...result, html: previous.html }
            : null
          : result;
        if (!next?.html) {
          setError(
            'The preview needs a fresh copy. Reload the design to continue.',
          );
        } else {
          latest.current = next;
          setPreview(next);
        }
        setLoading(false);
      },
      (reason: unknown) => {
        if (abort.signal.aborted || epoch !== request.current) return;
        setError(failureText(reason));
        // Revoked or missing content must not remain readable behind an error.
        latest.current = null;
        setPreview(null);
        setLoading(false);
      },
    );
    return () => abort.abort();
  }, [
    resourceId,
    resourceRevision,
    pageId,
    refresh,
    visible,
    authoringIdentity,
  ]);

  const current = preview?.resource_id === resourceId ? preview : null;
  // Slides fit the stage; a tall page (a web page, a document) fits its width
  // and scrolls, like a browser would show it.
  const zoomMode =
    zoom.resourceId === resourceId
      ? zoom.value
      : current &&
          current.mode !== 'deck' &&
          current.canvas_height > current.canvas_width * 1.2
        ? 'width'
        : 'fit';
  const paletteRevision = current?.resource_revision;
  useEffect(() => {
    if (
      !visible ||
      !paletteOpen ||
      !loadPalette ||
      !onDraftText ||
      !paletteRevision
    )
      return;
    const abort = new AbortController();
    setPaletteError('');
    setPalette(null);
    void loadPalette(paletteRevision, paletteQuery, abort.signal)
      .then((result) => {
        if (abort.signal.aborted) return;
        if (
          result.resource_id !== resourceId ||
          result.resource_revision !== paletteRevision
        ) {
          setPaletteError(
            'The design changed. Open the picker again to search it.',
          );
          setPalette(null);
          return;
        }
        setPalette(result);
      })
      .catch((cause) => {
        if (!abort.signal.aborted) setPaletteError(failureText(cause));
      });
    return () => abort.abort();
  }, [
    visible,
    paletteOpen,
    loadPalette,
    onDraftText,
    paletteRevision,
    paletteQuery,
    resourceId,
  ]);
  const lifecycleState = useDesignLifecycle({
    load: lifecycle?.load ?? noLifecycle,
    resourceId,
    resourceRevision: current?.resource_revision ?? resourceRevision,
    visible:
      visible &&
      !!lifecycle &&
      !!current &&
      current.resource_revision === resourceRevision,
  });
  // The Review button counts the page's suggestions: one quick check per
  // saved version and page while the panel shows it; a failed check shows
  // no count.
  const designSession = design?.session;
  const checkRevision =
    current?.resource_revision === resourceRevision ? resourceRevision : '';
  const checkPage = current?.page_id ?? '';
  useEffect(() => {
    if (!visible || !designSession || !checkRevision || !checkPage) return;
    let active = true;
    const key = `${resourceId}:${checkRevision}:${checkPage}`;
    designSession
      .review({ page_id: checkPage, scope: 'page' })
      .then((value) => {
        if (active && value.resource_revision === checkRevision)
          setSuggestions({ key, count: value.finding_count });
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [visible, designSession, resourceId, checkRevision, checkPage]);
  const suggestionCount =
    suggestions?.key === `${resourceId}:${checkRevision}:${checkPage}`
      ? suggestions.count
      : 0;
  const pageLabel = current?.mode === 'deck' || !current ? 'Slide' : 'Page';
  const interactive =
    current?.scripts_allowed === true &&
    (!!authoringIdentity ||
      ['landing', 'app_mockup', 'storyboard'].includes(current.mode));
  const editCallback = useRef(edit);
  useEffect(() => {
    editCallback.current = edit;
  }, [edit]);
  // A picked element belongs to the preview it was picked on.
  const pickedCurrent =
    picked &&
    authoring &&
    current &&
    picked.pageId === current.page_id &&
    picked.revision === current.preview_revision
      ? picked
      : null;
  const shortcut = useRef<(kind: 'undo' | 'redo') => void>(() => undefined);
  useEffect(() => {
    if (!visible || !authoringIdentity || !current || !canEdit) return;
    let active = true;
    const receive = (event: MessageEvent) => {
      const message = artifactBridgeMessage(
        event,
        frame.current?.contentWindow ?? null,
        authoringIdentity,
        current.preview_revision,
      );
      if (!message) return;
      if (message.type === 'undo' || message.type === 'redo') {
        shortcut.current(message.type);
        return;
      }
      if (message.type === 'unavailable') {
        setNotice(
          'This inline edit is too large. Use the text field in Properties.',
        );
        return;
      }
      if (message.type === 'select') {
        setPicked({
          elementId: message.elementId,
          tag: message.tag,
          text: message.text,
          rect: message.rect,
          pageId: current.page_id,
          revision: current.preview_revision,
        });
        if (message.elementId) {
          setSelectedElementId(message.elementId);
          setLook({ elementId: message.elementId, values: message.look });
          // Its controls are on the Selection tab.
          setPane('selection');
          setInspectorMode('panes');
        }
        return;
      }
      setSelectedElementId(message.elementId);
      if (inlineOperation.current) {
        setNotice(
          'A design edit is still saving. Check the saved version before editing again.',
        );
        return;
      }
      inlineOperation.current = true;
      setNotice('');
      void editCallback
        .current?.(
          {
            operation: 'text',
            page_id: current.page_id,
            element_id: message.elementId,
            text: message.text,
          },
          current.resource_revision,
        )
        .then(
          (receipt) => {
            if (!active) return;
            if (receipt.status !== 'completed')
              setNotice(
                'The inline edit was not confirmed. The preview shows the saved design.',
              );
            setRefresh((value) => value + 1);
          },
          () => {
            if (!active) return;
            setNotice(
              'The inline edit was not confirmed. The preview shows the saved design.',
            );
            setRefresh((value) => value + 1);
          },
        )
        .finally(() => {
          inlineOperation.current = false;
        });
    };
    window.addEventListener('message', receive);
    return () => {
      active = false;
      window.removeEventListener('message', receive);
    };
  }, [visible, authoringIdentity, current, canEdit]);
  useEffect(() => {
    if (!visible || !frameHost.current) return;
    const element = frameHost.current;
    let active = true;
    const measure = () => {
      if (!active || frameHost.current !== element) return;
      const whole = panel.current?.getBoundingClientRect().width ?? 0;
      setCompact(whole > 0 && whole <= COMPACT);
      const width = element.clientWidth,
        height = element.clientHeight;
      if (
        width <= 0 ||
        height <= 0 ||
        (width === measured.current.width && height === measured.current.height)
      )
        return;
      const next = { width, height };
      measured.current = next;
      setViewport(next);
    };
    measure();
    const observer =
      typeof ResizeObserver === 'undefined'
        ? null
        : new ResizeObserver(measure);
    observer?.observe(element);
    return () => {
      active = false;
      observer?.disconnect();
    };
  }, [visible, current?.resource_id, presenting]);

  useEffect(() => {
    if (!presenting) return;
    const changed = () => {
      if (!!stage.current && document.fullscreenElement === stage.current) {
        enteredFullscreen.current = true;
        setFullscreen(true);
        return;
      }
      setFullscreen(false);
      // Leaving full screen ends presenting, unless the audience window took it.
      if (!enteredFullscreen.current) return;
      enteredFullscreen.current = false;
      setPresenting(false);
      requestAnimationFrame(() => presentButton.current?.focus());
    };
    document.addEventListener('fullscreenchange', changed);
    // Full screen may already have started before this listener existed.
    if (!!stage.current && document.fullscreenElement === stage.current)
      changed();
    return () => document.removeEventListener('fullscreenchange', changed);
  }, [presenting]);

  // Ends presenting and gives Present the focus back. While the stage is
  // still full screen the browser refuses focus outside it, so that waits
  // until full screen has ended.
  function stopPresenting(refocus: boolean) {
    enteredFullscreen.current = false;
    setFullscreen(false);
    setPresenting(false);
    const focus = () =>
      requestAnimationFrame(() => presentButton.current?.focus());
    if (document.fullscreenElement === stage.current && stage.current) {
      const leaving = document.exitFullscreen?.();
      if (refocus) void (leaving ?? Promise.resolve()).then(focus, focus);
      else void leaving?.catch(() => {});
    } else if (refocus) focus();
  }

  function present() {
    if (presenting) {
      stopPresenting(false);
      return;
    }
    setPresenting(true);
    setSide({ resourceId, view: null });
    // Inside the click, while the browser still counts it as the person's
    // gesture; where full screen is refused the presentation stays in the panel.
    void stage.current?.requestFullscreen?.().catch(() => {});
  }

  // While shown, this design's actions are in the global ⌘K palette.
  const commandActions = useRef<Record<string, () => void>>({});
  commandActions.current = {
    present,
    export: () => openSide('export'),
    share: () => openSide('share'),
    addPage,
    duplicate: () => void duplicateDesign(),
    review: () => openInspector('review'),
    history: () => openInspector('history'),
  };
  const shownTitle = (renamed?.name ?? title ?? 'this design').slice(0, 80);
  const word = pageLabel.toLowerCase();
  const offers = [
    presentation && lifecycleState.available('presentation')
      ? ['present', `Present ${shownTitle}`, 'slideshow full screen play']
      : null,
    createExport && downloadExport && lifecycleState.available('export')
      ? [
          'export',
          `Export ${shownTitle}…`,
          'pdf png pptx powerpoint html download save',
        ]
      : null,
    sharing && lifecycleState.available('sharing')
      ? [
          'share',
          `Share or publish ${shownTitle}…`,
          'publish link qr copy send channel',
        ]
      : null,
    edit && current
      ? ['addPage', `Add a ${word} to ${shownTitle}`, 'page slide screen new']
      : null,
    duplicate && current
      ? ['duplicate', `Duplicate ${shownTitle}`, 'copy design']
      : null,
    design
      ? ['review', `Review ${shownTitle}`, 'check fix issues critique']
      : null,
    loadEditing && edit
      ? ['history', `${shownTitle}: versions`, 'history undo restore']
      : null,
  ].filter(Boolean) as [string, string, string][];
  const offerKey = JSON.stringify(offers);
  useEffect(() => {
    if (!visible || !offers.length) return;
    return registerDesignCommands({
      resourceId,
      title: shownTitle,
      commands: offers.map(([id, label, keywords]) => ({
        id,
        label,
        keywords: `design ${keywords}`,
        run: () => commandActions.current[id]?.(),
      })),
    });
    // offerKey carries the offered commands; actions are read when run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, resourceId, offerKey]);

  // Each finished drafting step refreshes the page shown, so the design
  // appears as it is written instead of only when the turn ends.
  const draftingKeySeen = useRef('');
  useEffect(() => {
    const key = drafting?.key ?? '';
    if (!key || key === draftingKeySeen.current) return;
    draftingKeySeen.current = key;
    setRefresh((value) => value + 1);
  }, [drafting?.key]);

  function reload() {
    latest.current = null;
    setSelection({ resourceId, pageId: undefined });
    setRefresh((value) => value + 1);
  }

  // An edit (yours, Row-Bot's or an undo) can take the selected element
  // away; the inspector then reads the page without it and says so once.
  function loseSelection(elementId: string) {
    if (selectedElementId !== elementId) return;
    setSelectedElementId(undefined);
    setNotice('Selection cleared.');
  }
  const refreshPreview = () => setRefresh((value) => value + 1);

  function goToPage(next: string | undefined) {
    if (!next) return;
    setSelectedElementId(undefined);
    setPicked(null);
    setSelection({ resourceId, pageId: next });
  }

  function pickPaletteItem(item: DesignerPalette['items'][number]) {
    if (!current || palette?.resource_revision !== current.resource_revision)
      return;
    try {
      if (item.category === 'page') goToPage(item.identity);
      else {
        if (!onDraftText) throw new Error('Designer tools are unavailable.');
        onDraftText(item.prefill);
      }
      setPaletteOpen(false);
      setPaletteQuery('');
      setPalette(null);
    } catch (cause) {
      setPaletteError(failureText(cause));
    }
  }

  function openSide(view: Side | null) {
    setSide({ resourceId, view });
    if (view) setPresenting(false);
  }

  function openInspector(view: InspectorView) {
    if (view === 'review' || view === 'history') setInspectorMode(view);
    else {
      setInspectorMode('panes');
      setPane(view);
    }
    openSide('inspector');
  }

  /** A toolbar button opens its view, or closes it when it is showing. */
  function toggleInspector(view: InspectorView) {
    const panes = ['selection', 'brand', 'library'];
    if (
      showing === view ||
      (panes.includes(view) && panes.includes(showing ?? ''))
    )
      openSide(null);
    else openInspector(view === 'selection' ? pane : view);
  }

  function setMode(next: 'preview' | 'edit') {
    setAuthoring(next === 'edit');
    setPicked(null);
    // The inspector sits beside the canvas, or as a half-height sheet under
    // it on a narrow panel, so the page stays in view either way.
    if (next === 'edit') openInspector(pane);
    else if (sideView === 'inspector') openSide(null);
  }

  function clearSelection() {
    setSelectedElementId(undefined);
    setPicked(null);
  }

  // Dragging the sheet's handle sizes it; letting go settles at half or full.
  function dragSheet(event: ReactPointerEvent<HTMLButtonElement>) {
    const area = body.current?.getBoundingClientRect();
    if (!area || !area.height) return;
    const handle = event.currentTarget;
    const start = event.clientY;
    dragged.current = false;
    handle.setPointerCapture?.(event.pointerId);
    const height = (y: number) =>
      Math.min(area.height, Math.max(120, area.bottom - y));
    const move = (moved: PointerEvent) => {
      if (Math.abs(moved.clientY - start) > 4) dragged.current = true;
      if (dragged.current) setSheetDrag(height(moved.clientY));
    };
    const end = (ended: PointerEvent) => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', end);
      handle.removeEventListener('pointercancel', end);
      if (dragged.current)
        setSheet(height(ended.clientY) > area.height * 0.75 ? 'full' : 'half');
      setSheetDrag(null);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  }

  // ------------------------------------------------ undo, redo and rename
  const chainRevision = current?.resource_revision ?? '';
  const canHistory = !!edit && !!loadEditing && !!current;
  const redoId = redoTarget(chain, chainRevision);

  async function restoreStep(kind: 'undo' | 'redo') {
    if (!current || !edit || !loadEditing || historyOperation.current) return;
    historyOperation.current = true;
    setHistoryBusy(true);
    setNotice('');
    const revision = current.resource_revision;
    const page = current.page_id;
    const abort = new AbortController();
    try {
      let target: string | null;
      if (kind === 'undo') {
        const editing = await loadEditing(
          { pageId: page, limit: 25 },
          abort.signal,
        );
        if (
          editing.resource_id !== resourceId ||
          editing.resource_revision !== revision
        )
          throw { code: 'resource_revision_conflict' };
        target = undoTarget(chain, editing.history, revision);
        if (!target) {
          setNotice('Nothing to undo yet.');
          return;
        }
      } else target = redoTarget(chain, revision);
      if (!target) return;
      const receipt = await edit(
        { operation: 'restore', snapshot_id: target },
        revision,
      );
      if (
        receipt.status !== 'completed' ||
        receipt.resource_id !== resourceId ||
        !receipt.resource_revision
      )
        throw { code: receipt.code ?? 'save_incomplete' };
      const next = receipt.resource_revision;
      if (kind === 'undo') {
        let created: string | null = null;
        try {
          const after = await loadEditing(
            { pageId: page, limit: 1 },
            abort.signal,
          );
          if (after.resource_revision === next)
            created = after.history[0]?.id ?? null;
        } catch {
          created = null;
        }
        setChain((value) => afterUndo(value, revision, target, next, created));
        setNotice('Undone.');
      } else {
        setChain((value) => (value ? afterRedo(value, next) : null));
        setNotice('Redone.');
      }
      setPicked(null);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setNotice(historyFailure(reason));
      setRefresh((value) => value + 1);
    } finally {
      historyOperation.current = false;
      setHistoryBusy(false);
    }
  }
  shortcut.current = (kind) => void restoreStep(kind);

  const displayName =
    renamed && renamed.resourceId === resourceId && renamed.base === title
      ? renamed.name
      : (title ?? 'Design');
  async function rename(value: string) {
    const name = value.trim();
    setNameDraft(null);
    if (!name || name === displayName || !edit || !current || nameSaving)
      return;
    setNameSaving(true);
    try {
      const receipt = await edit(
        { operation: 'project_properties', name },
        current.resource_revision,
      );
      if (receipt.status !== 'completed' || !receipt.resource_revision)
        throw { code: receipt.code ?? 'save_incomplete' };
      setRenamed({ resourceId, base: title, name });
      setNotice('Renamed.');
      setRefresh((count) => count + 1);
    } catch (reason) {
      const code =
        typeof reason === 'object' && reason !== null && 'code' in reason
          ? String(reason.code)
          : '';
      setNotice(
        code === 'resource_revision_conflict'
          ? 'The design changed meanwhile, so the name is unchanged. Try again.'
          : 'The new name was not saved. The design keeps its name.',
      );
    } finally {
      setNameSaving(false);
    }
  }

  async function changeStructure(
    payload: Parameters<NonNullable<typeof edit>>[0],
    after: { pageId?: string; notice: string; undo?: boolean },
  ) {
    if (!edit || !current || structureBusy || historyOperation.current) return;
    setStructureBusy(true);
    setNotice('');
    setUndoOffer(false);
    try {
      const receipt = await edit(payload, current.resource_revision);
      if (receipt.status !== 'completed' || !receipt.resource_revision)
        throw { code: receipt.code ?? 'save_incomplete' };
      setPicked(null);
      setSelectedElementId(undefined);
      // Without a page the preview opens the one the server selected (a
      // new page is selected when it is added).
      setSelection({ resourceId, pageId: after.pageId });
      setNotice(after.notice);
      setUndoOffer(!!after.undo);
    } catch (reason) {
      setNotice(structureFailure(reason));
    } finally {
      setStructureBusy(false);
      setRefresh((value) => value + 1);
    }
  }

  async function duplicateDesign() {
    if (!duplicate || structureBusy) return;
    setStructureBusy(true);
    setNotice('');
    setUndoOffer(false);
    try {
      await duplicate();
      setNotice('Made a copy. It opens beside this design.');
    } catch {
      setNotice('The copy was not made. This design is unchanged.');
    } finally {
      setStructureBusy(false);
    }
  }

  function addPage() {
    if (!current) return;
    void changeStructure(
      { operation: 'page_add', page_id: current.page_id },
      { notice: `Added a ${pageLabel.toLowerCase()}.` },
    );
  }

  function deletePage() {
    if (!current || current.page_count < 2) return;
    const neighbour =
      current.pages[current.page_index + 1] ??
      current.pages[current.page_index - 1];
    void changeStructure(
      { operation: 'page_delete', page_id: current.page_id },
      {
        pageId: neighbour?.id,
        notice: `Deleted “${current.page_title}”.`,
        undo: true,
      },
    );
  }

  function resize(ratio: DesignSize) {
    if (!current) return;
    void changeStructure(
      { operation: 'canvas_size', aspect_ratio: ratio },
      {
        pageId: current.page_id,
        notice: `Changed the size to ${ratio}. Every ${pageLabel.toLowerCase()} was re-fitted.`,
        undo: true,
      },
    );
  }

  function ask(instruction: string): AskOutcome {
    if (!pickedCurrent || !current) return 'unavailable';
    const text = askText(
      pickedCurrent,
      {
        label: pageLabel,
        number: current.page_index + 1,
        title: current.page_title,
      },
      instruction,
    );
    const outcome = onAsk?.(text) ?? 'unavailable';
    if (outcome !== 'unavailable') return outcome;
    if (!onDraftText) return 'unavailable';
    try {
      onDraftText(text);
      return 'drafted';
    } catch {
      return 'unavailable';
    }
  }

  if (!visible) return null;
  const responsive =
    !!current && ['landing', 'app_mockup'].includes(current.mode);
  const deviceMode =
    responsive && device.resourceId === resourceId ? device.value : 'desktop';
  const canvasWidth =
    current && deviceMode !== 'desktop'
      ? DEVICES[deviceMode][0]
      : (current?.canvas_width ?? 1);
  const canvasHeight =
    current && deviceMode !== 'desktop'
      ? DEVICES[deviceMode][1]
      : (current?.canvas_height ?? 1);
  const fitScale = Math.min(
    viewport.width / canvasWidth,
    viewport.height / canvasHeight,
  );
  const scale = current
    ? zoomMode === 'fit'
      ? fitScale
      : zoomMode === 'width'
        ? viewport.width / canvasWidth
        : zoomMode === 'actual'
          ? 1
          : Number(zoomMode) || 1
    : 1;
  const offset = {
    left: Math.max(0, (viewport.width - canvasWidth * scale) / 2),
    top: Math.max(0, (viewport.height - canvasHeight * scale) / 2),
  };
  const presentReady =
    !loading && !!current && current.resource_revision === resourceRevision;
  const canPresent = !!presentation && lifecycleState.available('presentation');
  const canExport =
    !!createExport && !!downloadExport && lifecycleState.available('export');
  const canShare = !!sharing && lifecycleState.available('sharing');
  const editorRevision = current?.resource_revision ?? resourceRevision;
  const hasInspector = (!!loadEditing && !!edit) || !!design;
  const pages = current?.pages ?? [];
  const canStructure = !!edit && !!current && !loading && !structureBusy;
  const sized =
    !!current && ['deck', 'document', 'storyboard'].includes(current.mode);
  const size = current
    ? designSize(current.canvas_width, current.canvas_height)
    : undefined;
  const canVersions = !!loadEditing && !!edit;
  const reviewLabel = suggestionCount
    ? `Review · ${suggestionCount} ${suggestionCount === 1 ? 'suggestion' : 'suggestions'}`
    : 'Review';
  const moreActions = [
    // A phone-width toolbar keeps these here instead.
    ...(compact && canVersions
      ? [{ label: 'Versions', onSelect: () => openInspector('history') }]
      : []),
    ...(compact && design
      ? [{ label: reviewLabel, onSelect: () => openInspector('review') }]
      : []),
    ...(compact && hasInspector
      ? [{ label: 'Inspector', onSelect: () => openInspector(pane) }]
      : []),
    ...(loadPalette && onDraftText && current
      ? [
          {
            label: 'Search design tools, pages & assets',
            separatorBefore: compact,
            disabled: loading,
            onSelect: () => setPaletteOpen(true),
          },
        ]
      : []),
    ...(design
      ? [{ label: 'Library', onSelect: () => openInspector('library') }]
      : []),
    ...(design && current && ['deck', 'document'].includes(current.mode)
      ? [{ label: 'Import document', onSelect: () => setImportOpen(true) }]
      : []),
    ...(duplicate && current
      ? [
          {
            label: 'Duplicate design',
            separatorBefore: true,
            disabled: structureBusy || loading,
            onSelect: () => void duplicateDesign(),
          },
        ]
      : []),
    ...(lifecycle
      ? [
          {
            label: 'Design capabilities',
            separatorBefore: true,
            onSelect: () => undefined,
            // Opens once the menu has closed and let go of focus.
            afterClose: () => setCapabilitiesOpen(true),
          },
        ]
      : []),
  ];
  const modeSwitch = loadEditing && edit && (
    <Segmented
      size="sm"
      label="Design mode"
      value={authoring ? 'edit' : 'preview'}
      onChange={setMode}
      options={[
        {
          value: 'preview',
          label: 'Preview',
          icon: <Eye size={14} aria-hidden />,
        },
        {
          value: 'edit',
          label: 'Edit',
          icon: <MousePointer2 size={14} aria-hidden />,
        },
      ]}
    />
  );
  const moreMenu = moreActions.length > 0 && (
    <Menu
      label="More design actions"
      iconOnly
      variant="ghost"
      triggerRef={moreButton}
      actions={moreActions}
    >
      <MoreHorizontal size={15} aria-hidden />
    </Menu>
  );
  const sheetStyle = {
    '--design-sheet':
      sheetDrag !== null ? `${sheetDrag}px` : sheet === 'full' ? '100%' : '50%',
  } as CSSProperties;
  const panes = [
    ...(hasInspector ? [{ id: 'selection' as const, label: 'Selection' }] : []),
    ...(design && current
      ? [
          { id: 'brand' as const, label: 'Brand' },
          { id: 'library' as const, label: 'Library' },
        ]
      : []),
  ];
  const designProps = design &&
    current && {
      ...design,
      resourceRevision: current.resource_revision,
      pageId: current.page_id,
      selectedElementId,
      onSelectElement: setSelectedElementId,
      onSelectionLost: loseSelection,
      onReload: refreshPreview,
      visible,
    };
  const textEditor = loadEditing && edit && selectedElementId && (
    <ArtifactEditor
      view="text"
      resourceId={resourceId}
      resourceRevision={editorRevision}
      visible={visible}
      pageId={current?.page_id}
      selectedElementId={selectedElementId}
      onSelectionLost={loseSelection}
      onPageChange={goToPage}
      load={loadEditing}
      edit={edit}
      onEdited={refreshPreview}
    />
  );
  const zoomLabel = (value: string) =>
    value === 'fit'
      ? `Fit · ${Math.round(fitScale * 100)}%`
      : value === 'width'
        ? 'Fit width'
        : value === 'actual'
          ? '100%'
          : `${Math.round(Number(value) * 100)}%`;
  return (
    <section
      ref={panel}
      aria-label="Design preview"
      aria-busy={loading}
      className="design-panel"
      data-mode={authoring ? 'edit' : 'preview'}
      onKeyDown={(event) => {
        const target = event.target as HTMLElement;
        if (
          !(event.ctrlKey || event.metaKey) ||
          event.key.toLowerCase() !== 'z' ||
          target.closest('input, textarea, select, [contenteditable="true"]')
        )
          return;
        event.preventDefault();
        void restoreStep(event.shiftKey ? 'redo' : 'undo');
      }}
    >
      <header className="design-topbar">
        <div className="design-topbar-title">
          <input
            className="design-name"
            aria-label="Design name"
            value={nameDraft ?? displayName}
            readOnly={!edit || !current}
            maxLength={200}
            title={displayName}
            onChange={(event) => setNameDraft(event.target.value)}
            onBlur={(event) => void rename(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') event.currentTarget.blur();
              if (event.key === 'Escape') {
                event.preventDefault();
                setNameDraft(null);
                event.currentTarget.blur();
              }
            }}
          />
          {!compact && modeSwitch}
        </div>
        <Toolbar label="Design actions" className="design-topbar-actions">
          {compact && modeSwitch}
          {canHistory && (
            <span className="design-tool-group">
              <IconButton
                size="sm"
                label="Undo"
                shortcut="Mod+Z"
                disabled={historyBusy || loading}
                onClick={() => void restoreStep('undo')}
              >
                <Undo2 size={15} aria-hidden />
              </IconButton>
              <IconButton
                size="sm"
                label="Redo"
                shortcut="Mod+Shift+Z"
                disabled={historyBusy || loading || !redoId}
                onClick={() => void restoreStep('redo')}
              >
                <Redo2 size={15} aria-hidden />
              </IconButton>
            </span>
          )}
          {!compact && (canVersions || design || hasInspector) && (
            <span className="design-tool-group">
              {canVersions && (
                <IconButton
                  size="sm"
                  label="Versions"
                  pressed={showing === 'history'}
                  onClick={() => toggleInspector('history')}
                >
                  <History size={15} aria-hidden />
                </IconButton>
              )}
              {design && (
                <IconButton
                  size="sm"
                  label={reviewLabel}
                  pressed={showing === 'review'}
                  onClick={() => toggleInspector('review')}
                >
                  <ListChecks size={15} aria-hidden />
                  {suggestionCount > 0 && (
                    <span className="design-tool-dot" aria-hidden />
                  )}
                </IconButton>
              )}
              {hasInspector && (
                <IconButton
                  size="sm"
                  label="Inspector"
                  pressed={
                    showing === 'selection' ||
                    showing === 'brand' ||
                    showing === 'library'
                  }
                  onClick={() => toggleInspector('selection')}
                >
                  <PanelRight size={15} aria-hidden />
                </IconButton>
              )}
            </span>
          )}
          {!compact && (presentation || sharing || createExport) && (
            <span className="design-tool-group">
              {presentation && (
                <IconButton
                  ref={presentButton}
                  size="sm"
                  label="Present"
                  pressed={presenting}
                  disabled={!canPresent || (!presenting && !presentReady)}
                  onClick={present}
                >
                  <Play size={15} aria-hidden />
                </IconButton>
              )}
              {sharing && (
                <IconButton
                  size="sm"
                  label="Share"
                  pressed={sideView === 'share'}
                  disabled={!canShare}
                  onClick={() =>
                    openSide(sideView === 'share' ? null : 'share')
                  }
                >
                  <Share2 size={15} aria-hidden />
                </IconButton>
              )}
              {createExport && downloadExport && (
                <IconButton
                  size="sm"
                  label="Export"
                  pressed={sideView === 'export'}
                  disabled={!canExport}
                  onClick={() =>
                    openSide(sideView === 'export' ? null : 'export')
                  }
                >
                  <Download size={15} aria-hidden />
                </IconButton>
              )}
            </span>
          )}
          {compact && (presentation || sharing || createExport) && (
            <Menu
              label="Share or export"
              iconOnly
              variant="ghost"
              triggerRef={presentButton}
              actions={[
                ...(presentation
                  ? [
                      {
                        label: 'Present',
                        disabled: !canPresent || (!presenting && !presentReady),
                        onSelect: present,
                        // The presentation takes the keyboard: the closing
                        // menu gives focus to it, not back to its trigger
                        // (Escape and arrows went nowhere, B247).
                        afterClose: () =>
                          stage.current
                            ?.querySelector<HTMLElement>(
                              '.artifact-presentation',
                            )
                            ?.focus({ preventScroll: true }),
                      },
                    ]
                  : []),
                ...(sharing
                  ? [
                      {
                        label: 'Share…',
                        disabled: !canShare,
                        onSelect: () => openSide('share'),
                      },
                    ]
                  : []),
                ...(createExport && downloadExport
                  ? [
                      {
                        label: 'Export…',
                        disabled: !canExport,
                        onSelect: () => openSide('export'),
                      },
                    ]
                  : []),
              ]}
            >
              <Share2 size={15} aria-hidden />
            </Menu>
          )}
          {lifecycle && moreMenu ? (
            <DesignCapabilities
              lifecycle={lifecycleState}
              open={capabilitiesOpen}
              onOpenChange={setCapabilitiesOpen}
              returnFocus={() => moreButton.current}
            >
              <span className="design-more-anchor">{moreMenu}</span>
            </DesignCapabilities>
          ) : (
            moreMenu
          )}
        </Toolbar>
      </header>
      <ModalTask
        open={
          paletteOpen && visible && Boolean(current) && Boolean(onDraftText)
        }
        onOpenChange={setPaletteOpen}
        title="Design command palette"
        description="Search this design's tools, pages, and assets. Selecting a tool or asset fills the conversation draft."
      >
        <input
          className="input"
          type="search"
          aria-label="Search design tools, pages, assets"
          autoFocus
          maxLength={128}
          value={paletteQuery}
          onChange={(event) => setPaletteQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && palette?.items[0]) {
              event.preventDefault();
              pickPaletteItem(palette.items[0]);
            }
          }}
        />
        {paletteError && <p role="alert">{paletteError}</p>}
        {!palette && !paletteError && <p role="status">Searching design…</p>}
        {palette && (
          <>
            {!palette.tools_available && (
              <p role="status">
                Designer tools are unavailable; pages and assets remain
                searchable.
              </p>
            )}
            <div
              className="stack"
              role="list"
              aria-label="Design command results"
            >
              {palette.items.map((item) => (
                <div role="listitem" key={`${item.category}:${item.identity}`}>
                  <Button onClick={() => pickPaletteItem(item)}>
                    {item.label} <small>{item.category}</small>
                  </Button>
                </div>
              ))}
            </div>
            {!palette.items.length && <p>No matches.</p>}
            {palette.has_more_matches && (
              <p>Refine your search to see more results.</p>
            )}
          </>
        )}
      </ModalTask>
      {design && current && ['deck', 'document'].includes(current.mode) && (
        <ArtifactDocumentImport
          open={importOpen}
          onOpenChange={setImportOpen}
          session={design.session}
          resourceId={resourceId}
          resourceRevision={current.resource_revision}
          onImported={() => setRefresh((value) => value + 1)}
        />
      )}
      <div ref={body} className="design-body" data-side={sideView ?? undefined}>
        {!presenting && pages.length > 1 && current && (
          <DesignPageStrip
            pages={pages}
            currentId={current.page_id}
            pageLabel={pageLabel}
            disabled={loading}
            onSelect={goToPage}
            aspect={current.canvas_width / current.canvas_height}
            renderThumbnail={
              presentation && typeof IntersectionObserver !== 'undefined'
                ? (id) => (
                    <StaticDesignPage
                      resourceId={resourceId}
                      resourceRevision={current.resource_revision}
                      pageId={id}
                      preview={presentation.preview}
                      thumbnail
                    />
                  )
                : undefined
            }
          />
        )}
        <div className="design-stage" ref={stage}>
          {presenting && presentation && current ? (
            <ArtifactPresentationPanel
              {...presentation}
              resourceId={resourceId}
              resourceRevision={current.resource_revision}
              visible={visible}
              autoStart
              fullscreen={fullscreen}
              startIndex={current.page_index}
              // Back to where presenting started.
              onEnded={() => stopPresenting(true)}
              // The presenter carries on in the panel.
              onAudience={() => {
                enteredFullscreen.current = false;
              }}
            />
          ) : (
            <>
              {error && (
                <ErrorState
                  title="Preview unavailable"
                  action={<Button onClick={reload}>Reload preview</Button>}
                >
                  {error}
                </ErrorState>
              )}
              {loading && !current && (
                <Skeleton label="Loading design preview" />
              )}
              {current?.html && (
                <>
                  <p aria-live="polite" className="visually-hidden">
                    {pageLabel} {current.page_index + 1} of {current.page_count}
                    : {current.page_title}
                  </p>
                  <div
                    ref={frameHost}
                    className="design-canvas"
                    // A zoomed or width-fitted page scrolls; keyboard users
                    // reach and scroll it too.
                    {...(zoomMode === 'fit'
                      ? {}
                      : {
                          tabIndex: 0,
                          role: 'group',
                          'aria-label': `${pageLabel} canvas`,
                        })}
                    style={{
                      width: '100%',
                      flex: '1 1 auto',
                      minHeight: 180,
                      position: 'relative',
                      overflow: zoomMode === 'fit' ? 'clip' : 'auto',
                    }}
                  >
                    <div
                      style={{
                        width: canvasWidth * scale,
                        height: canvasHeight * scale,
                      }}
                    >
                      <iframe
                        ref={frame}
                        className="design-frame"
                        title={`${pageLabel} preview: ${current.page_title}`}
                        sandbox={interactive ? 'allow-scripts' : ''}
                        referrerPolicy="no-referrer"
                        srcDoc={current.html}
                        style={{
                          width: canvasWidth,
                          height: canvasHeight,
                          position: 'absolute',
                          left: offset.left,
                          top: offset.top,
                          border: 0,
                          transform: `scale(${scale})`,
                          transformOrigin: 'top left',
                        }}
                      />
                    </div>
                    {pickedCurrent && (
                      <DesignSelection
                        selection={pickedCurrent}
                        scale={scale}
                        offset={offset}
                        viewport={viewport}
                        bounds={{
                          width: Math.max(
                            viewport.width,
                            offset.left + canvasWidth * scale,
                          ),
                          height: Math.max(
                            viewport.height,
                            offset.top + canvasHeight * scale,
                          ),
                        }}
                        onAsk={ask}
                        onClose={() => setPicked(null)}
                      />
                    )}
                  </div>
                </>
              )}
              {drafting ? (
                <span
                  id="design-preview-refresh-status"
                  className="design-updating design-drafting"
                  role="status"
                >
                  <span className="design-drafting-dot" aria-hidden />
                  Drafting · {drafting.label}…
                </span>
              ) : (
                loading &&
                current && (
                  <span
                    id="design-preview-refresh-status"
                    className="design-updating"
                    role="status"
                  >
                    Updating preview…
                  </span>
                )
              )}
              {(notice || (authoring && !pickedCurrent && current)) && (
                <p className="design-hint" role="status">
                  {notice ||
                    'Click an element to ask Row-Bot about it. Double-click text to edit it.'}
                  {notice && undoOffer && canHistory && (
                    <Button
                      variant="ghost"
                      className="design-hint-action"
                      aria-label="Undo this change"
                      disabled={historyBusy || structureBusy}
                      onClick={() => {
                        setUndoOffer(false);
                        void restoreStep('undo');
                      }}
                    >
                      Undo
                    </Button>
                  )}
                </p>
              )}
              <Toolbar label="Design preview controls" className="design-dock">
                <IconButton
                  size="sm"
                  label={`Previous ${pageLabel.toLowerCase()}`}
                  disabled={!current || loading || current.page_index === 0}
                  onClick={() =>
                    goToPage(current?.pages[current.page_index - 1]?.id)
                  }
                >
                  <ChevronLeft size={15} aria-hidden />
                </IconButton>
                <Menu
                  label={
                    current
                      ? `${pageLabel} ${current.page_index + 1} of ${current.page_count}: ${current.page_title}. Choose a ${pageLabel.toLowerCase()}`
                      : `Choose a ${pageLabel.toLowerCase()}`
                  }
                  variant="ghost"
                  className="design-dock-page"
                  disabled={!current || loading}
                  actions={[
                    ...(current?.pages ?? []).map((page) => ({
                      label: `${page.index + 1}. ${page.title}`,
                      selected: page.id === current?.page_id,
                      onSelect: () => goToPage(page.id),
                    })),
                    ...(edit && current
                      ? [
                          {
                            label: `Add a ${pageLabel.toLowerCase()} after this one`,
                            icon: <Plus size={16} aria-hidden />,
                            separatorBefore: true,
                            disabled: !canStructure,
                            onSelect: addPage,
                          },
                          ...(current.page_count > 1
                            ? [
                                {
                                  label: `Delete this ${pageLabel.toLowerCase()}`,
                                  icon: <Trash2 size={16} aria-hidden />,
                                  danger: true,
                                  disabled: !canStructure,
                                  onSelect: deletePage,
                                },
                              ]
                            : []),
                        ]
                      : []),
                  ]}
                >
                  <span aria-hidden>
                    {current
                      ? `${current.page_index + 1} / ${current.page_count}`
                      : '–'}
                  </span>
                </Menu>
                <IconButton
                  size="sm"
                  label={`Next ${pageLabel.toLowerCase()}`}
                  disabled={
                    !current ||
                    loading ||
                    current.page_index >= current.page_count - 1
                  }
                  onClick={() =>
                    goToPage(current?.pages[current.page_index + 1]?.id)
                  }
                >
                  <ChevronRight size={15} aria-hidden />
                </IconButton>
                {sized && edit && current && (
                  <>
                    <ToolbarSeparator />
                    <Menu
                      label={`Size: ${size?.ratio ?? 'custom'}. Change the size`}
                      variant="ghost"
                      className="design-dock-size"
                      disabled={!canStructure}
                      actions={DESIGN_SIZES.map((option) => ({
                        label: option.label,
                        selected: option.ratio === size?.ratio,
                        onSelect: () => {
                          if (option.ratio !== size?.ratio)
                            resize(option.ratio);
                        },
                      }))}
                    >
                      <span aria-hidden>{size?.ratio ?? 'Custom'}</span>
                    </Menu>
                  </>
                )}
                <ToolbarSeparator />
                <Select
                  aria-label="Preview zoom"
                  className="design-dock-zoom"
                  value={zoomMode}
                  onChange={(event) =>
                    setZoom({ resourceId, value: event.target.value })
                  }
                >
                  {ZOOMS.map((value) => (
                    <option key={value} value={value}>
                      {zoomLabel(value)}
                    </option>
                  ))}
                </Select>
                {responsive && (
                  <>
                    <ToolbarSeparator />
                    <Segmented
                      size="sm"
                      label="Device width"
                      value={deviceMode}
                      onChange={(value) => setDevice({ resourceId, value })}
                      options={[
                        {
                          value: 'desktop',
                          label: 'Desktop',
                          hideLabel: true,
                          icon: <Monitor size={14} aria-hidden />,
                        },
                        {
                          value: 'tablet',
                          label: 'Tablet',
                          hideLabel: true,
                          icon: <Tablet size={14} aria-hidden />,
                        },
                        {
                          value: 'phone',
                          label: 'Phone',
                          hideLabel: true,
                          icon: <Smartphone size={14} aria-hidden />,
                        },
                      ]}
                    />
                  </>
                )}
              </Toolbar>
            </>
          )}
        </div>
        {sideView && (
          <aside
            className="design-side"
            data-sheet={sheet}
            style={sheetStyle}
            aria-label={
              sideView === 'inspector'
                ? 'Design inspector'
                : sideView === 'export'
                  ? 'Export design'
                  : 'Share design'
            }
          >
            {/* A narrow panel shows this side as a sheet: drag or press to size it. */}
            <button
              type="button"
              className="design-sheet-grab"
              aria-label={
                sheet === 'full'
                  ? 'Shrink to half height'
                  : 'Expand to full height'
              }
              onPointerDown={dragSheet}
              onClick={() => {
                if (!dragged.current)
                  setSheet((value) => (value === 'full' ? 'half' : 'full'));
                dragged.current = false;
              }}
            >
              <span aria-hidden />
            </button>
            {showing && inspectorMode === 'panes' ? (
              <TabsPrimitive.Root
                className="design-inspector"
                value={pane}
                onValueChange={(value) => setPane(value as Pane)}
              >
                <header className="design-side-header design-side-tabs">
                  <TabsPrimitive.List
                    className="design-tabs"
                    aria-label="Inspector"
                  >
                    {panes.map((item) => (
                      <TabsPrimitive.Trigger
                        key={item.id}
                        value={item.id}
                        className="design-tab"
                      >
                        {item.label}
                      </TabsPrimitive.Trigger>
                    ))}
                  </TabsPrimitive.List>
                  <IconButton
                    size="sm"
                    label="Close inspector"
                    onClick={() => openSide(null)}
                  >
                    <X size={15} aria-hidden />
                  </IconButton>
                </header>
                <div className="design-side-body">
                  <TabsPrimitive.Content
                    value="selection"
                    className="design-pane"
                  >
                    {selectedElementId ? (
                      designProps ? (
                        <ArtifactDesignPanel
                          {...designProps}
                          view="selection"
                          look={
                            look?.elementId === selectedElementId
                              ? look.values
                              : undefined
                          }
                          textEditor={textEditor}
                          onClearSelection={clearSelection}
                        />
                      ) : (
                        textEditor
                      )
                    ) : (
                      <>
                        <header className="design-what">
                          <span className="design-what-tile" data-kind="page">
                            <PageGlyph size={16} aria-hidden />
                          </span>
                          <span className="design-what-text">
                            <strong>This {pageLabel.toLowerCase()}</strong>
                            {current && (
                              <small>
                                {pageLabel} {current.page_index + 1} of{' '}
                                {current.page_count} · {current.page_title}
                              </small>
                            )}
                          </span>
                        </header>
                        <p className="design-hint-line">
                          <MousePointer2 size={14} aria-hidden />
                          <span>
                            {authoring
                              ? 'Click anything on the page to change it, or ask Row-Bot about it.'
                              : 'Choose Edit, then click anything on the page to change it.'}
                          </span>
                        </p>
                        {loadEditing && edit && (
                          <ArtifactEditor
                            view="page"
                            resourceId={resourceId}
                            resourceRevision={editorRevision}
                            visible={visible}
                            pageId={current?.page_id}
                            onSelectionLost={loseSelection}
                            onPageChange={goToPage}
                            load={loadEditing}
                            edit={edit}
                            generateNotes={design?.session.generateNotes}
                            onEdited={refreshPreview}
                            pageExtras={
                              sized &&
                              current && (
                                <DesignRow label="Size">
                                  <Menu
                                    label={`Page size: ${size?.label ?? 'Custom'}. Change the size`}
                                    className="design-size-picker"
                                    disabled={!canStructure}
                                    actions={DESIGN_SIZES.map((option) => ({
                                      label: option.label,
                                      selected: option.ratio === size?.ratio,
                                      onSelect: () => {
                                        if (option.ratio !== size?.ratio)
                                          resize(option.ratio);
                                      },
                                    }))}
                                  >
                                    <span aria-hidden>
                                      {size?.label ?? 'Custom size'}
                                    </span>
                                  </Menu>
                                </DesignRow>
                              )
                            }
                          />
                        )}
                        {designProps && (
                          <ArtifactDesignPanel
                            {...designProps}
                            view="selection"
                          />
                        )}
                      </>
                    )}
                  </TabsPrimitive.Content>
                  {designProps && (
                    <>
                      <TabsPrimitive.Content
                        value="brand"
                        className="design-pane"
                      >
                        <ArtifactDesignPanel {...designProps} view="brand" />
                      </TabsPrimitive.Content>
                      <TabsPrimitive.Content
                        value="library"
                        className="design-pane"
                      >
                        <ArtifactDesignPanel {...designProps} view="library" />
                      </TabsPrimitive.Content>
                    </>
                  )}
                </div>
                <footer className="design-side-foot">
                  <Check size={13} aria-hidden />
                  <span>Changes save as you go</span>
                  {canHistory && <Kbd keys="Mod+Z" />}
                </footer>
              </TabsPrimitive.Root>
            ) : (
              <>
                <header className="design-side-header">
                  <h3>
                    {showing === 'review'
                      ? 'Review'
                      : showing === 'history'
                        ? 'Versions'
                        : sideView === 'export'
                          ? 'Export'
                          : 'Share'}
                  </h3>
                  <IconButton
                    size="sm"
                    label={
                      showing === 'review'
                        ? 'Close review'
                        : showing === 'history'
                          ? 'Close versions'
                          : sideView === 'export'
                            ? 'Close export'
                            : 'Close sharing'
                    }
                    onClick={() => openSide(null)}
                  >
                    <X size={15} aria-hidden />
                  </IconButton>
                </header>
                <div className="design-side-body">
                  {showing === 'review' && designProps && (
                    <div className="design-pane">
                      <ArtifactDesignPanel {...designProps} view="review" />
                    </div>
                  )}
                  {showing === 'history' && loadEditing && edit && (
                    <div className="design-pane">
                      <ArtifactEditor
                        view="history"
                        resourceId={resourceId}
                        resourceRevision={editorRevision}
                        visible={visible}
                        pageId={current?.page_id}
                        onPageChange={goToPage}
                        load={loadEditing}
                        edit={edit}
                        onEdited={() => {
                          setChain(null);
                          setRefresh((value) => value + 1);
                        }}
                      />
                    </div>
                  )}
                  {sideView === 'export' && createExport && downloadExport && (
                    <ArtifactExports
                      resourceId={resourceId}
                      resourceRevision={
                        current?.resource_revision ?? resourceRevision
                      }
                      visible={visible}
                      updating={loading}
                      currentPageIndex={current?.page_index ?? 0}
                      pageCount={current?.page_count ?? 0}
                      create={createExport}
                      download={downloadExport}
                      save={saveExport}
                      reveal={revealExport}
                    />
                  )}
                  {sideView === 'share' && sharing && (
                    <ArtifactSharingPanel
                      {...sharing}
                      resourceId={resourceId}
                      resourceRevision={
                        current?.resource_revision ?? resourceRevision
                      }
                      visible={visible}
                    />
                  )}
                </div>
              </>
            )}
          </aside>
        )}
      </div>
    </section>
  );
}
