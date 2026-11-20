import { useEffect, useMemo, useState } from 'react';
import { Pencil, Trash2 } from 'lucide-react';
import type {
  ApprovalRulePage,
  ApprovalRuleUpdate,
  ApprovalRuleView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useLiveUpdate } from '../../live';
import { useOptionalRuntime } from '../../runtime';
import { relativeTime } from '../../ui/format';
import { useNotify, useOptionalOverlay } from '../../ui/overlays';
import {
  Button,
  EmptyState,
  ErrorState,
  IconButton,
  Input,
  Skeleton,
  Toggle,
} from '../../ui/primitives';
import {
  DryRunPanel,
  NEVER_COVERED,
  RuleWords,
  SheetActions,
  useDryRun,
  type DryRun,
} from '../shell/AlwaysAllow';
import { SettingsGroup } from './anatomy';

/** What the rules list reads and changes; the app's controller by default. */
export type RulesIO = {
  list(signal?: AbortSignal): Promise<ApprovalRulePage>;
  update(ruleId: string, body: ApprovalRuleUpdate): Promise<ApprovalRuleView>;
  remove(ruleId: string): Promise<ApprovalRulePage>;
  dryRun: DryRun;
};

function useDefaultIO(): RulesIO | null {
  const runtime = useOptionalRuntime();
  return useMemo(() => {
    if (!runtime) return null;
    const { controller } = runtime;
    return {
      list: (signal) => controller.approvalRules(signal),
      update: (ruleId, body) => controller.updateApprovalRule(ruleId, body),
      remove: (ruleId) => controller.deleteApprovalRule(ruleId),
      dryRun: (body, signal) => controller.dryRunApprovalRule(body, signal),
    };
  }, [runtime]);
}

/** "Used 12 times · last 2 days ago", or "Not used yet". */
function usedWords(rule: ApprovalRuleView) {
  if (!rule.uses || !rule.last_used_at) return 'Not used yet';
  const count = rule.uses === 1 ? 'Used once' : `Used ${rule.uses} times`;
  return `${count} · last ${relativeTime(rule.last_used_at)}`;
}

/**
 * Settings › System › Activity › Always allow (D1): each rule in words, its use,
 * a switch, and Edit (a command rule's exact command, with its dry run) and
 * Delete. A rule starts only from an approval card; there is no "New rule".
 */
