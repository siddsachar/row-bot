import { useEffect, useRef, useState } from 'react';
import {
  ChevronDown,
  ChevronUp,
  Download,
  RefreshCw,
  Send,
} from 'lucide-react';
import { clientError } from '../../api/errors';
import type { PageDecisionView, PageView } from '../../api/types';
import { useClientSelector, useRuntime } from '../../runtime';
import { Button, ErrorState, IconButton, Skeleton } from '../../ui/primitives';
import { shortTime } from '../shell/conversation-groups';
import { saveProblem, useSaveFile } from '../shell/save-file';
import { PageBridge } from './page-bridge';
import { pageMode, pageTheme } from './page-theme';
import { choiceWords, decisionCount } from './page-words';

type Status = 'loading' | 'ready' | 'failed' | 'closed';

function DecisionLine({ item }: { item: PageDecisionView }) {
  return (
    <li>
      <strong>{choiceWords(item.choice)}:</strong> {item.label || item.id}
      {item.note && <span className="page-decision-note"> “{item.note}”</span>}
    </li>
  );
}

/**
 * A page beside its chat (N5): the side panel on desktop, full screen with
 * Back on a phone. The page runs in a frame with `sandbox="allow-scripts"`
 * only, loaded once under the policy Row-Bot served it with; it talks to
 * Row-Bot only through the page bridge, which can save its state and record
 * decisions. Under it, outside the frame, the person's own "Send to Row-Bot"
 * is the only thing that posts the decisions to the chat.
 */
