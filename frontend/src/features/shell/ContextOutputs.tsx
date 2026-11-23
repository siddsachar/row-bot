import { useEffect, useState } from 'react';
import {
  ChevronDown,
  Code2,
  Crosshair,
  FolderDown,
  Layers,
  MoreHorizontal,
  Palette,
  Play,
} from 'lucide-react';
import type {
  OutputVersionView,
  OutputView,
  ResourceView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  Hint,
  Menu,
  Toggle,
  type MenuAction,
} from '../../ui/primitives';
import { ATTACHMENT_LIMITS } from './attachment-limits';
import { openMediaViewer } from './MediaPreview';
import {
  OUTPUT_ICONS,
  ROLE_WORDS,
  outputTitle,
  useOutputs,
  versionWords,
} from './outputs';

/** Newest first; the rest fold under "Show all" (two rows of three). */
const FOLDED = 6;

/**
 * A small server thumbnail: a picture's, or a video's frame half a second in
 * (I20) under a play mark. Anything else, or without one, its kind's icon.
 */
function Thumb({ output }: { output: OutputView }) {
  const { controller } = useRuntime();
  const [src, setSrc] = useState('');
  const video = output.kind === 'video';
  const drawn = video || output.kind === 'image';
  useEffect(() => {
    setSrc('');
    if (!drawn) return;
    const abort = new AbortController();
    let url = '';
    controller.attachmentThumbnail(output.ref, abort.signal).then(
      (blob) => {
        if (abort.signal.aborted || !blob) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      },
      () => undefined,
    );
    return () => {
      abort.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [controller, drawn, output.ref]);
  const play = video && (
    <span className="context-output-play">
      <Play size={14} fill="currentColor" />
    </span>
  );
  if (src)
    return (
      <>
        <img className="context-output-thumb" src={src} alt="" />
        {play && (
          <span className="context-output-poster-mark" aria-hidden>
            {play}
          </span>
        )}
      </>
    );
  const Icon = OUTPUT_ICONS[output.kind];
  return (
    <span className="context-output-thumb context-output-glyph" aria-hidden>
      {play || <Icon size={20} />}
    </span>
  );
}

/** A version as the viewer and the menu name it: "Birthday montage · v2". */
function versionName(output: OutputView, version: OutputVersionView) {
  const title = outputTitle(output);
  return output.version_count > 1 ? `${title} · v${version.version}` : title;
}

function Tiles({
  label,
  outputs,
  busy,
  actions,
}: {
  label: string;
  outputs: OutputView[];
  busy: string;
  actions: (output: OutputView) => MenuAction[];
}) {
  const overlay = useOverlay();
  return (
    <ul className="context-output-grid" aria-label={label}>
      {outputs.map((output) => {
        const title = outputTitle(output);
        const version = versionWords(output);
        const hint = [output.file_name, version, ROLE_WORDS[output.role]]
          .filter(Boolean)
          .join(' · ');
        return (
          <li
            key={output.id}
            className="context-output-tile"
            data-busy={busy === output.ref ? 'true' : undefined}
          >
            <Hint label={hint}>
              <button
                type="button"
                className="context-output-open"
                aria-label={`Open ${title}`}
                aria-haspopup="dialog"
                onClick={() =>
                  openMediaViewer(overlay, {
                    reference: output.ref,
                    mime: output.mime_type,
                    name: versionName(output, output),
                    size: output.size,
                  })
                }
              >
                <span className="context-output-frame">
                  <Thumb output={output} />
                  {(output.role === 'draft' || output.version_count > 1) && (
                    <span className="context-output-badges" aria-hidden>
                      {output.role === 'draft' && (
                        <span className="context-output-badge">Draft</span>
                      )}
                      {output.version_count > 1 && (
                        <span className="context-output-badge">
                          <Layers size={11} />
                          {output.version_count}
                        </span>
                      )}
                    </span>
                  )}
                </span>
                <span className="context-output-label" aria-hidden>
                  {title}
                </span>
              </button>
            </Hint>
            <Menu
              label={`Actions for ${title}`}
              iconOnly
              variant="ghost"
              className="icon-action icon-action-sm context-output-menu"
              actions={actions(output)}
            >
              <MoreHorizontal size={16} aria-hidden />
            </Menu>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The conversation's outputs (W3), from the one server list: a tile per
 * thing made, newest first, showing its final or else its latest draft,
 * with its versions in its ⋯ menu. Working files show behind a toggle. A
 * tile opens the media viewer; its menu shows it in the conversation, saves
 * it to the workspace or hands it on.
 */
export default function ContextOutputs({
  conversationId,
  conversationRevision,
  outputs,
  workingCount,
  refresh,
  resources,
  onUseOutputInCode,
}: {
  conversationId: string;
  conversationRevision: string;
  outputs: OutputView[];
  workingCount: number;
  /** Changes when the conversation may have made something new. */
  refresh: string;
  resources: ResourceView[];
  onUseOutputInCode?: (output: { reference: string; mime: string }) => void;
}) {
  const { controller, artifactDesignSessions } = useRuntime();
  const overlay = useOverlay();
  const [expanded, setExpanded] = useState(false);
  const [showWorking, setShowWorking] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');
  useEffect(() => {
    setExpanded(false);
    setShowWorking(false);
    setError('');
    setSaved('');
  }, [conversationId]);
  const working = useOutputs(
    { conversation_id: conversationId, show: 'working' },
    refresh,
    showWorking && workingCount > 0,
  );

  const codeFolder = resources.some(
    (resource) => resource.binding.kind === 'workspace' && resource.available,
  );
  const designs = resources.filter(
    (resource) => resource.binding.kind === 'artifact' && resource.available,
  );
  const shown = expanded ? outputs : outputs.slice(0, FOLDED);

  async function save(output: OutputView) {
    if (busy) return;
    setBusy(output.ref);
    setError('');
    setSaved('');
    try {
      const receipt = await controller.intent(
        conversationId,
        'media.save',
        { media_ref: output.ref },
        conversationRevision,
      );
      if (receipt.status !== 'completed' || !receipt.saved_name)
        throw new Error('media_save_uncertain');
      setSaved(receipt.saved_name);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  async function addToDesign(output: OutputView, design: ResourceView) {
    if (!artifactDesignSessions || busy) return;
    setBusy(output.ref);
    setError('');
    setSaved('');
    try {
      const blob = await controller.download(output.ref);
      if (
        blob.size > ATTACHMENT_LIMITS.fileBytes ||
        blob.type !== output.mime_type
      )
        throw new Error('media_identity_conflict');
      const file = new File([blob], output.file_name, {
        type: output.mime_type,
      });
      const session = artifactDesignSessions.get(conversationId, design);
      const result = await session.upload(file, design.resource_revision);
      if (result.status === 'partial')
        throw new Error('media_import_needs_review');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  function actions(output: OutputView): MenuAction[] {
    const visual = output.kind === 'image' || output.kind === 'video';
    return [
      {
        label: 'Show in conversation',
        icon: <Crosshair size={16} />,
        // One rendering per result (B22): the media lives in the
        // conversation; the tile only points to it.
        onSelect: () =>
          document
            .querySelector<HTMLElement>(
              `[data-media-ref=${JSON.stringify(output.ref)}]`,
            )
            ?.scrollIntoView({ block: 'center', behavior: 'smooth' }),
      },
      {
        label: 'Save to workspace',
        description: `Saved outputs/${output.file_name}`,
        icon: <FolderDown size={16} />,
        disabled: Boolean(busy),
        onSelect: () => void save(output),
      },
      ...(onUseOutputInCode && codeFolder && visual
        ? [
            {
              label: 'Use in code folder',
              icon: <Code2 size={16} />,
              onSelect: () =>
                onUseOutputInCode({
                  reference: output.ref,
                  mime: output.mime_type,
                }),
            },
          ]
        : []),
      ...(visual
        ? designs.map((design) => ({
            label:
              designs.length === 1 ? 'Add to design' : `Add to ${design.title}`,
            icon: <Palette size={16} />,
            disabled: Boolean(busy),
            onSelect: () => void addToDesign(output, design),
          }))
        : []),
      // Its versions, newest first: each opens in the viewer.
      ...(output.version_count > 1
        ? output.versions.map((version, index) => ({
            label: `Version ${version.version}`,
            description: ROLE_WORDS[version.role],
            selected: version.ref === output.ref,
            separatorBefore: index === 0,
            onSelect: () =>
              openMediaViewer(overlay, {
                reference: version.ref,
                mime: version.mime_type,
                name: versionName(output, version),
                size: version.size,
              }),
          }))
        : []),
    ];
  }

  const workingItems = working.page?.items ?? [];
  return (
    <>
      {outputs.length > 0 && (
        <Tiles label="Outputs" outputs={shown} busy={busy} actions={actions} />
      )}
      {outputs.length > FOLDED && (
        <Button
          variant="ghost"
          className="context-output-more"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? 'Show fewer' : `Show all ${outputs.length}`}
          <ChevronDown
            size={14}
            aria-hidden
            data-open={expanded ? 'true' : undefined}
          />
        </Button>
      )}
      {workingCount > 0 && (
        <label className="context-output-working">
          <span>
            Show working files
            <span className="context-count">{workingCount}</span>
          </span>
          <Toggle
            label="Show working files"
            checked={showWorking}
            onChange={(event) => setShowWorking(event.target.checked)}
          />
        </label>
      )}
      {showWorking && workingItems.length > 0 && (
        <Tiles
          label="Working files"
          outputs={workingItems}
          busy={busy}
          actions={actions}
        />
      )}
      {showWorking && working.error && (
        <p className="context-output-note" role="alert" data-tone="danger">
          {working.error}
        </p>
      )}
      {saved && (
        <p className="context-output-note" role="status">
          Saved to your workspace as {saved}
        </p>
      )}
      {error && (
        <p className="context-output-note" role="alert" data-tone="danger">
          {error}
        </p>
      )}
    </>
  );
}
