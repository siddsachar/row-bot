import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  History,
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
  Menu,
  Segmented,
  Select,
  Skeleton,
  Tabs,
  Toolbar,
  ToolbarSeparator,
} from '../../ui/primitives';
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

type InspectorTab = 'properties' | 'library' | 'review' | 'history';
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

/** Panel width from which the inspector sits beside the canvas (panels.css). */
const SIDE_BY_SIDE = 720;
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
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('properties');
  const [presenting, setPresenting] = useState(false);
  const [authoring, setAuthoring] = useState(false);
  const [selectedElementId, setSelectedElementId] = useState<string>();
  const [picked, setPicked] = useState<DesignSelectionState | null>(null);
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
        if (message.elementId) setSelectedElementId(message.elementId);
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
      } else if (enteredFullscreen.current) {
        enteredFullscreen.current = false;
        setFullscreen(false);
        setPresenting(false);
        requestAnimationFrame(() => presentButton.current?.focus());
      }
    };
    document.addEventListener('fullscreenchange', changed);
    // Full screen may already have started before this listener existed.
    if (!!stage.current && document.fullscreenElement === stage.current)
      changed();
    return () => document.removeEventListener('fullscreenchange', changed);
  }, [presenting]);

  function present() {
    if (presenting) {
      enteredFullscreen.current = false;
      setFullscreen(false);
      setPresenting(false);
      if (document.fullscreenElement === stage.current)
        void document.exitFullscreen?.().catch(() => {});
      return;
    }
    setPresenting(true);
    setSide({ resourceId, view: null });
    // Inside the click, while the browser still counts it as the person's
    // gesture; where full screen is refused the presentation stays in the panel.
    void stage.current?.requestFullscreen?.().catch(() => {});
  }

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

  function openInspector(tab: InspectorTab) {
    setInspectorTab(tab);
    openSide('inspector');
  }

  function setMode(next: 'preview' | 'edit') {
    setAuthoring(next === 'edit');
    setPicked(null);
    // Beside the canvas the inspector helps; as a sheet over a narrow panel
    // it would cover what you are about to select, so there it waits.
    const width = panel.current?.getBoundingClientRect().width ?? 0;
    if (next === 'edit') {
      if (width >= SIDE_BY_SIDE) openInspector('properties');
    } else if (sideView === 'inspector') openSide(null);
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
  const moreActions = [
    ...(loadPalette && onDraftText && current
      ? [
          {
            label: 'Search design tools, pages & assets',
            disabled: loading,
            onSelect: () => setPaletteOpen(true),
          },
        ]
      : []),
    ...(design
      ? [
          {
            label: 'Insert blocks and assets',
            onSelect: () => openInspector('library'),
          },
          {
            label: 'Review design',
            onSelect: () => openInspector('review'),
          },
        ]
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
  ];
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
          {loadEditing && edit && (
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
          )}
        </div>
        <Toolbar label="Design actions" className="design-topbar-actions">
          {canHistory && (
            <>
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
              <ToolbarSeparator />
            </>
          )}
          {loadEditing && edit && (
            <IconButton
              size="sm"
              label="Design history"
              pressed={sideView === 'inspector' && inspectorTab === 'history'}
              onClick={() =>
                sideView === 'inspector' && inspectorTab === 'history'
                  ? openSide(null)
                  : openInspector('history')
              }
            >
              <History size={15} aria-hidden />
            </IconButton>
          )}
          {hasInspector && (
            <IconButton
              size="sm"
              label="Design properties"
              pressed={sideView === 'inspector' && inspectorTab !== 'history'}
              onClick={() =>
                sideView === 'inspector' && inspectorTab !== 'history'
                  ? openSide(null)
                  : openInspector(
                      loadEditing && edit ? 'properties' : 'library',
                    )
              }
            >
              <PanelRight size={15} aria-hidden />
            </IconButton>
          )}
          {(presentation || sharing || createExport) && <ToolbarSeparator />}
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
              onClick={() => openSide(sideView === 'share' ? null : 'share')}
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
              onClick={() => openSide(sideView === 'export' ? null : 'export')}
            >
              <Download size={15} aria-hidden />
            </IconButton>
          )}
          {lifecycle && <DesignCapabilities lifecycle={lifecycleState} />}
          {moreActions.length > 0 && (
            <Menu
              label="More design actions"
              iconOnly
              variant="ghost"
              actions={moreActions}
            >
              <MoreHorizontal size={15} aria-hidden />
            </Menu>
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
      <div className="design-body" data-side={sideView ?? undefined}>
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
              onEnded={() => {
                enteredFullscreen.current = false;
                if (document.fullscreenElement === stage.current)
                  void document.exitFullscreen?.().catch(() => {});
                setFullscreen(false);
                setPresenting(false);
                // Back to where presenting started.
                requestAnimationFrame(() => presentButton.current?.focus());
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
            aria-label={
              sideView === 'inspector'
                ? 'Design inspector'
                : sideView === 'export'
                  ? 'Export design'
                  : 'Share design'
            }
          >
            <header className="design-side-header">
              <h3>
                {sideView === 'inspector'
                  ? 'Inspector'
                  : sideView === 'export'
                    ? 'Export'
                    : 'Share'}
              </h3>
              <IconButton
                size="sm"
                label={
                  sideView === 'inspector'
                    ? 'Close inspector'
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
              {sideView === 'inspector' && (
                <Tabs
                  label="Inspector sections"
                  className="design-inspector-tabs"
                  value={inspectorTab}
                  onChange={(value) => setInspectorTab(value as InspectorTab)}
                  items={[
                    ...(hasInspector
                      ? [
                          {
                            id: 'properties',
                            label: 'Properties',
                            content: (
                              <div className="design-inspector-stack">
                                {loadEditing && edit && (
                                  <ArtifactEditor
                                    view="properties"
                                    resourceId={resourceId}
                                    resourceRevision={editorRevision}
                                    visible={visible}
                                    pageId={current?.page_id}
                                    selectedElementId={selectedElementId}
                                    onSelectElement={setSelectedElementId}
                                    onPageChange={goToPage}
                                    load={loadEditing}
                                    edit={edit}
                                    generateNotes={
                                      design?.session.generateNotes
                                    }
                                    onEdited={() =>
                                      setRefresh((value) => value + 1)
                                    }
                                  />
                                )}
                                {design && current && (
                                  <ArtifactDesignPanel
                                    {...design}
                                    view="properties"
                                    resourceRevision={current.resource_revision}
                                    pageId={current.page_id}
                                    selectedElementId={selectedElementId}
                                    onSelectElement={setSelectedElementId}
                                    visible={visible}
                                  />
                                )}
                              </div>
                            ),
                          },
                        ]
                      : []),
                    ...(design && current
                      ? [
                          {
                            id: 'library',
                            label: 'Library',
                            content: (
                              <ArtifactDesignPanel
                                {...design}
                                view="library"
                                resourceRevision={current.resource_revision}
                                pageId={current.page_id}
                                selectedElementId={selectedElementId}
                                onSelectElement={(id) => {
                                  setSelectedElementId(id);
                                  setInspectorTab('properties');
                                }}
                                visible={visible}
                              />
                            ),
                          },
                          {
                            id: 'review',
                            label: 'Review',
                            content: (
                              <ArtifactDesignPanel
                                {...design}
                                view="review"
                                resourceRevision={current.resource_revision}
                                pageId={current.page_id}
                                selectedElementId={selectedElementId}
                                onSelectElement={setSelectedElementId}
                                visible={visible}
                              />
                            ),
                          },
                        ]
                      : []),
                    ...(loadEditing && edit
                      ? [
                          {
                            id: 'history',
                            label: 'History',
                            content: (
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
                            ),
                          },
                        ]
                      : []),
                  ]}
                />
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
          </aside>
        )}
      </div>
    </section>
  );
}
