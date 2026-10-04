import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRuntime } from '../../runtime';
import type { IntegrationItem, IntegrationUse } from '../../api/types';
import { Button } from '../../ui/primitives';
import { clientError } from '../../api/errors';
import { useSetupOperations } from './setup-operations';

const noSubscription = () => () => undefined;
/** Prepare only a composer draft. Sending stays an explicit chat action. */
export default function TryInChat({ item }: { item: IntegrationItem }) {
  const { controller } = useRuntime();
  const navigate = useNavigate();
  const operations = useSetupOperations(item);
  const workspace = useSyncExternalStore(
    controller.subscribe ?? noSubscription,
    () => {
      const state = controller.getSnapshot?.();
      return state?.selectedConversationId &&
        state.workspace?.conversation_id === state.selectedConversationId
        ? state.workspace
        : null;
    },
  );
  const [result, setResult] = useState<IntegrationUse | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const draftRead = useRef<AbortController | null>(null);
  useEffect(
    () => () => draftRead.current?.abort(),
    [item.id, item.revision, item.status],
  );
  useEffect(() => {
    if (!workspace) return;
    const abort = new AbortController();
    void controller
      .integrationUse(workspace.conversation_id, item.id, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setResult(value);
      })
      .catch((error) => {
        if (!abort.signal.aborted) setMessage(clientError(error).message);
      });
    return () => abort.abort();
  }, [controller, workspace, item.id, item.revision, item.status]);
  const current =
    result?.conversation_id === workspace?.conversation_id &&
    result?.integration_id === item.id &&
    result?.conversation_revision === workspace?.revision
      ? result
      : null;
  const prepare = async () => {
    if (!workspace || operations.blocked) return;
    setBusy(true);
    draftRead.current?.abort();
    const abort = new AbortController();
    draftRead.current = abort;
    try {
      const checked = await controller.integrationUse(
        workspace.conversation_id,
        item.id,
        abort.signal,
      );
      if (abort.signal.aborted) return;
      const state = controller.getSnapshot();
      if (
        state.selectedConversationId !== workspace.conversation_id ||
        state.workspace !== workspace
      ) {
        setMessage(
          'The selected chat changed. Check availability again in that chat.',
        );
        return;
      }
      setResult(checked);
      if (
        !checked.eligible ||
        checked.conversation_revision !== workspace.revision
      ) {
        setMessage(checked.reason);
        return;
      }
      const draft = controller.getDraft(workspace.conversation_id);
      const text = `${draft.text}${draft.text ? '\n\n' : ''}Help me use ${item.name}${checked.account_label ? ` (${checked.account_label})` : ''}. Ask what I want to do before taking action.`;
      if (text.length > 200000) {
        setMessage(
          'This chat draft is full. Shorten it before adding this request.',
        );
        return;
      }
      controller.setDraft(workspace.conversation_id, { ...draft, text });
      navigate(
        `/conversations/${encodeURIComponent(workspace.conversation_id)}`,
      );
    } catch (error) {
      if (abort.signal.aborted) return;
      setMessage(clientError(error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="stack" aria-label="Try integration in chat">
      <p>
        {workspace
          ? current?.reason ||
            'Checking this integration against the current chat…'
          : 'Select a chat to check its profile and model before preparing a draft.'}
      </p>
      {current?.account_label && (
        <p>
          Account for this draft: {current.account_label}. Other saved
          connections are unchanged.
        </p>
      )}
      {message && <p role="status">{message}</p>}
      <Button
        variant="primary"
        disabled={busy || operations.blocked || !current?.eligible}
        onClick={() => void prepare()}
      >
        Try in chat
      </Button>
      <p>
        Prepares a visible draft in the current chat. Nothing is sent or
        executed until you choose Send; the profile and model stay unchanged.
      </p>
    </section>
  );
}
