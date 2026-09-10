import {
  lazy,
  memo,
  Suspense,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import * as DockTabs from '@radix-ui/react-tabs';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Group, Panel, Separator, usePanelRef } from 'react-resizable-panels';
import {
  ChevronLeft,
  Columns3,
  MessageSquare,
  PanelLeft,
  Search,
  Settings,
  X,
} from 'lucide-react';
import {
  Brand,
  Button,
  EmptyState,
  ErrorState,
  Hint,
  Menu,
  Skeleton,
} from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import { useClientSelector, useRuntime } from '../../runtime';
import {
  closeAllPanels,
  closePanel,
  focusPanel,
  movePanel,
  openPanel,
  panelKey,
  panelPresentation,
  panelRegistry,
  panelStatus,
  regionBounds,
  resetLayout,
  resizeRegion,
  samplePanels,
  toggleRegion,
  type PanelInstance,
  type PanelLayout,
  type PanelPlacement,
} from '../panels/model';
import { PanelSubscriptions } from '../panels/subscriptions';
import { useWorkspaceLayout } from './layout';
import Commands from './Commands';
import Navigation from './Navigation';
import Home, { type HomeSetupEntry } from './Home';
import SearchConversations from './SearchConversations';
import useNewChat from './useNewChat';
import { reconcilePanelPresentation } from '../panels/presentation';
import Conversation from './Conversation';
import ResourceSetup from './ResourceSetup';
import ResourcePanel from '../panels/ResourcePanel';

const Preferences = lazy(() => import('../settings/Preferences'));
const subscriptions = new PanelSubscriptions();
export const panelMetrics = Object.assign(subscriptions.metrics, {
  renders: 0,
});

const SamplePanel = memo(function SamplePanel({
  panel,
  visible,
}: {
  panel: PanelInstance;
  visible: boolean;
}) {
  if (import.meta.env.VITE_ENABLE_FIXTURES === '1') panelMetrics.renders += 1;
  const { controller } = useRuntime();
  const [revision, setRevision] = useState('0');
  useEffect(() => {
    const observer = subscriptions.acquire(
      panelKey(panel.descriptor),
      (notify) => {
        const publish = () => notify(panel.descriptor.resource_revision ?? '1');
        publish();
        return controller.subscribe(publish);
      },
      setRevision,
      visible,
    );
    return observer.release;
  }, [controller, panel.descriptor, visible]);
  const status = panelStatus(panel.descriptor, {
    capabilities: new Set(),
    resources: new Map(),
  });
  if (status !== 'ready')
    return (
      <EmptyState title={`Panel ${status.replaceAll('-', ' ')}`}>
        The resource is not available in this workspace. Close the panel or try
        again when access is restored.
      </EmptyState>
    );
  return (
    <div className="sample-panel stack">
      <span className="eyebrow">Sample panel</span>
      <h2>{panel.descriptor.title}</h2>
      <p>
        {panel.descriptor.panel_kind === 'fake.info'
          ? 'Keep useful context beside your conversation. These sample notes stay separate from your conversations and files.'
          : 'Activity will appear here as you work. This preview uses a local sample and does not start any tasks.'}
      </p>
      <div className="sample-card">
        <MessageSquare size={20} aria-hidden />
        <div>
          <strong>Your conversation stays in place</strong>
          <p className="muted">Move, resize or close this panel at any time.</p>
        </div>
      </div>
      <small>View revision {revision}</small>
    </div>
  );
});

function PanelContent({
  panel,
  visible,
}: {
  panel: PanelInstance;
  visible: boolean;
}) {
  return panel.descriptor.panel_kind === 'artifact.preview' ||
    panel.descriptor.panel_kind === 'workspace.inspector' ? (
    <ResourcePanel panel={panel} visible={visible} />
  ) : import.meta.env.VITE_ENABLE_FIXTURES === '1' ? (
    <SamplePanel panel={panel} visible={visible} />
  ) : (
    <EmptyState title="Panel unavailable">
      Choose a bound resource to open its panel.
    </EmptyState>
  );
}

