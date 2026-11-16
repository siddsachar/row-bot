import { useEffect, useState } from 'react';
import {
  Cable,
  FileCode2,
  FilePen,
  Plus,
  SquareTerminal,
  Unplug,
} from 'lucide-react';
import {
  editorsClient,
  type EditorConfigPreview,
  type EditorConfigWritten,
  type EditorId,
  type EditorSnippet,
  type EditorView,
  type EditorsClient,
} from '../../api/editors';
import { clientError } from '../../api/errors';
import type { ClientPlatform } from '../../platform';
import { writeClipboardText } from '../../platform/clipboard';
import { ModalTask, useOverlay } from '../../ui/overlays';
import {
  Button,
  CopyGlyph,
  EntityList,
  EntityRow,
  ErrorState,
  IconButton,
  InlineEmpty,
  Skeleton,
  useCopyFeedback,
} from '../../ui/primitives';
import { When } from '../../ui/When';
import './editors.css';

const TAGLINES: Record<EditorId, string> = {
  'claude-code': 'Row-Bot can add itself to Claude Code’s settings.',
  cursor: 'Row-Bot can add itself to Cursor’s settings.',
  codex: 'Row-Bot can add itself to Codex’s settings.',
  zed: 'You paste Row-Bot into Zed’s settings.',
};

type Step =
  | { kind: 'pick' }
  | { kind: 'snippet'; editor: EditorView; snippet: EditorSnippet | null }
  | {
      kind: 'review';
      editor: EditorView;
      snippet: EditorSnippet;
      preview: EditorConfigPreview | null;
    }
  | { kind: 'written'; editor: EditorView; written: EditorConfigWritten };

function changeWords(preview: EditorConfigPreview, name: string) {
  if (preview.action === 'unchanged')
    return `${name}’s settings already have this. Nothing changes.`;
  if (preview.action === 'replace')
    return 'Row-Bot replaces its own entry in this file. Nothing else in it changes.';
  return preview.exists
    ? 'Row-Bot adds its entry to this file. Nothing else in it changes.'
    : 'Row-Bot creates this file with its entry.';
}

function Snippet({
  snippet,
  writeClipboard,
  onCopyFailed,
}: {
  snippet: EditorSnippet;
  writeClipboard?: ClientPlatform['writeClipboard'];
  onCopyFailed: () => void;
}) {
  const [copied, setCopied] = useCopyFeedback();
  return (
    <div className="editor-snippet">
      <pre tabIndex={0} aria-label="Settings to add">
        <code>{snippet.text}</code>
      </pre>
      <IconButton
        size="sm"
        variant="secondary"
        className="editor-snippet-copy"
        label={copied ? 'Copied' : 'Copy settings'}
        onClick={() =>
          void writeClipboardText(snippet.text, writeClipboard).then((done) =>
            done ? setCopied(true) : onCopyFailed(),
          )
        }
      >
        <CopyGlyph copied={copied} />
      </IconButton>
    </div>
  );
}

/**
 * Settings › Devices & remote access › Editors (E4): Claude Code, Cursor,
 * Codex or Zed asking Row-Bot through `row-bot mcp`. Connecting makes the
 * editor its own key in this computer's keychain and shows the exact
 * settings, which never contain the key; with the person's OK Row-Bot adds
 * them to the editor's own file after showing the file and the change, and
 * keeps a copy of it first. Each connected editor shows its last use and
 * Revoke, which stops its key at once.
 */