export default function ApprovalRulesSettings({ io: given }: { io?: RulesIO }) {
  const fallback = useDefaultIO();
  const io = given ?? fallback;
  const notify = useNotify();
  const overlay = useOptionalOverlay();
  const [rules, setRules] = useState<ApprovalRuleView[] | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState('');
  // A card saved as a rule, or a rule used: the list again.
  useLiveUpdate(['approvals', 'runs'], () => setReload((value) => value + 1));
  useEffect(() => {
    if (!io) return;
    const request = new AbortController();
    io.list(request.signal)
      .then((page) => {
        if (request.signal.aborted) return;
        setRules(page.items);
        setError('');
      })
      .catch((cause) => {
        if (!request.signal.aborted) setError(clientError(cause).message);
      });
    return () => request.abort();
  }, [io, reload]);

  function replace(rule: ApprovalRuleView) {
    setRules((current) =>
      (current ?? []).map((item) =>
        item.rule_id === rule.rule_id ? rule : item,
      ),
    );
  }

  async function toggle(rule: ApprovalRuleView, enabled: boolean) {
    if (!io) return;
    setBusy(rule.rule_id);
    try {
      replace(await io.update(rule.rule_id, { enabled }));
    } catch (cause) {
      notify(clientError(cause).message, 'warning');
    } finally {
      setBusy('');
    }
  }

  async function remove(rule: ApprovalRuleView) {
    if (!io) return;
    setBusy(rule.rule_id);
    try {
      setRules((await io.remove(rule.rule_id)).items);
      notify('Rule deleted. That action asks again.');
    } catch (cause) {
      notify(clientError(cause).message, 'warning');
    } finally {
      setBusy('');
    }
  }

  function askToDelete(rule: ApprovalRuleView) {
    const confirm = () => {
      overlay?.close();
      void remove(rule);
    };
    if (!overlay) return confirm();
    overlay.open({
      kind: 'alert',
      title: 'Delete this rule?',
      description: `${rule.words}. The action asks again each time.`,
      confirmLabel: 'Delete rule',
      onConfirm: confirm,
    });
  }

  function edit(rule: ApprovalRuleView) {
    if (!io || !overlay) return;
    overlay.open({
      kind: 'sheet',
      title: 'Edit rule',
      description: 'Change the exact command this rule allows.',
      content: (
        <RuleEditSheet
          rule={rule}
          io={io}
          onSaved={(saved) => {
            overlay.close();
            replace(saved);
            notify('Rule saved.');
          }}
          onCancel={() => overlay.close()}
        />
      ),
    });
  }

  return (
    <SettingsGroup
      title="Always allow"
      anchor="always-allow"
      className="always-rules"
      note={NEVER_COVERED}
    >
      {error && (
        <ErrorState
          title="Your rules couldn’t be read"
          action={
            <Button
              className="small"
              onClick={() => {
                setError('');
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
      {rules === null && !error && <Skeleton label="Reading your rules" />}
      {rules !== null && !rules.length && !error && (
        <EmptyState title="No rules yet">
          When you approve something you do often, choose Always allow on its
          card.
        </EmptyState>
      )}
      {rules !== null && rules.length > 0 && (
        <ul className="always-rules-list">
          {rules.map((rule) => (
            <li
              key={rule.rule_id}
              id={`rule-${rule.rule_id}`}
              className="always-rule"
              data-off={rule.enabled ? undefined : true}
            >
              <span className="always-rule-what">
                <span className="always-rule-words">
                  <RuleWords rule={rule} />
                </span>
                <span className="always-rule-used">
                  {rule.enabled ? usedWords(rule) : `Off · ${usedWords(rule)}`}
                </span>
              </span>
              <span className="always-rule-tools">
                {rule.kind === 'command' && (
                  <IconButton
                    size="sm"
                    label="Edit rule"
                    disabled={busy === rule.rule_id}
                    onClick={() => edit(rule)}
                  >
                    <Pencil size={15} aria-hidden />
                  </IconButton>
                )}
                <IconButton
                  size="sm"
                  label="Delete rule"
                  disabled={busy === rule.rule_id}
                  onClick={() => askToDelete(rule)}
                >
                  <Trash2 size={15} aria-hidden />
                </IconButton>
                <Toggle
                  label={rule.enabled ? 'Rule on' : 'Rule off'}
                  checked={rule.enabled}
                  disabled={busy === rule.rule_id}
                  onChange={(event) =>
                    void toggle(rule, event.currentTarget.checked)
                  }
                />
              </span>
            </li>
          ))}
        </ul>
      )}
    </SettingsGroup>
  );
}

/** A command rule's exact command, checked again by the server; the dry run follows what's typed. */
function RuleEditSheet({
  rule,
  io,
  onSaved,
  onCancel,
}: {
  rule: ApprovalRuleView;
  io: RulesIO;
  onSaved: (rule: ApprovalRuleView) => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState(rule.command);
  const [command, setCommand] = useState(rule.command);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setCommand(typed.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [typed]);
  const { result, error } = useDryRun(
    command ? io.dryRun : null,
    { rule_id: rule.rule_id, command },
    command,
  );
  const changed = typed.trim() !== rule.command;
  async function save() {
    setSaving(true);
    setProblem('');
    try {
      onSaved(await io.update(rule.rule_id, { command: typed.trim() }));
    } catch (cause) {
      setProblem(clientError(cause).message);
      setSaving(false);
    }
  }
  return (
    <div className="always-allow stack">
      <p className="always-allow-words">
        <RuleWords rule={result?.rule ?? { ...rule, command: typed.trim() }} />
      </p>
      <label className="always-rule-command">
        <span>Exact command</span>
        <Input
          value={typed}
          maxLength={500}
          spellCheck={false}
          autoComplete="off"
          aria-describedby="always-rule-command-note"
          onChange={(event) => setTyped(event.currentTarget.value)}
        />
        <small id="always-rule-command-note">
          Only this exact text, in {rule.folder_name}. No other command, no
          patterns.
        </small>
      </label>
      <DryRunPanel result={result} error={error} />
      {problem && (
        <p role="alert" className="always-allow-problem">
          {problem}
        </p>
      )}
      <SheetActions>
        <Button disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={!changed || !typed.trim() || saving || Boolean(error)}
          onClick={() => void save()}
        >
          Save
        </Button>
      </SheetActions>
    </div>
  );
}
