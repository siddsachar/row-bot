import { useState } from 'react';
import {
  Bot,
  Check,
  Code2,
  Command,
  Cpu,
  Info,
  Maximize,
  MessageSquare,
  Minus,
  Palette,
  PanelRight,
  Plus,
  Search,
  SquarePen,
  Zap,
} from 'lucide-react';
import {
  Button,
  Combobox,
  Disclosure,
  EmptyState,
  EntityList,
  EntityRow,
  ErrorState,
  Field,
  Hint,
  IconButton,
  InlineEmpty,
  Input,
  Kbd,
  Menu,
  Popup,
  Progress,
  Segmented,
  Select,
  SettingRow,
  Skeleton,
  Stat,
  StatGroup,
  StatusDot,
  Surface,
  Tabs,
  Toggle,
  Toolbar,
  ToolbarSeparator,
  type ComboboxOption,
} from '../../ui/primitives';
import { Drawer, useOverlay } from '../../ui/overlays';

const SAMPLE_MODELS: ComboboxOption[] = [
  ...['Sol', 'Terra', 'Luna', 'Mini'].map((name) => ({
    value: `sample-cloud:${name}`,
    label: `Sample ${name}`,
    group: 'Sample cloud',
    description: 'Hosted example model',
  })),
  ...['Small', 'Medium', 'Large'].map((name) => ({
    value: `sample-local:${name}`,
    label: `Local ${name}`,
    group: 'On this device',
    keywords: ['private', 'local'],
    description: 'Private · on device',
  })),
  {
    value: 'sample-offline:unavailable',
    label: 'Unavailable example',
    group: 'Not connected',
    disabled: true,
  },
];

function FoundationExamples() {
  const { notify } = useOverlay();
  const [filter, setFilter] = useState<
    'all' | 'chats' | 'designs' | 'code' | 'workflows'
  >('all');
  const [view, setView] = useState<'graph' | 'list'>('graph');
  const [model, setModel] = useState('sample-cloud:Sol');
  const [sendOnEnter, setSendOnEnter] = useState(true);
  const [inspector, setInspector] = useState(false);
  return (
    <Surface>
      <h2>Foundation primitives</h2>
      <p className="muted">
        Icons for verbs with a tooltip and shortcut, status as shape plus a
        word, and detail behind disclosure.
      </p>
      <div className="gallery-row" role="group" aria-label="Icon actions">
        <IconButton
          label="Search"
          shortcut="Mod+K"
          onClick={() => notify('Search opened')}
        >
          <Search size={16} aria-hidden />
        </IconButton>
        <IconButton label="New chat" shortcut="Mod+N">
          <SquarePen size={16} aria-hidden />
        </IconButton>
        <IconButton label="Toggle right panel" shortcut="Mod+Period" pressed>
          <PanelRight size={16} aria-hidden />
        </IconButton>
        <IconButton label="Small icon action" size="sm">
          <Plus size={14} aria-hidden />
        </IconButton>
        <IconButton label="Unavailable icon action" disabled>
          <Bot size={16} aria-hidden />
        </IconButton>
        <span className="gallery-kbd-sample">
          Palette <Kbd keys="Mod+K" /> · Approve <Kbd keys="Mod+Enter" /> · Stop{' '}
          <Kbd keys="Escape" />
        </span>
      </div>
      <div className="gallery-row" role="group" aria-label="Status examples">
        <StatusDot tone="success" label="Connected" showLabel />
        <StatusDot tone="accent" label="Running" showLabel pulse />
        <StatusDot tone="warning" label="Needs review" showLabel />
        <StatusDot tone="danger" label="Failed" showLabel />
        <StatusDot tone="neutral" label="Idle" showLabel />
      </div>
      <div className="gallery-row">
        <Segmented
          label="Conversation type"
          size="sm"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'All' },
            {
              value: 'chats',
              label: 'Chats',
              icon: <MessageSquare size={14} aria-hidden />,
              hideLabel: true,
            },
            {
              value: 'designs',
              label: 'Designs',
              icon: <Palette size={14} aria-hidden />,
              hideLabel: true,
            },
            {
              value: 'code',
              label: 'Code',
              icon: <Code2 size={14} aria-hidden />,
              hideLabel: true,
            },
            {
              value: 'workflows',
              label: 'Workflows',
              icon: <Zap size={14} aria-hidden />,
              hideLabel: true,
            },
          ]}
        />
        <Segmented
          label="Knowledge view"
          value={view}
          onChange={setView}
          options={[
            { value: 'graph', label: 'Graph' },
            { value: 'list', label: 'List' },
          ]}
        />
        <Combobox
          label="Sample model"
          icon={<Cpu size={16} aria-hidden />}
          value={model}
          onChange={setModel}
          options={SAMPLE_MODELS}
          footer={<span className="muted">Pinned and recent appear first</span>}
        />
      </div>
      <div className="gallery-settings">
        <SettingRow
          label="Send on Enter"
          description="Shift+Enter adds a new line."
        >
          <Toggle
            label="Send on Enter"
            checked={sendOnEnter}
            onChange={(event) => setSendOnEnter(event.target.checked)}
          />
        </SettingRow>
        <SettingRow
          label="Default appearance"
          description="Follows the operating system unless you choose one."
          htmlFor="gallery-appearance"
          modified
        >
          <Select id="gallery-appearance" defaultValue="system">
            <option value="system">System</option>
            <option value="dark">Dark</option>
            <option value="light">Light</option>
          </Select>
        </SettingRow>
        <Disclosure summary="Advanced" meta="1 setting">
          <SettingRow
            label="Example timeout"
            description="Advanced options stay one click away."
            htmlFor="gallery-timeout"
          >
            <Input id="gallery-timeout" defaultValue="30s" />
          </SettingRow>
        </Disclosure>
      </div>
      <EntityList label="Sample providers">
        <EntityRow
          title="Sample Cloud"
          icon={<Cpu size={16} />}
          status={{ tone: 'success', label: 'Connected' }}
          meta="API key · 12 models"
          action={<Button variant="ghost">Configure</Button>}
          menu={[
            { label: 'Refresh models', onSelect: () => notify('Refreshed') },
            { label: 'Disconnect', danger: true, onSelect: () => undefined },
          ]}
          details={<p>Key saved · ····c99 · Replace</p>}
        />
        <EntityRow
          title="Local runtime"
          icon={<Bot size={16} />}
          status={{ tone: 'warning', label: 'Not running' }}
          meta="Private · on device"
          action={<Button variant="ghost">Start</Button>}
        />
      </EntityList>
      <StatGroup label="Sample totals">
        <Stat label="Memories" value="658" delta="+12 today" tone="success" />
        <Stat label="Links" value="1,016" />
        <Stat label="Median reply" value="8.4" unit="s" />
      </StatGroup>
      <InlineEmpty
        icon={<Info size={14} />}
        action={<Button variant="ghost">Add resource</Button>}
      >
        Nothing attached to this sample yet.
      </InlineEmpty>
      <div className="gallery-canvas" role="group" aria-label="Sample canvas">
        <Toolbar label="Sample canvas controls" floating placement="top-left">
          <IconButton label="Zoom out" size="sm">
            <Minus size={14} aria-hidden />
          </IconButton>
          <IconButton label="Zoom in" size="sm">
            <Plus size={14} aria-hidden />
          </IconButton>
          <ToolbarSeparator />
          <IconButton label="Fit to screen" size="sm">
            <Maximize size={14} aria-hidden />
          </IconButton>
        </Toolbar>
        <Button
          className="gallery-canvas-node"
          onClick={() => setInspector(true)}
        >
          Open sample inspector
        </Button>
        <Drawer
          open={inspector}
          onOpenChange={setInspector}
          title="Sample inspector"
          description="Details for the selected canvas item"
        >
          <StatGroup label="Sample inspector totals">
            <Stat label="Connections" value="7" />
            <Stat label="Updated" value="2 min ago" />
          </StatGroup>
          <p>Inspectors keep the canvas interactive and close with Escape.</p>
        </Drawer>
      </div>
    </Surface>
  );
}

