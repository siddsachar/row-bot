import { useEffect, useRef, useState } from 'react';
import type {
  CustomToolApproval,
  CustomToolLibrary,
  CustomToolLibraryCommand,
  CustomToolTestView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, EntityList, EntityRow, Toggle } from '../../ui/primitives';
import { SettingsSection } from './anatomy';
import { CommandApprovalCard } from '../shell/ApprovalCard';

type Target = { tool_id: string } | { draft_id: string };
type CommandView = CustomToolLibrary['tools'][number]['commands'][number];

function targetKey(target: Target) {
  return 'tool_id' in target
    ? `tool:${target.tool_id}`
    : `draft:${target.draft_id}`;
}

/**
 * Settings › Tools › Custom tools (parity rows 33/34): every custom tool,
 * add from a folder (desktop app), switch on or off, make available in chat,
 * Test (a command that needs approval asks with the standard card) and
 * remove. Cloning a repository happens in the conversation.
 */
export default function CustomToolsSettings() {
  const { controller, platform } = useRuntime();
  const overlay = useOverlay();
  const [library, setLibrary] = useState<CustomToolLibrary | null>(null);
  const [desktop, setDesktop] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [approval, setApproval] = useState<{
    key: string;
    target: Target;
    value: CustomToolApproval;
  } | null>(null);
  const [result, setResult] = useState<{
    key: string;
    name: string;
    value: CustomToolTestView;
  } | null>(null);
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
    },
    [],
  );

  useEffect(() => {
    const abort = new AbortController();
    void controller
      .customToolLibrary(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setLibrary(value);
      })
      .catch((cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      });
    void platform
      .discover()
      .then((value) =>
        setDesktop(value.status === 'ok' && value.value.kind === 'pywebview'),
      )
      .catch(() => setDesktop(false));
    return () => abort.abort();
  }, [controller, platform]);

  async function run(
    action: CustomToolLibraryCommand['action'],
    payload: Record<string, unknown>,
    extra: { folder_grant?: string; test?: { key: string; name: string } } = {},
  ) {
    if (!library || busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const receipt = await controller.executeCustomToolLibrary({
        command_id: crypto.randomUUID(),
        revision: library.revision,
        action,
        payload,
        ...(extra.folder_grant ? { folder_grant: extra.folder_grant } : {}),
      });
      if (!alive.current) return;
      setLibrary(receipt.snapshot);
      if (receipt.status === 'approval_required' && receipt.approval) {
        setApproval({
          key: extra.test?.key ?? '',
          target: payload as Target,
          value: receipt.approval,
        });
        return;
      }
      setApproval(null);
      if (receipt.test && extra.test)
        setResult({ ...extra.test, value: receipt.test });
      if (receipt.status === 'failed') setError(receipt.summary);
      else setNotice(receipt.summary);
    } catch (cause) {
      if (alive.current) setError(clientError(cause).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  async function addFromFolder() {
    const picked = await platform.selectFolder(undefined, {
      intentId: crypto.randomUUID(),
      intent: 'custom_tool',
      conversationId: null,
      destination: 'custom-tools',
    });
    if (picked.status !== 'ok' || picked.value.kind !== 'folder') return;
    if (!('reference' in picked.value) || !picked.value.reference) {
      setError(
        'Adding a tool from a folder needs the Row-Bot desktop app. You can also ask Row-Bot in the chat to make a tool from a repository.',
      );
      return;
    }
    await run('inspect', {}, { folder_grant: picked.value.reference });
  }

  function test(target: Target, command: CommandView) {
    const key = targetKey(target);
    setResult(null);
    void run(
      'test',
      { ...target, command_name: command.name },
      { test: { key, name: command.name } },
    );
  }

  function remove(target: Target, name: string) {
    overlay.open({
      kind: 'alert',
      title: `Remove ${name}?`,
      description:
        'Row-Bot stops offering this tool. Its files stay in the folder.',
      confirmLabel: 'Remove tool',
      onConfirm: () => {
        overlay.close();
        void run('remove', target);
      },
    });
  }

  function commandList(target: Target, commands: CommandView[]) {
    const key = targetKey(target);
    const pending = approval?.key === key ? approval : null;
    const shown = result?.key === key ? result : null;
    return (
      <div className="custom-tool-details">
        <ul className="custom-tool-commands">
          {commands.map((command) => (
            <li key={command.name}>
              <span className="custom-tool-command-text">
                <strong>{command.name}</strong>
                {command.description && <span>{command.description}</span>}
                <code>{command.command}</code>
              </span>
              <Button
                disabled={busy}
                aria-label={`Test ${command.name}`}
                onClick={() => test(target, command)}
              >
                Test
              </Button>
            </li>
          ))}
        </ul>
        {pending && (
          <CommandApprovalCard
            question={`Run “${pending.value.command_name}” once?`}
            reason={pending.value.reason}
            command={pending.value.command}
            busy={busy}
            onDeny={() => {
              setApproval(null);
              setNotice('Nothing ran.');
            }}
            onApprove={() =>
              void run(
                'test',
                {
                  ...pending.target,
                  approval_nonce: pending.value.nonce,
                },
                { test: { key, name: pending.value.command_name } },
              )
            }
          />
        )}
        {shown && (
          <div className="custom-tool-result" role="status">
            <strong>
              {shown.name}:{' '}
              {shown.value.ok
                ? 'passed'
                : shown.value.ran
                  ? `failed (exit ${shown.value.returncode ?? '?'})`
                  : 'did not run'}
            </strong>
            {(shown.value.stdout || shown.value.stderr) && (
              <pre>
                {(shown.value.stdout + '\n' + shown.value.stderr).trim()}
              </pre>
            )}
          </div>
        )}
      </div>
    );
  }

  const tools = library?.tools ?? [];
  const drafts = library?.drafts ?? [];
  return (
    <SettingsSection
      title="Custom tools"
      anchor="custom-tools"
      description={
        desktop === false
          ? 'Adding a tool from a folder needs the Row-Bot desktop app. You can also ask Row-Bot in the chat to make a tool from a repository.'
          : 'Turn a folder’s scripts into tools Row-Bot can run. Adding one sends short excerpts of the folder to your chosen model, which may cost money with a paid provider.'
      }
      actions={
        <Button
          disabled={busy || !library || desktop === false}
          onClick={() => void addFromFolder()}
        >
          Add from a folder
        </Button>
      }
    >
      {library && !tools.length && !drafts.length && (
        <p className="muted">No custom tools yet.</p>
      )}
      <EntityList label="Custom tools">
        {tools.map((tool) => {
          const target = { tool_id: tool.id };
          return (
            <EntityRow
              key={tool.id}
              title={tool.name}
              status={{
                tone: tool.enabled ? 'success' : 'neutral',
                label: tool.enabled
                  ? tool.available_in_chat
                    ? 'On · in chat'
                    : 'On'
                  : 'Off',
              }}
              meta={`${tool.commands.length} ${tool.commands.length === 1 ? 'command' : 'commands'} · ${tool.folder}`}
              action={
                <Toggle
                  label={`Use ${tool.name}`}
                  checked={tool.enabled}
                  disabled={busy}
                  onChange={(event) =>
                    void run('enable', {
                      ...target,
                      enabled: event.target.checked,
                    })
                  }
                />
              }
              menuLabel={`More actions for ${tool.name}`}
              menu={[
                ...(tool.available_in_chat
                  ? []
                  : [
                      {
                        label: 'Make available in chat',
                        disabled: busy,
                        onSelect: () => void run('promote', target),
                      },
                    ]),
                {
                  label: 'Remove…',
                  danger: true,
                  disabled: busy,
                  onSelect: () => remove(target, tool.name),
                },
              ]}
              details={commandList(target, tool.commands)}
            />
          );
        })}
        {drafts.map((draft) => {
          const target = { draft_id: draft.id };
          return (
            <EntityRow
              key={draft.id}
              title={draft.name}
              status={{ tone: 'warning', label: 'Draft — not set up yet' }}
              meta={draft.folder}
              action={
                <Button
                  variant="primary"
                  disabled={busy}
                  onClick={() => void run('create', target)}
                >
                  Create tool
                </Button>
              }
              menuLabel={`More actions for ${draft.name}`}
              menu={[
                ...(draft.python_project && !draft.setup_ok
                  ? [
                      {
                        label: 'Set up Python',
                        disabled: busy,
                        onSelect: () => void run('setup', target),
                      },
                    ]
                  : []),
                {
                  label: 'Remove…',
                  danger: true,
                  disabled: busy,
                  onSelect: () => remove(target, draft.name),
                },
              ]}
              details={
                <>
                  {draft.warnings.length > 0 && (
                    <ul className="custom-tool-warnings">
                      {draft.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  )}
                  {commandList(target, draft.commands)}
                </>
              }
            />
          );
        })}
      </EntityList>
      {error && (
        <p role="alert" className="settings-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
    </SettingsSection>
  );
}
