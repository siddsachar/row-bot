import { useEffect, useState } from 'react';
import type {
  AccessPresetView,
  PlanAccess,
  PlanContinueRequest,
  PlanTool,
} from '../../api/types';

type AccessPresetId = AccessPresetView['id'];
import { Button, Disclosure, Select } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';

const PRESETS: [AccessPresetId, string, string][] = [
  ['read_only', 'Read only', 'Looks things up. Cannot change anything.'],
  [
    'ask',
    'Ask before changes',
    'Looks things up. Asks you before every change.',
  ],
  [
    'full',
    'Full access',
    'Makes routine changes without asking. Risky actions still ask.',
  ],
];

/** Which group a tool belongs to; the groups do not depend on the preset. */
export function groupOf(tool: PlanTool) {
  if (tool.always_asks) return 'Always asks first';
  return tool.effect === 'read_only' ? 'Looks things up' : 'Makes changes';
}

export function readable(tool: PlanTool) {
  return tool.title || tool.name;
}

export function ToolGroups({ tools }: { tools: PlanTool[] }) {
  return (
    <div className="access-groups">
      {['Looks things up', 'Makes changes', 'Always asks first'].map(
        (group) => {
          const members = tools.filter((tool) => groupOf(tool) === group);
          return members.length ? (
            <Disclosure
              key={group}
              summary={group}
              meta={String(members.length)}
            >
              <ul className="access-tools">
                {members.map((tool) => (
                  <li key={tool.name}>
                    <strong>{readable(tool)}</strong>
                    {tool.description && <small>{tool.description}</small>}
                  </li>
                ))}
              </ul>
            </Disclosure>
          ) : null;
        },
      )}
    </div>
  );
}

/** "Here's what {app} can do": a preset, readable tools, and optional per-tool choices. */
export default function AccessSheet({
  open,
  name,
  access,
  change,
  busy,
  onAllow,
  onCancel,
}: {
  open: boolean;
  name: string;
  access: PlanAccess | null;
  change: boolean;
  busy: boolean;
  onAllow: (choice: PlanContinueRequest) => void;
  onCancel: () => void;
}) {
  const [preset, setPreset] = useState<AccessPresetId>('ask');
  const [overrides, setOverrides] = useState<
    Record<string, 'use' | 'ask' | 'off'>
  >({});
  useEffect(() => {
    if (open && access) {
      const kept = access.preset === 'custom' || access.manual;
      setPreset(access.preset === 'custom' ? 'ask' : access.preset);
      // A custom policy, or tools chosen one by one, stay as they are until the person changes one.
      setOverrides(
        kept
          ? Object.fromEntries(
              access.tools.map((tool) => [tool.name, tool.state]),
            )
          : {},
      );
    }
  }, [open, access]);
  const tools = access?.tools ?? [];
  const manual = Boolean(access?.manual);
  const title = change
    ? `Change what ${name} can do`
    : `Here's what ${name} can do`;
  return (
    <ModalTask
      open={open}
      title={title}
      description={
        manual
          ? 'Turn on what it may do. You can change this later.'
          : 'Choose how much it can do on its own. You can change this later.'
      }
      onOpenChange={(value) => {
        if (!value) onCancel();
      }}
    >
      <div className="stack access-sheet">
        {manual ? (
          <p className="settings-help" role="status">
            {name}&apos;s tools overlap with what Row-Bot already does, so each
            stays off until you turn it on below.
          </p>
        ) : (
          <fieldset className="access-presets">
            <legend className="visually-hidden">Access</legend>
            {PRESETS.map(([id, label, description]) => (
              <label
                key={id}
                className="access-preset"
                data-selected={preset === id}
              >
                <input
                  type="radio"
                  name="access-preset"
                  value={id}
                  checked={preset === id}
                  onChange={() => {
                    // Picking a preset sets every tool from it; Customise can then adjust single tools.
                    setPreset(id);
                    setOverrides({});
                  }}
                  data-initial-focus={preset === id ? true : undefined}
                />
                <span>
                  <strong>{label}</strong>
                  <small>{description}</small>
                </span>
              </label>
            ))}
          </fieldset>
        )}
        {tools.length ? (
          <ToolGroups tools={tools} />
        ) : (
          <p className="settings-help">It has no tools to choose yet.</p>
        )}
        {tools.length > 0 && (
          <Disclosure summary="Customise" defaultOpen={manual}>
            <ul className="access-tools">
              {tools.map((tool) => (
                <li key={tool.name} className="access-tool-choice">
                  <span>
                    <strong>{readable(tool)}</strong>
                    <small>{groupOf(tool)}</small>
                  </span>
                  <Select
                    aria-label={`What ${readable(tool)} may do`}
                    value={overrides[tool.name] ?? ''}
                    onChange={(event) => {
                      const value = event.target.value as
                        'use' | 'ask' | 'off' | '';
                      const next = { ...overrides };
                      if (value) next[tool.name] = value;
                      else delete next[tool.name];
                      setOverrides(next);
                    }}
                  >
                    {!manual && <option value="">As chosen above</option>}
                    {!tool.always_asks && (
                      <option value="use">Use without asking</option>
                    )}
                    <option value="ask">Ask first</option>
                    <option value="off">Off</option>
                  </Select>
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
        <div className="app-dialog-actions">
          <Button onClick={onCancel}>Cancel</Button>
          <Button
            variant="primary"
            disabled={busy || !access}
            onClick={() =>
              access &&
              onAllow({ preset, overrides, tools_digest: access.tools_digest })
            }
          >
            {change ? 'Save' : 'Allow'}
          </Button>
        </div>
      </div>
    </ModalTask>
  );
}
