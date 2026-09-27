import {
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
  Activity,
  ArrowDownToLine,
  ArrowRightToLine,
  Bot,
  BookOpen,
  Brain,
  ChevronLeft,
  Code2,
  Columns3,
  Copy,
  Globe,
  Home as HomeIcon,
  Library,
  Lightbulb,
  MessageSquare,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  PanelLeft,
  PanelRight,
  Palette,
  PanelRightClose,
  PencilLine,
  RotateCcw,
  Search,
  Settings as SettingsIcon,
  SquareTerminal,
  Workflow,
  X,
  type LucideIcon,
} from 'lucide-react';
import {
  Button,
  EmptyState,
  ErrorState,
  IconButton,
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
import { bindVisualViewportState, useWorkspaceLayout } from './layout';
import CommandPalette, { type PaletteCommand } from './CommandPalette';
import Navigation, { NavigationRail } from './Navigation';
import { ContextHostContext, useContextHostOwner } from './context-host';
import Home from './Home';
import useNewChat from './useNewChat';
import { reconcilePanelPresentation } from '../panels/presentation';
import Conversation from './Conversation';
import { canAutoOpenDesign } from './design-auto-open';
import type {
  ConversationView,
  PanelDescriptor,
  ResourceView,
  SearchHit,
} from '../../api/types';
import ResourcePanel from '../panels/ResourcePanel';
import BrowserLiveControls from '../browser/BrowserLiveControls';
import NativeTerminal from '../panels/NativeTerminal';
import { WorkspaceActionsContext } from './workspace-actions';
import { openAgentProfiles } from './agent-profiles';
import type { ProfileSummary } from '../settings/GoalProfileSettings';

const subscriptions = new PanelSubscriptions();

/** A monochrome glyph per panel kind for the right region's tabs. */
function panelIcon(descriptor: PanelDescriptor): LucideIcon {
  return descriptor.panel_kind === 'artifact.preview'
    ? Palette
    : descriptor.panel_kind === 'workspace.inspector'
      ? Code2
      : descriptor.panel_kind === 'native.terminal'
        ? SquareTerminal
        : descriptor.panel_kind === 'browser.live'
          ? Globe
          : BookOpen;
}

/** Keyboard shortcut match: Mod is Command on macOS and Control elsewhere. */
function shortcut(
  event: globalThis.KeyboardEvent,
  key: string,
  shift = false,
): boolean {
  return (
    !event.isComposing &&
    !event.altKey &&
    event.shiftKey === shift &&
    !event.repeat &&
    (event.ctrlKey || event.metaKey) &&
    event.key.toLowerCase() === key
  );
}
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

function BrowserPanel({ visible }: { visible: boolean }) {
  const conversationId = useClientSelector(
    (value) => value.selectedConversationId,
  );
  const { controller, browserControlOwner } = useRuntime();
  const session = conversationId
    ? browserControlOwner?.get()?.get(conversationId)
    : undefined;
  if (!visible) return null;
  if (!conversationId || !session)
    return (
      <EmptyState title="Managed browser unavailable">
        Open an authenticated conversation after pending browser actions are
        resolved.
      </EmptyState>
    );
  return (
    <BrowserLiveControls
      session={session}
      load={controller.browserControls}
      loadPreview={controller.browserPreview}
      review={(action, payload, signal) =>
        controller.reviewBrowserControl(conversationId, action, payload, signal)
      }
      execute={(command, review) =>
        controller.executeBrowserControl(conversationId, command, review)
      }
    />
  );
}

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
  ) : panel.descriptor.panel_kind === 'browser.live' ? (
    <BrowserPanel visible={visible} />
  ) : panel.descriptor.panel_kind === 'native.terminal' ? (
    <NativeTerminal visible={visible} />
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
    generation: useClientSelector((value) => value.projection?.generation),
    rows: useClientSelector(
      (value) => (value.history ?? value.projection)?.rows,
    ),
  };
  const { controller, goalProfileOwner } = useRuntime();
  const overlay = useOverlay();
  const paletteSequence = useRef(0);
  const agentProfiles = useRef(new Map<string, ProfileSummary>());
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
  const settingsOpen = location.pathname.startsWith('/settings');
  const [layout, setLayout] = useWorkspaceLayout(
    state.handshake?.instance_id,
    conversationId ?? 'home',
  );
  const creation = useNewChat();
  const { host: contextHost, parking: contextParking } = useContextHostOwner();
  // Mod+. toggles the conversation's Context card; each press bumps this.
  const [contextToggle, setContextToggle] = useState(0);
  const pendingPanel = useRef<{
    descriptor: (typeof samplePanels)[number];
    selectionVersion: number;
    originRouteKey: string;
    instance: string | undefined;
  } | null>(null);
  const observedGeneration = useRef<{
    id: string;
    designRevision: string;
  } | null>(null);
  const designBaseline = useRef<{
    conversation: string;
    revision: string;
  } | null>(null);
  if (
    conversationId &&
    state.workspace?.conversation_id === conversationId &&
    designBaseline.current?.conversation !== conversationId
  ) {
    designBaseline.current = {
      conversation: conversationId,
      revision:
        state.workspace.resources.find(
          (item) => item.binding.kind === 'artifact',
        )?.resource_revision ?? '',
    };
  }
  const handledGeneration = useRef('');
  const pendingDesignGeneration = useRef('');
  const [completedDesign, setCompletedDesign] = useState<{
    conversation: string;
    binding: string;
  } | null>(null);
  const [maximizedPanelId, setMaximizedPanelId] = useState<string | null>(null);
  const autoOpenDesign = useEffectEvent(
    (
      panel: (typeof samplePanels)[number],
      resources: readonly ResourceView[],
    ) => showPanel(panel, undefined, resources),
  );
  const presentationScope = useRef({
    conversationId,
    routeKey: location.key,
    setLayout,
  });
  useEffect(() => {
    if (!maximizedPanelId) return;
    const restore = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setMaximizedPanelId(null);
    };
    document.addEventListener('keydown', restore);
    return () => document.removeEventListener('keydown', restore);
  }, [maximizedPanelId]);
  useEffect(() => {
    if (
      maximizedPanelId &&
      !layout.panels.some(
        (panel) =>
          panel.instance_id === maximizedPanelId &&
          (!panel.descriptor.resource_ref ||
            panel.descriptor.resource_ref.startsWith(`${conversationId}:`)),
      )
    )
      setMaximizedPanelId(null);
  }, [conversationId, layout.panels, maximizedPanelId]);
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
      autoOpenResources: false,
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
  useEffect(() => {
    const generation = state.generation;
    const design = state.workspace?.resources.find(
      (item) => item.binding.kind === 'artifact',
    );
    if (
      !conversationId ||
      state.selectedConversationId !== conversationId ||
      !generation
    )
      return;
    if (
      generation.status === 'running' &&
      designBaseline.current?.conversation !== conversationId
    ) {
      observedGeneration.current = {
        id: generation.generation_id,
        designRevision: design?.resource_revision ?? '',
      };
      return;
    }
    if (
      generation.status !== 'completed' ||
      handledGeneration.current === generation.generation_id ||
      pendingDesignGeneration.current === generation.generation_id ||
      (observedGeneration.current?.id !== generation.generation_id &&
        designBaseline.current?.conversation !== conversationId)
    )
      return;
    pendingDesignGeneration.current = generation.generation_id;
    const baseline =
      designBaseline.current?.conversation === conversationId
        ? designBaseline.current.revision
        : (observedGeneration.current?.designRevision ?? '');
    void controller
      .workspaceFor(conversationId)
      .then((fresh) => {
        if (
          fresh.conversation_id !== conversationId ||
          controller.getSnapshot().selectedConversationId !== conversationId
        )
          return;
        const currentDesign = fresh.resources.find(
          (item) => item.binding.kind === 'artifact',
        );
        designBaseline.current = {
          conversation: conversationId,
          revision: currentDesign?.resource_revision ?? '',
        };
        const latestRequest =
          [
            ...((
              controller.getSnapshot().history ??
              controller.getSnapshot().projection
            )?.rows ?? []),
          ]
            .reverse()
            .find((row) => row.role === 'user')
            ?.blocks.map((block) => ('text' in block ? block.text : ''))
            .join('') ?? '';
        const explicitDesignRequest =
          /\b(design|deck|presentation|slides?|storyboard|social post|mock[ -]?up)\b/i.test(
            latestRequest,
          );
        const updated = fresh.resources.find(
          (item) =>
            item.binding.kind === 'artifact' &&
            item.available &&
            (item.resource_revision !== baseline || explicitDesignRequest),
        );
        if (!updated) return;
        handledGeneration.current = generation.generation_id;
        if (
          canAutoOpenDesign(
            conversationId,
            location.pathname,
            document.visibilityState,
            document.activeElement,
            controller.getDraft(conversationId).text,
          )
        ) {
          autoOpenDesign(
            {
              panel_kind: 'artifact.preview',
              title: updated.title,
              resource_ref: updated.resource_ref,
              resource_kind: 'artifact',
              resource_revision: updated.resource_revision,
            },
            fresh.resources,
          );
        } else {
          setCompletedDesign({
            conversation: conversationId,
            binding: updated.binding.binding_id,
          });
        }
      })
      .catch(() => {
        // The bound design remains accessible in Context if a refresh fails.
      })
      .finally(() => {
        if (pendingDesignGeneration.current === generation.generation_id)
          pendingDesignGeneration.current = '';
      });
  }, [
    controller,
    conversationId,
    location.pathname,
    state.generation,
    state.rows,
    state.selectedConversationId,
    state.workspace,
  ]);
  const closeCompactSheet = useEffectEvent(() =>
    overlay.dismiss('workspace-panel'),
  );
  useEffect(() => {
    if (desktop) closeCompactSheet();
  }, [desktop]);
  function openSearchHit(hit: SearchHit) {
    const ticket = ++paletteSequence.current;
    void controller
      .selectConversation(hit.conversation_id)
      .then(async () => {
        if (
          ticket !== paletteSequence.current ||
          controller.getSnapshot().selectedConversationId !==
            hit.conversation_id ||
          controller.getSnapshot().conversation?.id !== hit.conversation_id
        )
          return;
        const selection = controller.getSelectionVersion();
        if (hit.message_id) await controller.showHistory(hit.message_id);
        if (
          ticket !== paletteSequence.current ||
          controller.getSelectionVersion() !== selection
        )
          return;
        navigate(`/conversations/${hit.conversation_id}`, { replace: true });
        const target = Array.from(
          document.querySelectorAll<HTMLElement>('[data-message-id]'),
        ).find((element) => element.dataset.messageId === hit.message_id);
        overlay.close(target);
      })
      .catch(() =>
        overlay.notify('That result is no longer available. Search again.'),
      );
  }
  function openCommands() {
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const go = (to: string) => {
      navigate(to, { replace: true });
      overlay.close();
    };
    const session = goalProfileOwner?.get();
    const resourcePanels: PanelDescriptor[] = [
      ...(state.workspace?.conversation_id === conversationId
        ? (state.workspace?.resources ?? [])
        : []
      ).map((resource) => ({
        panel_kind:
          resource.binding.kind === 'artifact'
            ? 'artifact.preview'
            : 'workspace.inspector',
        title: resource.title.slice(0, 160),
        resource_ref: resource.resource_ref,
        resource_kind: resource.binding.kind,
        resource_revision: resource.resource_revision,
      })),
      ...(conversationId &&
      state.handshake?.application_capabilities?.includes('native:terminal')
        ? [{ panel_kind: 'native.terminal', title: 'Interactive terminal' }]
        : []),
    ];
    const commands: PaletteCommand[] = [
      {
        id: 'new-chat',
        label: 'New chat',
        keywords: 'new conversation start compose',
        icon: <PencilLine size={16} />,
        shortcut: 'Mod+Shift+O',
        run: () => {
          overlay.close();
          void creation.newChat();
        },
      },
      {
        id: 'home',
        label: 'Home',
        keywords: 'start overview',
        icon: <HomeIcon size={16} />,
        run: () => go('/'),
      },
      {
        id: 'library',
        label: 'Conversation library',
        keywords: 'browse conversations history search manage delete',
        icon: <Library size={16} />,
        run: () => go('/library'),
      },
      {
        id: 'workflows',
        label: 'Workflows',
        keywords: 'tasks reminders schedules automations',
        icon: <Workflow size={16} />,
        run: () => go('/?tab=workflows'),
      },
      {
        id: 'knowledge',
        label: 'Knowledge graph',
        keywords: 'memory memories wiki',
        icon: <Brain size={16} />,
        run: () => go('/?tab=knowledge'),
      },
      {
        id: 'monitor',
        label: 'Monitor',
        keywords: 'health logs status diagnosis',
        icon: <Activity size={16} />,
        run: () => go('/?tab=monitor'),
      },
      {
        id: 'insights',
        label: 'Insights',
        keywords: 'suggestions findings',
        icon: <Lightbulb size={16} />,
        run: () => go('/?tab=insights'),
      },
      {
        id: 'settings',
        label: 'Settings',
        keywords: 'configuration providers models',
        icon: <SettingsIcon size={16} />,
        run: () => go('/settings/providers'),
      },
      ...(session
        ? [
            {
              id: 'agents',
              label: 'Agent profiles',
              keywords: 'agents profiles library',
              icon: <Bot size={16} />,
              run: () =>
                openAgentProfiles({
                  overlay,
                  controller,
                  session,
                  returnFocusTo: opener,
                  onStartProfileChat: (profile) =>
                    void creation.newChat('', profile),
                }),
            },
          ]
        : []),
      ...(desktop
        ? [
            {
              id: 'toggle-navigation',
              label: 'Toggle sidebar',
              keywords: 'navigation collapse expand',
              icon: <PanelLeft size={16} />,
              run: () => {
                overlay.close();
                update((previous) => toggleRegion(previous, 'navigation'));
              },
            },
          ]
        : []),
      ...(desktop && conversationId
        ? [
            {
              id: 'toggle-context',
              label: 'Toggle Context',
              keywords: 'context panel inspector resources agents',
              icon: <PanelRight size={16} />,
              shortcut: 'Mod+.',
              run: () => {
                overlay.close();
                setContextToggle((count) => count + 1);
              },
            },
          ]
        : []),
      ...[
        ...resourcePanels,
        ...(import.meta.env.VITE_ENABLE_FIXTURES === '1' ? samplePanels : []),
      ].map((panel) => {
        const Icon = panelIcon(panel);
        return {
          id: `panel:${panelKey(panel)}`,
          label: `Open ${panel.title}`,
          keywords: 'panel',
          icon: <Icon size={16} />,
          run: () => {
            overlay.close();
            showPanel(panel, opener);
          },
        };
      }),
      {
        id: 'reset-layout',
        label: 'Reset layout',
        keywords: 'panels sizes',
        icon: <RotateCcw size={16} />,
        run: () => {
          overlay.close();
          update(resetLayout);
        },
      },
    ];
    overlay.open({
      kind: 'palette',
      className: 'command-palette-dialog',
      title: 'Workspace commands',
      description:
        'Search conversations, commands, settings and agents. Press Escape to return to your workspace.',
      content: (
        <CommandPalette
          commands={commands}
          loadAgents={
            session
              ? async (signal) => {
                  const page = await controller.profiles(
                    '',
                    undefined,
                    undefined,
                    signal,
                  );
                  agentProfiles.current = new Map(
                    page.items.map((profile) => [profile.id, profile]),
                  );
                  return page.items
                    .filter((profile) => profile.enabled)
                    .map((profile) => ({
                      id: profile.id,
                      label: profile.display_name,
                      description: profile.description,
                    }));
                }
              : undefined
          }
          onStartAgent={(agent) => {
            const profile = agentProfiles.current.get(agent.id);
            overlay.close();
            if (profile) void creation.newChat('', profile);
          }}
          onOpenConversation={(row: ConversationView) => {
            void controller.selectConversation(row.id);
            go(`/conversations/${row.id}`);
          }}
          onOpenSearchHit={openSearchHit}
          onOpenSetting={go}
        />
      ),
    });
  }
  const shellShortcut = useEffectEvent((event: globalThis.KeyboardEvent) => {
    if (shortcut(event, 'k')) {
      event.preventDefault();
      openCommands();
    } else if (shortcut(event, 'o', true) || shortcut(event, 'n')) {
      if (state.status !== 'ready' || creation.creatingChat) return;
      event.preventDefault();
      overlay.close();
      void creation.newChat();
    } else if (shortcut(event, '.')) {
      if (!conversationId || !desktop || homeOpen || routeOpen) return;
      event.preventDefault();
      setContextToggle((count) => count + 1);
    }
  });
  useEffect(() => {
    const keydown = (event: globalThis.KeyboardEvent) => shellShortcut(event);
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
  useEffect(() => bindVisualViewportState(document.documentElement), []);
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
    resourceSnapshot?: readonly ResourceView[],
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
          resources: resourceSnapshot ?? snapshot.workspace?.resources ?? [],
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
  // Exactly one "Close all panels" is on screen: in the side region, else
  // the bottom region, else the rail that lists hidden panels.
  const closeAllHost = sideVisible ? 'side' : bottomVisible ? 'bottom' : 'rail';
  const closeAll = (
    <IconButton
      size="sm"
      label="Close all panels"
      onClick={() => {
        openPanelRef.current?.focus({ preventScroll: true });
        update(closeAllPanels);
      }}
    >
      <X size={16} aria-hidden />
    </IconButton>
  );
  function dock(panels: PanelInstance[], placement: PanelPlacement) {
    const side = placement === 'side';
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
          className={`dock ${side ? 'right-region' : 'bottom-region'} ${active && maximizedPanelId === active.instance_id ? 'maximized' : ''}`}
          aria-label={`${side ? 'Side' : 'Bottom'} panels`}
        >
          <header className="dock-header">
            <DockTabs.List
              className="dock-tabs"
              aria-label={`${placement} panel tabs`}
            >
              {panels.map((panel) => {
                const Icon = panelIcon(panel.descriptor);
                return (
                  <DockTabs.Trigger
                    asChild
                    value={panel.instance_id}
                    key={panel.instance_id}
                  >
                    <Button
                      variant="ghost"
                      className="dock-tab"
                      title={panel.descriptor.title}
                    >
                      <Icon size={14} aria-hidden />
                      <span>{panel.descriptor.title}</span>
                    </Button>
                  </DockTabs.Trigger>
                );
              })}
            </DockTabs.List>
            <div className="dock-actions">
              {active && (
                <IconButton
                  size="sm"
                  label={
                    maximizedPanelId === active.instance_id
                      ? 'Exit focus mode'
                      : 'Focus mode'
                  }
                  pressed={maximizedPanelId === active.instance_id}
                  onClick={() =>
                    setMaximizedPanelId((current) =>
                      current === active.instance_id
                        ? null
                        : active.instance_id,
                    )
                  }
                >
                  {maximizedPanelId === active.instance_id ? (
                    <Minimize2 size={16} aria-hidden />
                  ) : (
                    <Maximize2 size={16} aria-hidden />
                  )}
                </IconButton>
              )}
              {active && (
                <Menu
                  label="Panel actions"
                  iconOnly
                  variant="ghost"
                  className="dock-menu"
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
                      label: `Move to ${side ? 'bottom' : 'side'}`,
                      icon: side ? (
                        <ArrowDownToLine size={16} />
                      ) : (
                        <ArrowRightToLine size={16} />
                      ),
                      onSelect: () => {
                        panelFocusPending.current = true;
                        update((previous) =>
                          movePanel(
                            previous,
                            active.instance_id,
                            side ? 'bottom' : 'side',
                          ),
                        );
                      },
                    },
                    {
                      label: 'Open another copy',
                      icon: <Copy size={16} />,
                      onSelect: () =>
                        update((previous) =>
                          openPanel(
                            previous,
                            active.descriptor,
                            placement,
                            true,
                          ),
                        ),
                    },
                    {
                      label: 'Make panel smaller',
                      separatorBefore: true,
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
                      icon: <PanelRightClose size={16} />,
                      shortcut: side ? 'Mod+.' : undefined,
                      onSelect: () => {
                        panelFocusPending.current = true;
                        update((previous) => toggleRegion(previous, placement));
                      },
                    },
                    {
                      label: 'Close panel',
                      icon: <X size={16} />,
                      separatorBefore: true,
                      onSelect: () => {
                        panelFocusPending.current = true;
                        update((previous) =>
                          closePanel(previous, active.instance_id),
                        );
                      },
                    },
                  ]}
                >
                  <MoreHorizontal size={16} aria-hidden />
                </Menu>
              )}
              {closeAllHost === placement && closeAll}
            </div>
          </header>
          {panels.map(pane)}
        </section>
      </DockTabs.Root>
    );
  }
  const commandsButton = (
    <IconButton
      size="sm"
      label="Workspace commands"
      shortcut="Mod+K"
      onClick={openCommands}
    >
      <Search size={16} aria-hidden />
    </IconButton>
  );
  const navigationToggle = (
    <IconButton
      size="sm"
      label={
        desktop && layout.navigation.collapsed
          ? 'Expand navigation'
          : 'Toggle navigation'
      }
      onClick={() =>
        desktop
          ? update((previous) => toggleRegion(previous, 'navigation'))
          : overlay.open({
              kind: 'drawer',
              title: 'Conversations',
              description: 'Choose a conversation',
              content: navigation(false),
            })
      }
    >
      <PanelLeft size={16} aria-hidden />
    </IconButton>
  );
  const openPanelMenu = (
    <Menu
      label="Open panel"
      triggerRef={openPanelRef}
      iconOnly
      variant="ghost"
      className="open-panel-menu"
      hint="Open panel"
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
        ...(state.handshake?.application_capabilities?.includes(
          'native:terminal',
        )
          ? [{ panel_kind: 'native.terminal', title: 'Interactive terminal' }]
          : []),
        ...(import.meta.env.VITE_ENABLE_FIXTURES === '1' ? samplePanels : []),
      ].map((panel) => {
        const Icon = panelIcon(panel);
        return {
          label: panel.title,
          icon: <Icon size={16} />,
          onSelect: (opener: HTMLButtonElement | null) =>
            showPanel(panel, opener),
        };
      })}
    >
      <Columns3 size={16} aria-hidden />
    </Menu>
  );
  const navigation = (inPane: boolean) => (
    <Navigation
      showBuddy={!layout.navigation.collapsed}
      headerActions={
        inPane ? (
          <>
            {commandsButton}
            {navigationToggle}
          </>
        ) : undefined
      }
      onNewChat={() => void creation.newChat()}
      onStartProfileChat={(profile) => void creation.newChat('', profile)}
      creatingChat={creation.creatingChat}
      onOpenConversation={() =>
        update((previous) =>
          previous.widthClass !== 'desktop' && previous.activePanelId !== null
            ? focusPanel(previous, null)
            : previous,
        )
      }
    />
  );
  // Panels whose region is hidden stay one click away on the rail.
  const railPanels = desktop
    ? layout.panels.filter(
        (panel) =>
          (panel.placement === 'side' && !sideVisible) ||
          (panel.placement === 'bottom' && !bottomVisible),
      )
    : layout.panels;
  return (
    <ContextHostContext.Provider value={contextHost}>
      <div
        className={`workspace ${layout.navigation.collapsed ? 'navigation-collapsed' : ''} ${layout.panels.length > 0 ? 'has-resource-panels' : ''}`}
        ref={workspaceRef}
      >
        <a className="skip-link" href="#conversation">
          Skip to conversation
        </a>
        <div className="context-parking" ref={contextParking} hidden />
        {!desktop && (
          <div className="compact-controls">
            <div
              className="workspace-controls"
              role="group"
              aria-label="Workspace controls"
            >
              {commandsButton}
              {navigationToggle}
              {openPanelMenu}
            </div>
          </div>
        )}
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
                Check pending receipt
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
                <NavigationRail
                  railActions={
                    <>
                      {navigationToggle}
                      {commandsButton}
                    </>
                  }
                  onNewChat={() => void creation.newChat()}
                  onStartProfileChat={(profile) =>
                    void creation.newChat('', profile)
                  }
                  creatingChat={creation.creatingChat}
                />
              ) : (
                navigation(true)
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
                      {/* Announced, not drawn: the sidebar footer shows a
                          status dot and a disconnection shows the banner
                          below (B7). */}
                      <span
                        className={`connection-status visually-hidden ${state.status === 'ready' ? 'connected' : ''}`}
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
                        {state.status === 'reconnecting'
                          ? ' Row-Bot is trying to reconnect. Sending and live updates are unavailable in the meantime.'
                          : state.status === 'disconnected'
                            ? ' Sending and live updates are unavailable until you reconnect.'
                            : ''}
                      </ErrorState>
                    ) : null}
                    <Conversation
                      onPanel={showPanel}
                      completedDesignId={
                        completedDesign?.conversation === conversationId
                          ? completedDesign.binding
                          : undefined
                      }
                      onResourceOpened={(binding) => {
                        if (completedDesign?.binding === binding)
                          setCompletedDesign(null);
                      }}
                      onNewChat={() => void creation.newChat()}
                      onStartProfileChat={(profile) =>
                        void creation.newChat('', profile)
                      }
                      focusConversationId={creation.focusConversationId}
                      onComposerFocused={creation.onComposerFocused}
                      firstPrompt={creation.firstPrompt}
                      onFirstPromptConsumed={creation.onFirstPromptConsumed}
                      contextPlacement={desktop ? 'inline' : 'compact'}
                      contextToggle={contextToggle}
                      headerActions={desktop ? openPanelMenu : undefined}
                    />
                  </section>
                  {homeOpen && <Home />}
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
                    <div
                      className={`routed-view${settingsOpen ? ' settings-route' : ''}`}
                    >
                      {!settingsOpen && (
                        <Link className="button ghost routed-home" to="/">
                          <ChevronLeft size={18} aria-hidden />
                          Home
                        </Link>
                      )}
                      <Suspense fallback={<Skeleton label="Opening view" />}>
                        <WorkspaceActionsContext.Provider
                          value={{
                            resetLayout: () => update(resetLayout),
                            startProfileChat: (profile) =>
                              void creation.newChat('', profile),
                          }}
                        >
                          <Outlet />
                        </WorkspaceActionsContext.Provider>
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
        {conversationId && railPanels.length > 0 && (
          <aside
            className={`panel-rail ${desktop ? 'panel-rail-docked' : ''}`}
            aria-label="Panel rail"
          >
            {railPanels.map((panel) => {
              const Icon = panelIcon(panel.descriptor);
              return (
                <Button
                  key={panel.instance_id}
                  variant="ghost"
                  className="panel-rail-item"
                  title={panel.descriptor.title}
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
                  <Icon size={16} aria-hidden />
                  <span>{panel.descriptor.title}</span>
                </Button>
              );
            })}
            {(!desktop || closeAllHost === 'rail') && closeAll}
          </aside>
        )}
      </div>
    </ContextHostContext.Provider>
  );
}
