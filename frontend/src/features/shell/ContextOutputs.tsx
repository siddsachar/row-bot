import { useEffect, useState } from 'react';
import {
  ChevronDown,
  Code2,
  Crosshair,
  FolderDown,
  Image as ImageIcon,
  MoreHorizontal,
  Palette,
  Play,
} from 'lucide-react';
import type { ResourceView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Hint, Menu, type MenuAction } from '../../ui/primitives';
import { ATTACHMENT_LIMITS } from './attachment-limits';
import { openMediaViewer } from './MediaPreview';

export type ConversationOutput = {
  id: string;
  reference: string;
  mime: string;
  /** The prompt it was made from; older chats have none. */
  prompt?: string;
};

/** Newest first; the rest fold under "Show all" (two rows of three). */
const FOLDED = 6;
const SHORT = 48;

// Style words a media prompt often opens with: names start at the subject, so
// five videos aren't all "Photorealistic …" (I20).
const STYLE_OPENING =
  /^(?:(?:an?|the|photorealistic|realistic|hyper-?realistic|cinematic|dramatic|beautiful|stylized|stylised|detailed|high[- ]quality|4k|8k|hd|wide|close-up|aerial|slow-motion|shot|view|image|video|picture|photo|of|,)\s+)+/i;

const STYLE_WORD =
  /\b(?:photorealistic|realistic|hyper-?realistic|cinematic|dramatic|stylized|stylised|4k|8k|close-up|aerial|slow-motion|shot|view)\b/i;

/** A prompt's subject, cut at a word ("Colourful fireworks over a lake…"). */
function shortPrompt(full: string) {
  const opening = STYLE_OPENING.exec(full)?.[0] ?? '';
  const subject = STYLE_WORD.test(opening) ? full.slice(opening.length) : full;
  const prompt =
    subject.length >= 8
      ? subject.charAt(0).toLocaleUpperCase() + subject.slice(1)
      : full;
  if (prompt.length <= SHORT) return prompt;
  const cut = prompt.slice(0, SHORT);
  const space = cut.lastIndexOf(' ');
  return `${(space > SHORT / 2 ? cut.slice(0, space) : cut).replace(/[\s,.;:–-]+$/, '')}…`;
}