function ExampleForm() {
  const [name, setName] = useState('A useful idea');
  const { open, notify } = useOverlay();
  return (
    <div className="stack">
      <Field label="Example name">
        <Input value={name} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Menu
        label="Example actions"
        actions={[
          {
            label: 'Keep this idea',
            onSelect: () => notify('Idea kept in this example'),
          },
        ]}
      />
      <Popup label="Dialog help">
        <p>This popover belongs to the active dialog.</p>
      </Popup>
      <Button
        onClick={() =>
          open({
            kind: 'alert',
            title: 'Discard this example?',
            description:
              'This confirms a sample action. No files or conversations will change.',
            confirmLabel: 'Discard example',
            onConfirm: () => notify('Example discarded'),
          })
        }
      >
        Discard example
      </Button>
      {Array.from({ length: 8 }, (_, index) => (
        <p key={index}>
          Sample content {index + 1}. The body scrolls while actions remain
          available.
        </p>
      ))}
    </div>
  );
}
export default function Gallery() {
  const [tab, setTab] = useState('controls');
  const { open, notify } = useOverlay();
  const [command, setCommand] = useState('');
  return (
    <section className="gallery stack" aria-label="Component gallery">
      <div>
        <span className="eyebrow">Shared product system</span>
        <h1>Component gallery</h1>
        <p className="muted">
          The same controls, states and surfaces across your workspace.
        </p>
      </div>
      <FoundationExamples />
      <Surface>
        <h2>Buttons and inputs</h2>
        <div className="inline-actions">
          <Button variant="primary">
            <Check size={18} aria-hidden />
            Primary action
          </Button>
          <Button>Secondary action</Button>
          <Button variant="ghost">Quiet action</Button>
          <Button variant="danger">Destructive action</Button>
          <Button disabled>Unavailable</Button>
          <Hint label="Helpful detail">
            <Button iconOnly aria-label="Helpful detail">
              <Info size={18} aria-hidden />
            </Button>
          </Hint>
        </div>
        <div className="field-row">
          <Field label="Example input">
            <Input placeholder="Write a short label" />
          </Field>
          <Field label="Example selection">
            <Select defaultValue="first">
              <option value="first">First choice</option>
              <option value="second">Second choice</option>
            </Select>
          </Field>
          <Field label="Unavailable input">
            <Input disabled value="Unavailable" readOnly />
          </Field>
        </div>
      </Surface>
      <Surface>
        <h2>Navigation and floating surfaces</h2>
        <Tabs
          label="Gallery examples"
          value={tab}
          onChange={setTab}
          items={[
            {
              id: 'controls',
              label: 'Controls',
              content: <p>Consistent controls with visible focus states.</p>,
            },
            {
              id: 'states',
              label: 'States',
              content: <p>Keyboard arrows switch these tabs.</p>,
            },
          ]}
        />
        <div className="inline-actions">
          <Menu
            label="Sample menu"
            actions={[
              {
                label: 'First action',
                onSelect: () => notify('First action selected'),
              },
              {
                label: 'Unavailable action',
                onSelect: () => undefined,
                disabled: true,
              },
            ]}
          />
          <Menu
            label="Long sample menu"
            actions={Array.from({ length: 60 }, (_, index) => ({
              label: `Sample option ${index + 1}`,
              selected: index === 44,
              onSelect: () => notify(`Sample option ${index + 1} selected`),
            }))}
          />
          <Popup label="Sample popover">
            <p>Supporting detail stays close to its control.</p>
          </Popup>
          <Button
            onClick={() =>
              open({
                title: 'Sample dialog',
                description: 'A scrollable example with a persistent form.',
                content: <ExampleForm />,
              })
            }
          >
            Open dialog
          </Button>
          <Button
            onClick={() =>
              open({
                kind: 'sheet',
                title: 'Sample sheet',
                description: 'A compact surface for a focused task.',
                content: <ExampleForm />,
              })
            }
          >
            Open sheet
          </Button>
          <Button
            onClick={() =>
              open({
                kind: 'alert',
                title: 'Confirm sample action?',
                description: 'This is a demonstration. No data will change.',
                confirmLabel: 'Confirm sample',
                onConfirm: () => notify('Sample action confirmed'),
              })
            }
          >
            Open alert dialog
          </Button>
          <Button onClick={() => notify('Your example preference is saved')}>
            Show toast
          </Button>
        </div>
      </Surface>
      <Surface>
        <h2>Command surface</h2>
        <Field label="Find a command">
          <Input
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            placeholder="Search sample commands"
          />
        </Field>
        <ul className="command-list" aria-label="Sample commands">
          {['Open sample dialog', 'Show notification']
            .filter((label) =>
              label.toLowerCase().includes(command.toLowerCase()),
            )
            .map((label) => (
              <li key={label}>
                <Button
                  variant="ghost"
                  onClick={() =>
                    label.startsWith('Open')
                      ? open({
                          title: 'Sample dialog',
                          description: 'Command example',
                          content: <ExampleForm />,
                        })
                      : notify('Command completed')
                  }
                >
                  <Command size={18} aria-hidden />
                  {label}
                </Button>
              </li>
            ))}
        </ul>
      </Surface>
      <Surface>
        <h2>Feedback and progress</h2>
        <div className="status-examples">
          {['info', 'success', 'warning', 'danger'].map((status) => (
            <p key={status} className={`status-example ${status}`}>
              {status}: a clear text label accompanies colour.
            </p>
          ))}
        </div>
        <Progress label="Example progress" value={65} />
        <Skeleton label="Loading example" />
        <ErrorState
          title="Unable to load example"
          action={
            <Button onClick={() => notify('Retry requested')}>Try again</Button>
          }
        >
          A clear explanation and a safe next step.
        </ErrorState>
        <EmptyState title="Nothing here yet">
          Useful context appears here when it becomes available.
        </EmptyState>
      </Surface>
      <Surface>
        <h2>Code, changes and charts</h2>
        <pre className="code-sample">
          <code>
            <span className="syntax-keyword">const</span> message ={' '}
            <span className="syntax-string">'Hello, workspace'</span>;<br />
            <span className="code-comment">// A readable code surface</span>
          </code>
        </pre>
        <div className="diff-sample">
          <p className="diff-remove">− Removed example line</p>
          <p className="diff-add">+ Added example line</p>
          <p className="diff-change">~ Changed example line</p>
        </div>
        <div
          className="chart-example"
          role="img"
          aria-label="Sample bar chart: three, five, two, four, six and three"
        >
          <div className="chart-bars">
            {[3, 5, 2, 4, 6, 3].map((value, index) => (
              <span
                key={index}
                style={{
                  height: `${value * 16}px`,
                  background: `var(--chart-series-${index + 1})`,
                }}
              >
                <span>{value}</span>
              </span>
            ))}
          </div>
          <p>1 · 2 · 3 · 4 · 5 · 6</p>
        </div>
        <div className="artifact-example">
          <div>Sample artifact surface</div>
        </div>
      </Surface>
    </section>
  );
}