export default function Workspace() {
  const state = {
    status: useClientSelector((value) => value.status),
    error: useClientSelector((value) => value.error),
    handshake: useClientSelector((value) => value.handshake),
    workspace: useClientSelector((value) => value.workspace),
    selectedConversationId: useClientSelector(
      (value) => value.selectedConversationId,
    ),
    loadingConversation: useClientSelector(
      (value) => value.loadingConversation,
    ),
    suggestions: useClientSelector((value) => value.suggestions),
  };
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const location = useLocation();
  const navigate = useNavigate();
  const routeConversation = /^\/conversations\/([^/]+)$/.exec(
    location.pathname,
  )?.[1];
  const conversationId = routeConversation
    ? decodeURIComponent(routeConversation)
    : null;
  const homeOpen = location.pathname === '/';
  const routeOpen = !homeOpen && !routeConversation;
  const [layout, setLayout] = useWorkspaceLayout(
    state.handshake?.instance_id,
    conversationId ?? 'home',
  );
  const creation = useNewChat();
  const pendingPanel = useRef<{
    descriptor: (typeof samplePanels)[number];
    selectionVersion: number;
    originRouteKey: string;
    instance: string | undefined;
  } | null>(null);
  const presentationScope = useRef({
    conversationId,
    routeKey: location.key,
    setLayout,
  });
  useLayoutEffect(() => {
    presentationScope.current = {
      conversationId,
      routeKey: location.key,
      setLayout,
    };
  });
  const restoredScope = useRef('');
  const currentLayout = useRef(layout);
  useLayoutEffect(() => {
    currentLayout.current = layout;
  }, [layout]);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const openPanelRef = useRef<HTMLButtonElement>(null);
  const panelFocusPending = useRef(false);
  const navRef = usePanelRef();
  const sideRef = usePanelRef();
  const bottomRef = usePanelRef();
  const desktop = layout.widthClass === 'desktop';
  const sidePanels = layout.panels.filter(
    (panel) =>
      panel.placement === 'side' &&
      (!panel.descriptor.resource_ref ||
        panel.descriptor.resource_ref.startsWith(
          `${state.selectedConversationId}:`,
        )),
  );
  const bottomPanels = layout.panels.filter(
    (panel) =>
      panel.placement === 'bottom' &&
      (!panel.descriptor.resource_ref ||
        panel.descriptor.resource_ref.startsWith(
          `${state.selectedConversationId}:`,
        )),
  );
  const sideVisible =
    Boolean(conversationId) &&
    desktop &&
    sidePanels.length > 0 &&
    !layout.side.collapsed;
  const bottomVisible =
    Boolean(conversationId) &&
    desktop &&
    bottomPanels.length > 0 &&
    !layout.bottom.collapsed;
  const compact =
    conversationId && !desktop
      ? layout.panels.find(
          (panel) =>
            panel.instance_id === layout.activePanelId &&
            (!panel.descriptor.resource_ref ||
              panel.descriptor.resource_ref.startsWith(
                `${state.selectedConversationId}:`,
              )) &&
            panelPresentation(layout, panel) === 'tab',
        )
      : undefined;
  useEffect(() => {
    if (
      routeConversation &&
      routeConversation !== controller.getSnapshot().selectedConversationId
    )
      void controller.selectConversation(decodeURIComponent(routeConversation));
  }, [controller, routeConversation]);
  const reconcileResources = useEffectEvent(() => {
    const workspace = state.workspace;
    if (
      !conversationId ||
      state.loadingConversation ||
      workspace?.conversation_id !== conversationId ||
      state.selectedConversationId !== conversationId
    )
      return;
    const scope = JSON.stringify([
      state.handshake?.instance_id,
      conversationId,
    ]);
    const source = restoredScope.current === scope ? 'update' : 'restore';
    restoredScope.current = scope;
    const result = reconcilePanelPresentation(currentLayout.current, {
      conversationId,
      activeConversationId: state.selectedConversationId,
      resources: workspace.resources,
      hints: state.suggestions
        .filter((item) => item.conversation_id === conversationId)
        .map((item) => item.descriptor),
      source,
    });
    currentLayout.current = result.layout;
    setLayout(result.layout);
    if (result.available.length)
      overlay.notify(
        `${result.available.map((item) => item.title).join(', ')} available in panels`,
      );
    const waiting = pendingPanel.current;
    if (waiting?.descriptor.resource_ref?.startsWith(`${conversationId}:`)) {
      pendingPanel.current = null;
      showPanel(waiting.descriptor);
    }
  });
  useEffect(() => {
    const waiting = pendingPanel.current;
    if (
      waiting &&
      (waiting.selectionVersion !== controller.getSelectionVersion() ||
        waiting.instance !== state.handshake?.instance_id ||
        (waiting.originRouteKey !== location.key &&
          !waiting.descriptor.resource_ref?.startsWith(`${conversationId}:`)))
    )
      pendingPanel.current = null;
    reconcileResources();
  }, [
    controller,
    location.key,
    conversationId,
    state.workspace,
    state.suggestions,
    state.loadingConversation,
    state.selectedConversationId,
    state.handshake?.instance_id,
  ]);
  function preferences() {
    overlay.open({
      title: 'Preferences',
      description: 'Make this workspace your own.',
      content: (
        <Suspense fallback={<Skeleton />}>
          <Preferences onReset={() => update(resetLayout)} />
        </Suspense>
      ),
    });
  }
  function setup(entry?: HomeSetupEntry) {
    overlay.open({
      title: 'New or open resource',
      description: 'Create a Deck or register an existing coding folder.',
      content: (
        <ResourceSetup
          conversationId={null}
          onPanel={showPanel}
          initialEntry={entry}
        />
      ),
    });
  }
  const closeCompactSheet = useEffectEvent(() =>
    overlay.dismiss('workspace-panel'),
  );
  useEffect(() => {
    if (desktop) closeCompactSheet();
  }, [desktop]);
  function openCommands() {
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    overlay.open({
      title: 'Workspace commands',
      description: 'Find an action. Press Escape to return to your workspace.',
      content: (
        <Commands
          commands={[
            {
              label: 'Home',
              keywords: 'start library',
              run: () => {
                overlay.close();
                navigate('/');
              },
            },
            {
              label: 'New chat',
              keywords: 'new conversation',
              run: () => {
                overlay.close();
                void creation.newChat();
              },
            },
            ...(import.meta.env.VITE_ENABLE_FIXTURES === '1'
              ? samplePanels
              : []
            ).map((panel) => ({
              label: `Open ${panel.title}`,
              run: () => {
                overlay.close();
                showPanel(panel, opener);
              },
            })),
            {
              label: 'Preferences',
              keywords: 'appearance theme settings',
              run: () =>
                overlay.open({
                  title: 'Preferences',
                  description: 'Make this workspace your own.',
                  content: (
                    <Suspense fallback={<Skeleton />}>
                      <Preferences onReset={() => update(resetLayout)} />
                    </Suspense>
                  ),
                }),
            },
            {
              label: 'Reset layout',
              run: () =>
                overlay.open({
                  kind: 'alert',
                  title: 'Reset layout?',
                  description: 'Restore panel sizes and close panels.',
                  confirmLabel: 'Reset layout',
                  onConfirm: () => update(resetLayout),
                }),
            },
          ]}
        />
      ),
    });
  }
  const commandShortcut = useEffectEvent((event: globalThis.KeyboardEvent) => {
    if (
      !event.isComposing &&
      !event.altKey &&
      !event.shiftKey &&
      !event.repeat &&
      (event.ctrlKey || event.metaKey) &&
      event.key.toLowerCase() === 'k'
    ) {
      event.preventDefault();
      openCommands();
    }
  });
  useEffect(() => {
    const keydown = (event: globalThis.KeyboardEvent) => commandShortcut(event);
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, []);
  useEffect(() => {
    const visibility = () =>
      controller.setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', visibility);
    visibility();
    return () => document.removeEventListener('visibilitychange', visibility);
  }, [controller]);
  useEffect(() => {
    navRef.current?.resize(
      desktop ? (layout.navigation.collapsed ? 48 : layout.navigation.size) : 0,
    );
    sideRef.current?.resize(sideVisible ? layout.side.size : 0);
    bottomRef.current?.resize(bottomVisible ? layout.bottom.size : 0);
  }, [
    desktop,
    sideVisible,
    bottomVisible,
    layout.navigation.collapsed,
    layout.navigation.size,
    layout.side.size,
    layout.bottom.size,
    navRef,
    sideRef,
    bottomRef,
  ]);
  const update = (action: (previous: PanelLayout) => PanelLayout) =>
    setLayout(action);
  function settleResize(
    previous: PanelLayout,
    region: 'navigation' | PanelPlacement,
    pixels: number | undefined,
  ): PanelLayout {
    if (pixels === undefined || !Number.isFinite(pixels)) return previous;
    const collapsedSize = region === 'navigation' ? 48 : 0;
    if (Math.abs(pixels - collapsedSize) < 0.5)
      return previous[region].collapsed
        ? previous
        : toggleRegion(previous, region);
    return pixels >= regionBounds(previous, region).min - 0.5
      ? resizeRegion(previous, region, pixels)
      : previous;
  }
  function showPanel(
    panel: (typeof samplePanels)[number],
    opener?: HTMLElement | null,
  ) {
    // Command contents stay mounted while the workspace can change breakpoint.
    // Read the current layout when the action runs, not when it was opened.
    const snapshot = controller.getSnapshot();
    const scope = presentationScope.current;
    const target = panel.resource_ref?.slice(
      0,
      panel.resource_ref.lastIndexOf(':'),
    );
    if (
      target &&
      (snapshot.workspace?.conversation_id !== target ||
        scope.conversationId !== target)
    ) {
      if (snapshot.selectedConversationId === target)
        pendingPanel.current = {
          descriptor: panel,
          selectionVersion: controller.getSelectionVersion(),
          originRouteKey: scope.routeKey,
          instance: snapshot.handshake?.instance_id,
        };
      return;
    }
    const next = target
      ? reconcilePanelPresentation(currentLayout.current, {
          conversationId: target,
          activeConversationId: snapshot.selectedConversationId,
          resources: snapshot.workspace?.resources ?? [],
          source: 'explicit',
          descriptor: panel,
        }).layout
      : openPanel(currentLayout.current, panel);
    currentLayout.current = next;
    scope.setLayout(next);
    const instance = next.panels.find(
      (value) => value.instance_id === next.activePanelId,
    );
    if (!instance || panelKey(instance.descriptor) !== panelKey(panel)) {
      overlay.notify(
        'This resource view changed. Open its current binding to continue.',
      );
      return;
    }
    if (next.widthClass !== 'desktop') {
      if (panelPresentation(next, instance) === 'sheet')
        overlay.open({
          kind: 'sheet',
          key: 'workspace-panel',
          title: panel.title,
          description:
            panelRegistry[panel.panel_kind as keyof typeof panelRegistry]
              ?.title ?? 'Resource panel',
          content: <PanelContent panel={instance} visible />,
          returnFocusTo: opener,
        });
      else
        scope.setLayout((previous) =>
          focusPanel(previous, instance.instance_id),
        );
    }
  }
  function keyboardResize(
    event: KeyboardEvent,
    region: 'navigation' | PanelPlacement,
  ) {
    if (
      ![
        'ArrowLeft',
        'ArrowRight',
        'ArrowUp',
        'ArrowDown',
        'Home',
        'End',
        'Enter',
      ].includes(event.key)
    )
      return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === 'Enter' && region !== 'navigation')
      openPanelRef.current?.focus({ preventScroll: true });
    update((previous) => {
      if (event.key === 'Enter') return toggleRegion(previous, region);
      const bounds = regionBounds(previous, region);
      const delta =
        (event.shiftKey ? 48 : 16) *
        (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1) *
        (region === 'navigation' ? 1 : -1);
      return resizeRegion(
        previous,
        region,
        event.key === 'Home'
          ? bounds.min
          : event.key === 'End'
            ? bounds.max
            : previous[region].size + delta,
      );
    });
  }
  function pane(panel: PanelInstance) {
    const isVisible =
      panel.instance_id ===
      (layout.panels.find(
        (value) =>
          value.placement === panel.placement &&
          value.instance_id === layout.activePanelId,
      )?.instance_id ??
        layout.panels.find((value) => value.placement === panel.placement)
          ?.instance_id);
    return (
      <DockTabs.Content
        key={panel.instance_id}
        value={panel.instance_id}
        forceMount
        className="panel-content"
        hidden={!isVisible}
      >
        <PanelContent
          panel={panel}
          visible={isVisible && desktop && !layout[panel.placement].collapsed}
        />
      </DockTabs.Content>
    );
  }
  function dock(panels: PanelInstance[], placement: PanelPlacement) {
    const active =
      panels.find((panel) => panel.instance_id === layout.activePanelId) ??
      panels[0];
    return (
      <DockTabs.Root
        asChild
        value={active?.instance_id ?? ''}
        onValueChange={(id) => update((previous) => focusPanel(previous, id))}
      >
        <section
          className="dock"
          aria-label={`${placement === 'side' ? 'Side' : 'Bottom'} panels`}
        >
          <header className="dock-header">
            <DockTabs.List
              className="dock-tabs"
              aria-label={`${placement} panel tabs`}
            >
              {panels.map((panel) => (
                <DockTabs.Trigger
                  asChild
                  value={panel.instance_id}
                  key={panel.instance_id}
                >
                  <Button>{panel.descriptor.title}</Button>
                </DockTabs.Trigger>
              ))}
            </DockTabs.List>
            {active && (
              <Menu
                label="Panel actions"
                focusAfterClose={() => {
                  if (!panelFocusPending.current) return null;
                  panelFocusPending.current = false;
                  return (
                    workspaceRef.current?.querySelector<HTMLElement>(
                      '[role="tab"][aria-selected="true"]',
                    ) ?? openPanelRef.current
                  );
                }}
                actions={[
                  {
                    label: `Move to ${placement === 'side' ? 'bottom' : 'side'}`,
                    onSelect: () => {
                      panelFocusPending.current = true;
                      update((previous) =>
                        movePanel(
                          previous,
                          active.instance_id,
                          placement === 'side' ? 'bottom' : 'side',
                        ),
                      );
                    },
                  },
                  {
                    label: 'Make panel smaller',
                    onSelect: () =>
                      update((previous) =>
                        resizeRegion(
                          previous,
                          placement,
                          previous[placement].size - 48,
                        ),
                      ),
                  },
                  {
                    label: 'Make panel larger',
                    onSelect: () =>
                      update((previous) =>
                        resizeRegion(
                          previous,
                          placement,
                          previous[placement].size + 48,
                        ),
                      ),
                  },
                  {
                    label: 'Collapse panel',
                    onSelect: () => {
                      panelFocusPending.current = true;
                      update((previous) => toggleRegion(previous, placement));
                    },
                  },
                  {
                    label: 'Open another copy',
                    onSelect: () =>
                      update((previous) =>
                        openPanel(previous, active.descriptor, placement, true),
                      ),
                  },
                  {
                    label: 'Close panel',
                    onSelect: () => {
                      panelFocusPending.current = true;
                      update((previous) =>
                        closePanel(previous, active.instance_id),
                      );
                    },
                  },
                ]}
              />
            )}
          </header>
          {panels.map(pane)}
        </section>
      </DockTabs.Root>
    );
  }
  const navigation = (
    <Navigation
      onNewChat={() => void creation.newChat()}
      creatingChat={creation.creatingChat}
      onPreferences={desktop ? preferences : undefined}
      onOpenConversation={() =>
        update((previous) =>
          previous.widthClass !== 'desktop' && previous.activePanelId !== null
            ? focusPanel(previous, null)
            : previous,
        )
      }
    />
  );
  return (
    <div className="workspace" ref={workspaceRef}>
      <a className="skip-link" href="#conversation">
        Skip to conversation
      </a>
      <header className="app-header">
        <Brand compact />
        <div className="header-actions">
          <Button
            className="command-trigger"
            aria-label="Workspace commands"
            onClick={openCommands}
          >
            <Search className="compact-command-icon" size={20} aria-hidden />
            <span className="wide-label">Commands</span>
            <span className="shortcut-label" aria-hidden>
              ⌘/Ctrl K
            </span>
          </Button>
          <Hint label="Toggle navigation">
            <Button
              iconOnly
              aria-label="Toggle navigation"
              variant="ghost"
              onClick={() =>
                desktop
                  ? update((previous) => toggleRegion(previous, 'navigation'))
                  : overlay.open({
                      kind: 'drawer',
                      title: 'Conversations',
                      description: 'Choose a conversation',
                      content: navigation,
                    })
              }
            >
              <PanelLeft size={20} aria-hidden />
            </Button>
          </Hint>
          <Menu
            label="Open panel"
            triggerRef={openPanelRef}
            actions={[
              ...(state.workspace?.resources ?? []).map((resource) => ({
                panel_kind:
                  resource.binding.kind === 'artifact'
                    ? 'artifact.preview'
                    : 'workspace.inspector',
                title: resource.title.slice(0, 160),
                resource_ref: resource.resource_ref,
                resource_kind: resource.binding.kind,
                resource_revision: resource.resource_revision,
              })),
              ...(import.meta.env.VITE_ENABLE_FIXTURES === '1'
                ? samplePanels
                : []),
            ].map((panel) => ({
              label: panel.title,
              onSelect: (opener) => showPanel(panel, opener),
            }))}
          >
            <Columns3 size={18} aria-hidden />
            <span className="wide-label">Open panel</span>
          </Menu>
          <Button
            aria-label="New resource"
            disabled={state.status !== 'ready'}
            onClick={() =>
              overlay.open({
                title: 'New or open resource',
                description:
                  'Create a Deck or register an existing coding folder.',
                content: (
                  <ResourceSetup conversationId={null} onPanel={showPanel} />
                ),
              })
            }
          >
            <Columns3 size={18} aria-hidden />
            <span className="wide-label">New resource</span>
          </Button>
          {!desktop && (
            <Hint label="Preferences">
              <Button
                iconOnly
                aria-label="Preferences"
                variant="ghost"
                onClick={() =>
                  overlay.open({
                    title: 'Preferences',
                    description: 'Make this workspace your own.',
                    content: (
                      <Suspense fallback={<Skeleton />}>
                        <Preferences onReset={() => update(resetLayout)} />
                      </Suspense>
                    ),
                  })
                }
              >
                <Settings size={20} aria-hidden />
              </Button>
            </Hint>
          )}
        </div>
      </header>
      {creation.error && (
        <aside className="shell-recovery" role="alert">
          <span>{creation.error}</span>
          {creation.pending && (
            <Button
              disabled={creation.creatingChat}
              onClick={() => void creation.newChat()}
            >
              Check new chat
            </Button>
          )}
          {creation.canReview && (
            <Button onClick={creation.reviewMissingReceipt}>
              Review pending receipt
            </Button>
          )}
        </aside>
      )}
      {homeOpen && state.error && (
        <ErrorState
          title={
            state.status === 'incompatible'
              ? 'Client update needed'
              : 'Connect to continue'
          }
          action={
            state.error.recovery === 'retry' ? (
              <Button onClick={() => void controller.reconnect()}>
                Reconnect
              </Button>
            ) : (
              <a className="button" href="/">
                Open current application
              </a>
            )
          }
        >
          {state.error.message}
        </ErrorState>
      )}
      <Group
        id="workspace-columns"
        className="workspace-columns"
        orientation="horizontal"
        resizeTargetMinimumSize={{ fine: 12, coarse: 44 }}
        onLayoutChanged={(_, meta) => {
          if (meta.isUserInteraction && desktop) {
            const size = navRef.current?.getSize().inPixels;
            const side = sideRef.current?.getSize().inPixels;
            if (sideVisible && side !== undefined && side < 0.5)
              openPanelRef.current?.focus({ preventScroll: true });
            update((previous) => {
              let next = settleResize(previous, 'navigation', size);
              if (sideVisible) next = settleResize(next, 'side', side);
              return next;
            });
          }
        }}
      >
        <Panel
          id="navigation-pane"
          panelRef={navRef}
          minSize={desktop ? 200 : 0}
          maxSize={desktop ? 320 : 0}
          defaultSize={desktop ? layout.navigation.size : 0}
          collapsible
          collapsedSize={desktop ? 48 : 0}
          className={
            layout.navigation.collapsed
              ? 'navigation-pane collapsed'
              : 'navigation-pane'
          }
        >
          {desktop &&
            (layout.navigation.collapsed ? (
              <Button
                iconOnly
                aria-label="Expand navigation"
                onClick={() =>
                  update((previous) => toggleRegion(previous, 'navigation'))
                }
              >
                <PanelLeft size={18} aria-hidden />
              </Button>
            ) : (
              navigation
            ))}
        </Panel>
        {desktop && (
          <Separator
            className="resize-handle"
            aria-label="Resize navigation"
            onKeyDownCapture={(event) => keyboardResize(event, 'navigation')}
          />
        )}
        <Panel id="conversation-area" minSize={desktop ? 400 : 0}>
          <Group
            id="workspace-rows"
            orientation="vertical"
            resizeTargetMinimumSize={{ fine: 12, coarse: 44 }}
            onLayoutChanged={(_, meta) => {
              if (meta.isUserInteraction && bottomVisible) {
                const size = bottomRef.current?.getSize().inPixels;
                if (size !== undefined && size < 0.5)
                  openPanelRef.current?.focus({ preventScroll: true });
                update((previous) => settleResize(previous, 'bottom', size));
              }
            }}
          >
            <Panel id="conversation-pane" minSize={desktop ? 240 : 0}>
              <main className="conversation-area">
                <section
                  id="conversation"
                  tabIndex={-1}
                  data-testid="conversation-workspace"
                  className="conversation"
                  aria-label="Conversation"
                  hidden={homeOpen || routeOpen || Boolean(compact)}
                >
                  <div className="conversation-heading">
                    <span
                      className={`connection-status ${state.status === 'ready' ? 'connected' : ''}`}
                      role="status"
                    >
                      {state.status === 'ready' ? 'Connected' : state.status}
                    </span>
                  </div>
                  {state.status === 'loading' || state.loadingConversation ? (
                    <Skeleton label="Opening conversation" />
                  ) : state.error ? (
                    <ErrorState
                      title={
                        state.status === 'incompatible'
                          ? 'Client update needed'
                          : state.status === 'unauthorized'
                            ? 'Connect to continue'
                            : 'Connection interrupted'
                      }
                      action={
                        state.error.recovery === 'retry' ? (
                          <Button
                            onClick={() => {
                              void controller.reconnect();
                            }}
                          >
                            Reconnect
                          </Button>
                        ) : (
                          <a className="button" href="/">
                            Open current application
                          </a>
                        )
                      }
                    >
                      {state.error.message}
                    </ErrorState>
                  ) : null}
                  <Conversation
                    onPanel={showPanel}
                    focusConversationId={creation.focusConversationId}
                    onComposerFocused={creation.onComposerFocused}
                  />
                  {import.meta.env.VITE_ENABLE_FIXTURES === '1' &&
                    state.suggestions
                      .filter(
                        (item) =>
                          item.conversation_id ===
                            state.selectedConversationId &&
                          item.descriptor.panel_kind.startsWith('fake.'),
                      )
                      .map((suggestion) => (
                        <aside
                          className="suggestion"
                          key={panelKey(suggestion.descriptor)}
                          aria-label="Suggested panel"
                        >
                          <p>Suggested: {suggestion.descriptor.title}</p>
                          <Button
                            onClick={() => {
                              showPanel(suggestion.descriptor);
                              controller.dismissSuggestion(suggestion);
                            }}
                          >
                            Open suggested panel
                          </Button>
                          <Button
                            variant="ghost"
                            onClick={() =>
                              controller.dismissSuggestion(suggestion)
                            }
                          >
                            Dismiss suggestion
                          </Button>
                        </aside>
                      ))}
                </section>
                {homeOpen && (
                  <Home
                    onNewChat={() => void creation.newChat()}
                    creatingChat={creation.creatingChat}
                    onSetup={setup}
                    onOpenConversation={(id) => {
                      void controller.selectConversation(id);
                      navigate(`/conversations/${id}`);
                    }}
                    onSearch={() =>
                      overlay.open({
                        title: 'Search conversations',
                        description: 'Search your conversation history.',
                        content: <SearchConversations />,
                      })
                    }
                  />
                )}
                {compact && !routeOpen && (
                  <section className="compact-tab" aria-label="Compact panel">
                    <Button
                      onClick={() => {
                        openPanelRef.current?.focus({ preventScroll: true });
                        update((previous) => focusPanel(previous, null));
                      }}
                    >
                      <ChevronLeft size={18} aria-hidden />
                      Back to conversation
                    </Button>
                    <PanelContent panel={compact} visible />
                    <Button
                      onClick={() => {
                        openPanelRef.current?.focus({ preventScroll: true });
                        update((previous) =>
                          closePanel(previous, compact.instance_id),
                        );
                        update((previous) => focusPanel(previous, null));
                      }}
                    >
                      Close panel
                    </Button>
                  </section>
                )}
                {routeOpen && (
                  <div className="routed-view">
                    <Link className="button ghost" to="/">
                      <ChevronLeft size={18} aria-hidden />
                      Home
                    </Link>
                    <Suspense fallback={<Skeleton label="Opening view" />}>
                      <Outlet />
                    </Suspense>
                  </div>
                )}
              </main>
            </Panel>
            {bottomVisible && (
              <Separator
                className="resize-handle horizontal"
                aria-label="Resize bottom panel"
                onKeyDownCapture={(event) => keyboardResize(event, 'bottom')}
              />
            )}
            <Panel
              id="bottom-pane"
              panelRef={bottomRef}
              minSize={desktop ? 160 : 0}
              maxSize={desktop ? regionBounds(layout, 'bottom').max : 0}
              defaultSize={0}
              collapsible
              collapsedSize={0}
            >
              {bottomVisible && dock(bottomPanels, 'bottom')}
            </Panel>
          </Group>
        </Panel>
        {sideVisible && (
          <Separator
            className="resize-handle"
            aria-label="Resize side panel"
            onKeyDownCapture={(event) => keyboardResize(event, 'side')}
          />
        )}
        <Panel
          id="side-pane"
          panelRef={sideRef}
          minSize={desktop ? 320 : 0}
          maxSize={desktop ? regionBounds(layout, 'side').max : 0}
          defaultSize={0}
          collapsible
          collapsedSize={0}
        >
          {sideVisible && dock(sidePanels, 'side')}
        </Panel>
      </Group>
      {conversationId && layout.panels.length > 0 && (
        <aside className="panel-rail" aria-label="Panel rail">
          {layout.panels.map((panel) => (
            <Button
              key={panel.instance_id}
              variant="ghost"
              onClick={() => {
                if (!desktop) {
                  if (panelPresentation(layout, panel) === 'sheet')
                    overlay.open({
                      kind: 'sheet',
                      key: 'workspace-panel',
                      title: panel.descriptor.title,
                      description:
                        panelRegistry[
                          panel.descriptor
                            .panel_kind as keyof typeof panelRegistry
                        ]?.title ?? 'Resource panel',
                      content: <PanelContent panel={panel} visible />,
                    });
                  else
                    update((previous) =>
                      focusPanel(previous, panel.instance_id),
                    );
                } else
                  update((previous) =>
                    openPanel(previous, panel.descriptor, panel.placement),
                  );
              }}
            >
              {panel.descriptor.title}
            </Button>
          ))}
          <Button
            iconOnly
            aria-label="Close all panels"
            onClick={() => {
              openPanelRef.current?.focus({ preventScroll: true });
              update(closeAllPanels);
            }}
          >
            <X size={18} aria-hidden />
          </Button>
        </aside>
      )}
    </div>
  );
}
