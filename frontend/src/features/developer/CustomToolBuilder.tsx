import { useEffect, useState } from 'react';
import type {
  CustomToolCommand,
  CustomToolCommandView,
  CustomToolSnapshot,
} from '../../api/types';
import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import { readRetainedCommand, retainCommand } from '../../api/retained-command';
import { Button, ErrorState, Field, Input, Toggle } from '../../ui/primitives';

type Action = CustomToolCommand['action'];

export default function CustomToolBuilder({
  controller,
  conversation,
  binding,
  visible,
}: {
  controller: ClientController;
  conversation: string;
  binding: string;
  visible: boolean;
}) {
  const [snapshot, setSnapshot] = useState<CustomToolSnapshot | null>(null);
  const [selected, setSelected] = useState('');
  const [name, setName] = useState('');
  const [version, setVersion] = useState('');
  const [commands, setCommands] = useState<CustomToolCommandView[]>([]);
  const [instruction, setInstruction] = useState('');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const commandScope = `custom-tool:${conversation}:${binding}`;
  const [pending, setPending] = useState(() =>
    readRetainedCommand(commandScope),
  );
  const remember = (value: string) => {
    setPending(value);
    retainCommand(commandScope, value);
  };

  useEffect(() => {
    if (!visible) return;
    const abort = new AbortController();
    void controller
      .customTools(conversation, binding, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setSnapshot(value);
      })
      .catch((cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      });
    return () => abort.abort();
  }, [controller, conversation, binding, visible]);

  const draft =
    snapshot?.drafts.find((item) => item.id === selected) ??
    snapshot?.drafts[0];
  const tool = snapshot?.tools.find(
    (item) => item.id === draft?.created_tool_id,
  );
  useEffect(() => {
    setName(draft?.name ?? '');
    setVersion(draft?.version ?? '');
    setCommands(draft?.commands ?? []);
  }, [
    draft?.id,
    draft?.name,
    draft?.version,
    draft?.commands,
    snapshot?.revision,
  ]);

  const run = async (action: Action, payload: Record<string, unknown> = {}) => {
    if (!snapshot || busy || pending) return;
    if (
      action === 'remove' &&
      !window.confirm(
        'Remove this Custom Tool? Source files will be preserved.',
      )
    )
      return;
    const commandId = crypto.randomUUID();
    setBusy(true);
    remember(commandId);
    setError('');
    setMessage('');
    try {
      const receipt = await controller.executeCustomTool(
        conversation,
        binding,
        {
          command_id: commandId,
          revision: snapshot.revision,
          action,
          payload:
            action === 'inspect' ? {} : { draft_id: draft?.id, ...payload },
        },
      );
      setSnapshot(receipt.snapshot);
      if (receipt.status === 'failed') setError(receipt.summary);
      else setMessage(receipt.summary);
      remember('');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };

  const recover = async () => {
    if (!pending || busy) return;
    setBusy(true);
    try {
      const receipt = await controller.customToolReceipt(
        conversation,
        binding,
        pending,
      );
      setSnapshot(receipt.snapshot);
      if (receipt.status === 'failed') setError(receipt.summary);
      else {
        setMessage(receipt.summary);
        setError('');
      }
      if (receipt.status !== 'uncertain') remember('');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };

  if (!visible) return null;
  return (
    <section className="stack" aria-label="Custom Tool Builder">
      <h3>Custom Tool Builder</h3>
      <p>Inspect the selected workspace and turn its commands into a tool.</p>
      <p className="muted">
        Inspect sends bounded repository excerpts to your configured AI model.
        Your provider may charge for this request.
      </p>
      <Button
        disabled={!snapshot || busy || Boolean(pending)}
        onClick={() => void run('inspect')}
      >
        {busy ? 'Working…' : 'Inspect tool'}
      </Button>
      {error && (
        <ErrorState
          title="Custom Tool action failed"
          action={
            pending ? (
              <Button onClick={() => void recover()}>Check outcome</Button>
            ) : undefined
          }
        >
          {error}
        </ErrorState>
      )}
      {pending && !error && !busy && (
        <Button onClick={() => void recover()}>Check previous outcome</Button>
      )}
      {message && <p role="status">{message}</p>}
      {!snapshot && !error && <p role="status">Loading Custom Tools…</p>}
      {snapshot && snapshot.drafts.length > 1 && (
        <Field label="Draft">
          <select
            className="input select"
            value={draft?.id ?? ''}
            onChange={(event) => setSelected(event.target.value)}
          >
            {snapshot.drafts.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      {draft && (
        <div className="stack">
          <Field label="Name">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="Version">
            <Input
              value={version}
              onChange={(event) => setVersion(event.target.value)}
            />
          </Field>
          {draft.warnings.map((warning, index) => (
            <p key={`${index}:${warning}`} role="status">
              {warning}
            </p>
          ))}
          <h4>Commands</h4>
          {commands.map((command, index) => (
            <div className="stack" key={index}>
              <Field label={`Command ${index + 1} name`}>
                <Input
                  value={command.name}
                  onChange={(event) =>
                    setCommands((current) =>
                      current.map((item, position) =>
                        position === index
                          ? { ...item, name: event.target.value }
                          : item,
                      ),
                    )
                  }
                />
              </Field>
              <Field label={`Command ${index + 1} description`}>
                <Input
                  value={command.description}
                  onChange={(event) =>
                    setCommands((current) =>
                      current.map((item, position) =>
                        position === index
                          ? { ...item, description: event.target.value }
                          : item,
                      ),
                    )
                  }
                />
              </Field>
              <Field label={`Command ${index + 1} line`}>
                <Input
                  value={command.command}
                  onChange={(event) =>
                    setCommands((current) =>
                      current.map((item, position) =>
                        position === index
                          ? { ...item, command: event.target.value }
                          : item,
                      ),
                    )
                  }
                />
              </Field>
              {draft.created_tool_id && (
                <Button
                  disabled={busy || Boolean(pending)}
                  onClick={() =>
                    void run('test', { command_name: command.name, query })
                  }
                >
                  Run {command.name}
                </Button>
              )}
              {draft.test_results[command.name] && (
                <p role="status">
                  {draft.test_results[command.name].ok
                    ? 'Passed'
                    : draft.test_results[command.name].ran
                      ? 'Failed'
                      : 'Blocked'}
                  {draft.test_results[command.name].stderr &&
                    `: ${draft.test_results[command.name].stderr}`}
                </p>
              )}
            </div>
          ))}
          <Field label="Test query">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </Field>
          <Button
            disabled={busy || Boolean(pending)}
            onClick={() =>
              void run('update', { fields: { name, version, commands } })
            }
          >
            Save draft
          </Button>
          <Field label="AI refinement instruction">
            <Input
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
            />
          </Field>
          <Button
            disabled={busy || Boolean(pending) || !instruction.trim()}
            onClick={() => void run('refine', { instruction })}
          >
            Refine with AI
          </Button>
          {!draft.created_tool_id && (
            <Button
              disabled={busy || Boolean(pending)}
              onClick={() => void run('create')}
            >
              Create tool
            </Button>
          )}
          {draft.python_project && !draft.setup_ok && (
            <>
              <p className="muted">
                Python setup may install dependencies in an isolated environment
                and use the network.
              </p>
              <Button
                disabled={busy || Boolean(pending)}
                onClick={() => void run('setup')}
              >
                Set up Python environment
              </Button>
            </>
          )}
          {tool && (
            <>
              <label className="field">
                <span>Enabled in Developer</span>
                <Toggle
                  label="Enabled in Developer"
                  checked={tool.enabled}
                  disabled={busy || Boolean(pending)}
                  onChange={(event) =>
                    void run('enable', { enabled: event.target.checked })
                  }
                />
              </label>
              {!tool.available_in_chat && (
                <Button
                  disabled={busy || Boolean(pending)}
                  onClick={() => void run('promote')}
                >
                  Add to chat tools
                </Button>
              )}
            </>
          )}
          <Button
            variant="danger"
            disabled={busy || Boolean(pending)}
            onClick={() => void run('remove')}
          >
            Remove tool
          </Button>
        </div>
      )}
    </section>
  );
}