/** A picture's small server thumbnail; a video (which has none) a play mark. */
function Thumb({ output }: { output: ConversationOutput }) {
  const { controller } = useRuntime();
  const [src, setSrc] = useState('');
  const video = output.mime.startsWith('video/');
  useEffect(() => {
    setSrc('');
    if (video) return;
    const abort = new AbortController();
    let url = '';
    controller.attachmentThumbnail(output.reference, abort.signal).then(
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
  }, [controller, output.reference, video]);
  if (src) return <img className="context-output-thumb" src={src} alt="" />;
  return (
    <span className="context-output-thumb context-output-glyph" aria-hidden>
      {video ? (
        <span className="context-output-play">
          <Play size={14} fill="currentColor" />
        </span>
      ) : (
        <ImageIcon size={20} />
      )}
    </span>
  );
}

/**
 * The conversation's generated pictures and videos (5.3.3): a grid of
 * thumbnails, newest first. A tile opens the media viewer; its ⋯ menu shows
 * it in the conversation, saves it to the workspace or hands it on.
 */
export default function ContextOutputs({
  conversationId,
  conversationRevision,
  outputs,
  resources,
  onUseOutputInCode,
}: {
  conversationId: string;
  conversationRevision: string;
  outputs: ConversationOutput[];
  resources: ResourceView[];
  onUseOutputInCode?: (output: { reference: string; mime: string }) => void;
}) {
  const { controller, artifactDesignSessions } = useRuntime();
  const overlay = useOverlay();
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');
  useEffect(() => {
    setExpanded(false);
    setError('');
    setSaved('');
  }, [conversationId]);

  const codeFolder = resources.some(
    (resource) => resource.binding.kind === 'workspace' && resource.available,
  );
  const designs = resources.filter(
    (resource) => resource.binding.kind === 'artifact' && resource.available,
  );
  // An output is named by the start of its prompt, the whole prompt in its
  // tooltip; one from an older chat, which has none, by its kind and place
  // in the chat ("Image 3"). The viewer's title says the same.
  const counts = { image: 0, video: 0 };
  const named = outputs.map((output) => {
    const kind = output.mime.startsWith('video/') ? 'video' : 'image';
    counts[kind] += 1;
    const fallback = `${kind === 'video' ? 'Video' : 'Image'} ${counts[kind]}`;
    return {
      ...output,
      name: output.prompt ? shortPrompt(output.prompt) : fallback,
      hint: output.prompt || fallback,
    };
  });
  const newest = [...named].reverse();
  const shown = expanded ? newest : newest.slice(0, FOLDED);

  async function save(output: ConversationOutput) {
    if (busy) return;
    setBusy(output.reference);
    setError('');
    setSaved('');
    try {
      const receipt = await controller.intent(
        conversationId,
        'media.save',
        { media_ref: output.reference },
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

  async function addToDesign(output: ConversationOutput, design: ResourceView) {
    if (!artifactDesignSessions || busy) return;
    setBusy(output.reference);
    setError('');
    setSaved('');
    try {
      const blob = await controller.download(output.reference);
      if (blob.size > ATTACHMENT_LIMITS.fileBytes || blob.type !== output.mime)
        throw new Error('media_identity_conflict');
      const extension =
        output.mime === 'image/jpeg' ? 'jpg' : output.mime.split('/')[1];
      const filename = `generated-${output.reference.split(':').at(-1)}.${extension}`;
      const file = new File([blob], filename, { type: output.mime });
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

  function actions(output: ConversationOutput): MenuAction[] {
    return [
      {
        label: 'Show in conversation',
        icon: <Crosshair size={16} />,
        // One rendering per result (B22): the media lives in the
        // conversation; the tile only points to it.
        onSelect: () =>
          document
            .querySelector<HTMLElement>(
              `[data-media-ref=${JSON.stringify(output.reference)}]`,
            )
            ?.scrollIntoView({ block: 'center', behavior: 'smooth' }),
      },
      {
        label: 'Save to workspace',
        icon: <FolderDown size={16} />,
        disabled: Boolean(busy),
        onSelect: () => void save(output),
      },
      ...(onUseOutputInCode && codeFolder
        ? [
            {
              label: 'Use in code folder',
              icon: <Code2 size={16} />,
              onSelect: () => onUseOutputInCode(output),
            },
          ]
        : []),
      ...designs.map((design) => ({
        label:
          designs.length === 1 ? 'Add to design' : `Add to ${design.title}`,
        icon: <Palette size={16} />,
        disabled: Boolean(busy),
        onSelect: () => void addToDesign(output, design),
      })),
    ];
  }

  return (
    <>
      <ul className="context-output-grid" aria-label="Generated media">
        {shown.map((output) => (
          <li
            key={output.id}
            className="context-output-tile"
            data-busy={busy === output.reference ? 'true' : undefined}
          >
            <Hint label={output.hint}>
              <button
                type="button"
                className="context-output-open"
                aria-label={`Open ${output.name}`}
                aria-haspopup="dialog"
                onClick={() =>
                  openMediaViewer(overlay, {
                    reference: output.reference,
                    mime: output.mime,
                    name: output.name,
                  })
                }
              >
                <span className="context-output-frame">
                  <Thumb output={output} />
                </span>
                <span className="context-output-label" aria-hidden>
                  {output.name}
                </span>
              </button>
            </Hint>
            <Menu
              label={`Actions for ${output.name}`}
              iconOnly
              variant="ghost"
              className="icon-action icon-action-sm context-output-menu"
              actions={actions(output)}
            >
              <MoreHorizontal size={16} aria-hidden />
            </Menu>
          </li>
        ))}
      </ul>
      {named.length > FOLDED && (
        <Button
          variant="ghost"
          className="context-output-more"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? 'Show fewer' : `Show all ${named.length}`}
          <ChevronDown
            size={14}
            aria-hidden
            data-open={expanded ? 'true' : undefined}
          />
        </Button>
      )}
      {saved && (
        <p className="context-output-note" role="status">
          Saved to outputs/{saved}
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