export default function EditorConnections({
  client = editorsClient,
  writeClipboard,
}: {
  client?: EditorsClient;
  writeClipboard?: ClientPlatform['writeClipboard'];
}) {
  const overlay = useOverlay();
  const [editors, setEditors] = useState<EditorView[] | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [step, setStep] = useState<Step | null>(null);
  const [choice, setChoice] = useState<EditorId | null>(null);
  const [busy, setBusy] = useState(false);
  const [taskError, setTaskError] = useState('');

  useEffect(() => {
    const abort = new AbortController();
    setError('');
    void client.list(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setEditors(value);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [client, reload]);

  function begin() {
    setTaskError('');
    setChoice(null);
    setStep({ kind: 'pick' });
  }

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setTaskError('');
    try {
      await action();
    } catch (cause) {
      setTaskError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  function connect() {
    const picked = editors?.find((editor) => editor.id === choice);
    if (!picked) return;
    void run(async () => {
      const result = await client.connect(picked.id);
      setStep({
        kind: 'snippet',
        editor: result.editor,
        snippet: result.snippet,
      });
      setReload((value) => value + 1);
    });
  }

  function showSettings(editor: EditorView) {
    setTaskError('');
    setStep({ kind: 'snippet', editor, snippet: null });
    void client.snippet(editor.id).then(
      (snippet) =>
        setStep((current) =>
          current?.kind === 'snippet' && current.editor.id === editor.id
            ? { ...current, snippet }
            : current,
        ),
      (cause) => setTaskError(clientError(cause).message),
    );
  }

  function review(editor: EditorView, snippet: EditorSnippet) {
    setTaskError('');
    setStep({ kind: 'review', editor, snippet, preview: null });
    void client.preview(editor.id).then(
      (preview) =>
        setStep((current) =>
          current?.kind === 'review' && current.editor.id === editor.id
            ? { ...current, preview }
            : current,
        ),
      (cause) => setTaskError(clientError(cause).message),
    );
  }

  function write(editor: EditorView, preview: EditorConfigPreview) {
    void run(async () => {
      const written = await client.write(editor.id, preview.fingerprint);
      setStep({ kind: 'written', editor, written });
    });
  }

  function revoke(editor: EditorView, opener: HTMLElement) {
    overlay.open({
      kind: 'alert',
      returnFocusTo: opener,
      title: `Revoke ${editor.name}?`,
      description: `${editor.name} can’t reach Row-Bot from now on. Its chats stay in Row-Bot. To use it again, connect it again.`,
      confirmLabel: 'Revoke',
      onConfirm: () => {
        overlay.close();
        void client.revoke(editor.id).then(
          () => setReload((value) => value + 1),
          (cause) => setError(clientError(cause).message),
        );
      },
    });
  }

  const connected = editors?.filter((editor) => editor.connected) ?? [];
  const copyFailed = () =>
    setTaskError('Row-Bot couldn’t copy the settings. Select them and copy.');
  return (
    <section
      className="settings-section-flat editor-connections"
      aria-labelledby="editor-connections-title"
      data-setting-anchor="editors"
    >
      <header className="settings-section-head">
        <span className="settings-section-icon" aria-hidden>
          <SquareTerminal size={16} aria-hidden />
        </span>
        <div className="settings-section-text">
          <h3 id="editor-connections-title">Editors</h3>
          <p>
            Ask Row-Bot from Claude Code, Cursor, Codex or Zed. It answers with
            what it knows about you, and asks you here before anything that
            needs your approval.
          </p>
        </div>
        {connected.length > 0 && (
          <div className="settings-section-actions">
            <IconButton
              label="Connect an editor"
              variant="secondary"
              disabled={!editors}
              onClick={begin}
            >
              <Plus size={16} aria-hidden />
            </IconButton>
          </div>
        )}
      </header>
      {error && (
        <ErrorState
          title="Editors unavailable"
          action={
            <Button onClick={() => setReload((value) => value + 1)}>
              Retry
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {!editors && !error ? <Skeleton label="Loading editors" /> : null}
      {editors && connected.length === 0 && (
        <InlineEmpty
          icon={<SquareTerminal size={16} aria-hidden />}
          action={
            <Button variant="primary" onClick={begin}>
              <Plus size={16} aria-hidden /> Connect an editor
            </Button>
          }
        >
          No editors connected.
        </InlineEmpty>
      )}
      {connected.length > 0 && (
        <EntityList label="Connected editors">
          {connected.map((editor) => (
            <EntityRow
              key={editor.id}
              title={editor.name}
              icon={<SquareTerminal size={16} aria-hidden />}
              meta={
                editor.last_used_at ? (
                  <>
                    Last used <When value={editor.last_used_at} />
                  </>
                ) : (
                  'Not used yet'
                )
              }
              action={
                <>
                  <IconButton
                    size="sm"
                    label={`Settings for ${editor.name}`}
                    onClick={() => showSettings(editor)}
                  >
                    <FileCode2 size={16} aria-hidden />
                  </IconButton>
                  <IconButton
                    size="sm"
                    variant="danger"
                    label={`Revoke ${editor.name}`}
                    onClick={(event) => revoke(editor, event.currentTarget)}
                  >
                    <Unplug size={16} aria-hidden />
                  </IconButton>
                </>
              }
            />
          ))}
        </EntityList>
      )}
      <ModalTask
        open={step !== null}
        onOpenChange={(open) => !open && setStep(null)}
        kind="sheet"
        className="editor-connect-sheet"
        title={
          step?.kind === 'review'
            ? `Add Row-Bot to ${step.editor.name}`
            : step && step.kind !== 'pick'
              ? step.editor.name
              : 'Connect an editor'
        }
        description={
          step?.kind === 'pick' || !step
            ? 'Row-Bot makes the editor its own key and keeps it in this computer’s keychain.'
            : 'The key stays in this computer’s keychain: these settings don’t contain it.'
        }
      >
        {step?.kind === 'pick' && (
          <div className="stack">
            <div
              role="radiogroup"
              aria-label="Editor"
              className="access-options editor-options"
            >
              {(editors ?? []).map((editor) => (
                <label key={editor.id} className="access-option">
                  <input
                    type="radio"
                    name="editor"
                    value={editor.id}
                    checked={choice === editor.id}
                    data-initial-focus={
                      editor.id === editors?.[0]?.id ? true : undefined
                    }
                    onChange={() => setChoice(editor.id)}
                  />
                  <span className="access-option-icon" aria-hidden>
                    <SquareTerminal size={18} aria-hidden />
                  </span>
                  <span className="access-option-text">
                    <span className="access-option-title">{editor.name}</span>
                    <span className="access-option-tagline">
                      {editor.connected
                        ? 'Connected. Connecting again gives it a new key.'
                        : TAGLINES[editor.id]}
                    </span>
                  </span>
                </label>
              ))}
            </div>
            {taskError && <p role="alert">{taskError}</p>}
            <div className="editor-actions">
              <Button onClick={() => setStep(null)}>Cancel</Button>
              <Button
                variant="primary"
                disabled={!choice || busy}
                onClick={connect}
              >
                <Cable size={16} aria-hidden />
                {busy ? 'Connecting…' : 'Connect'}
              </Button>
            </div>
          </div>
        )}
        {step?.kind === 'snippet' && (
          <div className="stack">
            {!step.snippet ? (
              taskError ? (
                <p role="alert">{taskError}</p>
              ) : (
                <Skeleton label="Reading the settings" />
              )
            ) : (
              <>
                <p className="settings-help">
                  {step.editor.writes_config
                    ? 'Row-Bot can add this for you, or copy it into '
                    : 'Copy this into '}
                  <code className="editor-path">{step.snippet.path}</code>
                  {`, then restart ${step.editor.name}.`}
                </p>
                <Snippet
                  snippet={step.snippet}
                  writeClipboard={writeClipboard}
                  onCopyFailed={copyFailed}
                />
                {taskError && <p role="alert">{taskError}</p>}
                <div className="editor-actions">
                  <Button onClick={() => setStep(null)}>Done</Button>
                  {step.editor.writes_config && (
                    <Button
                      variant="primary"
                      data-initial-focus
                      onClick={() => review(step.editor, step.snippet!)}
                    >
                      <FilePen size={16} aria-hidden /> Add it for me
                    </Button>
                  )}
                </div>
              </>
            )}
          </div>
        )}
        {step?.kind === 'review' && (
          <div className="stack">
            {!step.preview ? (
              taskError ? (
                <p role="alert">{taskError}</p>
              ) : (
                <Skeleton label="Reading the file" />
              )
            ) : step.preview.action === 'unavailable' ? (
              <p role="alert">
                {clientError({ code: step.preview.problem }).message}
              </p>
            ) : (
              <>
                <p className="settings-help">
                  {changeWords(step.preview, step.editor.name)}
                </p>
                <code className="editor-path editor-path-block">
                  {step.preview.path}
                </code>
                {step.preview.before && step.preview.action === 'replace' && (
                  <div className="editor-change">
                    <span className="editor-change-label">Now</span>
                    <pre tabIndex={0} aria-label="Row-Bot's entry now">
                      <code>{step.preview.before}</code>
                    </pre>
                  </div>
                )}
                {step.preview.after && (
                  <div className="editor-change" data-after="true">
                    <span className="editor-change-label">
                      {step.preview.action === 'replace' ? 'After' : 'Adds'}
                    </span>
                    <pre tabIndex={0} aria-label="Row-Bot's entry after">
                      <code>{step.preview.after}</code>
                    </pre>
                  </div>
                )}
                {step.preview.exists && step.preview.action !== 'unchanged' && (
                  <p className="settings-help">
                    A copy of the file is kept beside it first.
                  </p>
                )}
              </>
            )}
            {taskError && step.preview && <p role="alert">{taskError}</p>}
            <div className="editor-actions">
              <Button
                onClick={() =>
                  setStep({
                    kind: 'snippet',
                    editor: step.editor,
                    snippet: step.snippet,
                  })
                }
              >
                Back
              </Button>
              {step.preview &&
                step.preview.action !== 'unavailable' &&
                step.preview.action !== 'unchanged' && (
                  <Button
                    variant="primary"
                    disabled={busy}
                    data-initial-focus
                    onClick={() => write(step.editor, step.preview!)}
                  >
                    {busy ? 'Changing…' : 'Change the file'}
                  </Button>
                )}
            </div>
          </div>
        )}
        {step?.kind === 'written' && (
          <div className="stack">
            <p className="settings-help">
              {step.written.changed
                ? `Added to ${step.editor.name}’s settings. Restart ${step.editor.name} to use Row-Bot.`
                : `${step.editor.name}’s settings already had Row-Bot.`}
            </p>
            {step.written.backup_path && (
              <p className="settings-help">
                The file as it was:{' '}
                <code className="editor-path">{step.written.backup_path}</code>
              </p>
            )}
            <div className="editor-actions">
              <Button
                variant="primary"
                data-initial-focus
                onClick={() => setStep(null)}
              >
                Done
              </Button>
            </div>
          </div>
        )}
      </ModalTask>
    </section>
  );
}