export default function PagePanel({
  conversationId,
  pageId,
  visible,
}: {
  conversationId: string;
  pageId: string;
  visible: boolean;
}) {
  const { controller } = useRuntime();
  const saveFile = useSaveFile();
  const [view, setView] = useState<PageView | null>(null);
  const [frameUrl, setFrameUrl] = useState('');
  const [status, setStatus] = useState<Status>('loading');
  const [problem, setProblem] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [listOpen, setListOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const bridge = useRef<PageBridge | null>(null);
  const loads = useRef(0);
  const version = useRef(0);

  const generation = useClientSelector((value) =>
    value.conversation?.id === conversationId
      ? value.projection?.generation
      : undefined,
  );
  // The chat's saved messages moved on (a step finished, a message was sent).
  const revision = useClientSelector((value) =>
    value.projection?.conversation_id === conversationId
      ? value.projection?.checkpoint_revision
      : undefined,
  );
  const busy =
    Boolean(generation && !generation.quiesced) ||
    generation?.status === 'waiting_approval';

  // Make a frame for the page: on opening, on Retry, and when the agent made a new version.
  useEffect(() => {
    if (!visible) return;
    const abort = new AbortController();
    setStatus('loading');
    setProblem('');
    controller.pageFrame(pageId, pageTheme(), abort.signal).then(
      (made) => {
        if (abort.signal.aborted) return;
        version.current = made.page.version;
        setView(made.page);
        setFrameUrl(made.frame_url);
      },
      (cause) => {
        if (abort.signal.aborted) return;
        setStatus('failed');
        setProblem(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [controller, pageId, visible, attempt]);

  // The page's bridge, for as long as this frame is shown.
  useEffect(() => {
    const element = frame.current;
    if (!frameUrl || !element) return;
    loads.current = 0;
    const instance = new PageBridge(element, {
      saveState: async (state) => {
        setSaving((count) => count + 1);
        try {
          await controller.savePageState(pageId, state);
        } finally {
          setSaving((count) => count - 1);
        }
      },
      decide: async (decision) => {
        setSaving((count) => count + 1);
        try {
          const next = await controller.savePageDecision(pageId, decision);
          setView(next);
          setNotice('');
        } finally {
          setSaving((count) => count - 1);
        }
      },
      onNavigated: () => {
        setStatus('closed');
        setProblem('This page tried to open another page, so it was closed.');
      },
    });
    bridge.current = instance;
    const listener = (event: MessageEvent) => instance.handle(event);
    window.addEventListener('message', listener);
    const themes = new MutationObserver(() => instance.theme(pageMode()));
    themes.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    return () => {
      themes.disconnect();
      window.removeEventListener('message', listener);
      instance.end();
      bridge.current = null;
    };
  }, [controller, frameUrl, pageId]);

  // The chat moved on (the agent may have changed the page, or the person
  // sent its decisions from another device): read it again.
  useEffect(() => {
    if (!visible || revision === undefined || !version.current) return;
    const abort = new AbortController();
    controller.page(pageId, abort.signal).then(
      (fresh) => {
        if (abort.signal.aborted) return;
        if (fresh.version !== version.current) setAttempt((n) => n + 1);
        else {
          setView(fresh);
          bridge.current?.decisions(fresh.decisions ?? []);
        }
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [controller, pageId, revision, visible]);

  async function send() {
    if (!view || sending || busy) return;
    setSending(true);
    setError('');
    setNotice('');
    try {
      await controller.controlsSettled?.(conversationId);
      const workspace = await controller.workspaceFor(conversationId);
      const selection = workspace.controls.model_selection;
      if (!selection) throw { code: 'model_selection_required' };
      const snapshot = controller.getSnapshot();
      const receipt = await controller.intent(
        conversationId,
        'conversation.submit',
        {
          submission_id: crypto.randomUUID(),
          text: `${decisionCount(view.pending)} from ${view.title}`,
          attachment_refs: [],
          model_selection: selection,
          page_id: pageId,
        },
        snapshot.conversation?.id === conversationId
          ? snapshot.conversation.revision
          : workspace.revision,
      );
      if (receipt.status !== 'accepted' && receipt.status !== 'completed')
        throw { code: receipt.code ?? 'operation_uncertain' };
      const fresh = await controller.page(pageId);
      setView(fresh);
      bridge.current?.decisions(fresh.decisions ?? []);
      setListOpen(false);
      setNotice('Sent to Row-Bot.');
      controller.showLatest();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setSending(false);
    }
  }

  async function download() {
    if (!view || !saveFile) return;
    setError('');
    setNotice('');
    const result = await saveFile({
      kind: 'page',
      page_id: pageId,
      theme: pageTheme(),
    });
    setError(saveProblem(result, 'the page'));
  }

  const pending = (view?.decisions ?? []).filter((item) => !item.sent);
  const title = view?.title ?? 'Page';
  return (
    <section className="page-panel" aria-label={`${title} page`}>
      <div className="page-toolbar">
        <span className="page-toolbar-meta" aria-live="polite">
          {saving > 0
            ? 'Saving…'
            : view
              ? `Updated ${shortTime(view.updated_at)}`
              : ''}
        </span>
        <IconButton
          size="sm"
          label="Download as HTML"
          disabled={!view}
          onClick={() => void download()}
        >
          <Download size={16} aria-hidden />
        </IconButton>
      </div>
      <div className="page-frame-wrap" data-status={status}>
        {frameUrl && status !== 'failed' && status !== 'closed' && (
          <iframe
            key={frameUrl}
            ref={frame}
            className="page-frame"
            title={title}
            src={frameUrl}
            sandbox="allow-scripts"
            referrerPolicy="no-referrer"
            onLoad={() => {
              loads.current += 1;
              if (loads.current > 1) bridge.current?.navigated();
              else {
                // The theme may have changed while the frame was being made.
                bridge.current?.theme(pageMode());
                setStatus('ready');
              }
            }}
          />
        )}
        {status === 'loading' && (
          <div className="page-frame-state">
            <Skeleton label={`Opening ${title}`} />
          </div>
        )}
        {(status === 'failed' || status === 'closed') && (
          <div className="page-frame-state">
            <ErrorState
              title="This page didn't open"
              action={
                <Button onClick={() => setAttempt((n) => n + 1)}>
                  <RefreshCw size={16} aria-hidden />
                  Open again
                </Button>
              }
            >
              {problem || 'Try again in a moment.'}
            </ErrorState>
          </div>
        )}
      </div>
      {(pending.length > 0 || notice || error) && (
        <section className="page-footer" aria-label="Decisions">
          {listOpen && pending.length > 0 && (
            <ul className="page-decisions" id={`${pageId}-decisions`}>
              {pending.map((item) => (
                <DecisionLine key={item.id} item={item} />
              ))}
            </ul>
          )}
          {error && (
            <p className="page-footer-error" role="alert">
              {error}
            </p>
          )}
          {notice && (
            <p className="page-footer-notice" role="status">
              {notice}
            </p>
          )}
          {pending.length > 0 && (
            <div className="page-footer-bar">
              <button
                type="button"
                className="page-footer-count"
                aria-expanded={listOpen}
                aria-controls={`${pageId}-decisions`}
                onClick={() => setListOpen((open) => !open)}
              >
                {decisionCount(pending.length)}
                {listOpen ? (
                  <ChevronDown size={14} aria-hidden />
                ) : (
                  <ChevronUp size={14} aria-hidden />
                )}
              </button>
              {busy && (
                <span className="page-footer-hint">
                  Send when Row-Bot has replied.
                </span>
              )}
              <Button
                variant="primary"
                className="page-footer-send"
                disabled={sending || busy}
                onClick={() => void send()}
              >
                <Send size={16} aria-hidden />
                {sending ? 'Sending…' : 'Send to Row-Bot'}
              </Button>
            </div>
          )}
        </section>
      )}
    </section>
  );
}
