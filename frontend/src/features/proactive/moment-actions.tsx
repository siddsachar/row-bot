import { useState, type ReactNode } from 'react';
import { Check, Clock, Info, Settings, ThumbsDown } from 'lucide-react';
import type {
  ProactiveAction,
  ProactiveMomentDetail,
  ProactiveMomentView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import type { MenuAction } from '../../ui/primitives';
import { useNotify } from '../../ui/overlays';
import type { ProactiveStore } from './proactive-state';
import { DraftsDrawer } from './Drafts';
import { WhyThisSheet } from './WhyThis';

/**
 * Where an action takes the person. In the app these are routes; in the
 * desktop pet they bring the main window forward.
 */
export type MomentGo = {
  conversation(id: string): void;
  /** An in-app path such as `/?tab=monitor`. */
  path(to: string): void;
  /** Open a prepared draft or brief (`prepared_ref`). */
  draft(draftId: string): void;
  /** Explain the moment ("Why this?"). */
  why(moment: ProactiveMomentView): void;
};

/** Where `open_source` goes: the first piece of evidence with a place in the app. */
export function sourcePath(detail: ProactiveMomentDetail): string | null {
  for (const item of detail.evidence) {
    if (item.type === 'conversation')
      return `/conversations/${encodeURIComponent(item.id)}`;
    if (item.type === 'workflow_run') return '/?tab=workflows';
    if (item.type === 'memory') return '/?tab=knowledge';
    if (item.type === 'check') return '/?tab=monitor';
  }
  return null;
}

export const FOR_YOU_PATH = '/?tab=overview#for-you';

/**
 * Do one of a moment's actions. The client performs the navigation, then
 * reports `act` with that action's id; only `continue_in_chat` does
 * something on the server, which names the chat to open.
 */
export async function performMoment(
  store: ProactiveStore,
  moment: ProactiveMomentView,
  action: ProactiveAction,
  go: MomentGo,
): Promise<void> {
  const act = () =>
    store.feedback(moment.moment_id, { action: 'act', action_id: action.id });
  switch (action.id) {
    case 'continue_in_chat': {
      const detail = await act();
      if (detail.conversation_id) go.conversation(detail.conversation_id);
      return;
    }
    case 'open_draft':
    case 'open_brief':
      if (!moment.prepared_ref) break;
      go.draft(moment.prepared_ref);
      await act();
      return;
    case 'open_fix':
      go.path('/?tab=monitor');
      await act();
      return;
    case 'open_for_you':
      go.path(FOR_YOU_PATH);
      await act();
      return;
    case 'open_source': {
      const where = sourcePath(await store.io.moment(moment.moment_id));
      if (!where) break;
      go.path(where);
      await act();
      return;
    }
  }
  // Nowhere to go: explain it instead.
  go.why(moment);
}

/** The words for an action that couldn't be done. */
export function actionProblem(cause: unknown): string {
  const problem = clientError(cause);
  if (problem.code === 'generation_active')
    return 'That chat is busy. Try again when it has finished.';
  if (problem.code === 'revision_conflict')
    return 'That one was already handled.';
  return problem.message;
}

export type LessScope = 'topic' | 'kind' | 'time';

/**
 * "Less like this" in one step: the server settles the moment and writes the
 * rule; the notice says the rule in its own words, with Undo (which removes
 * the rule again).
 */
export function useLessLikeThis(store: ProactiveStore | null) {
  const notify = useNotify();
  return async (
    moment: ProactiveMomentView,
    scope: LessScope,
  ): Promise<void> => {
    if (!store) return;
    const before = new Set(
      (store.getView().settings?.rules ?? []).map((rule) => rule.rule_id),
    );
    await store.feedback(moment.moment_id, {
      action: 'less_like_this',
      scope,
    });
    const settings = await store
      .settingsChanged(() => store.io.settings())
      .catch(() => null);
    const rule = settings?.rules.find((item) => !before.has(item.rule_id));
    if (!rule) {
      notify('Rule saved');
      return;
    }
    notify(`Rule saved: ${rule.words}`, undefined, {
      label: 'Undo',
      onAction: () =>
        void store
          .settingsChanged(() => store.io.deleteRule(rule.rule_id))
          .catch(() => notify('Couldn’t undo. Try again.', 'danger')),
    });
  };
}

/** The "…" of a moment: Not now, Less like this, Why this? (then Done or Buddy settings). */
export function momentMenu(handlers: {
  notNow(): void;
  less(): void;
  why(): void;
  dismiss?: () => void;
  settings?: () => void;
}): MenuAction[] {
  const actions: MenuAction[] = [
    {
      label: 'Not now',
      description: 'Keep it in For you. Buddy won’t bring it up again.',
      icon: <Clock size={16} aria-hidden />,
      onSelect: handlers.notNow,
    },
    {
      label: 'Less like this',
      description: 'Choose what to hear less about.',
      icon: <ThumbsDown size={16} aria-hidden />,
      onSelect: handlers.less,
    },
    {
      label: 'Why this?',
      description: 'What Row-Bot saw and why now.',
      icon: <Info size={16} aria-hidden />,
      onSelect: handlers.why,
    },
  ];
  if (handlers.dismiss)
    actions.push({
      label: 'Done',
      description: 'Take it off For you.',
      icon: <Check size={16} aria-hidden />,
      onSelect: handlers.dismiss,
      separatorBefore: true,
    });
  if (handlers.settings)
    actions.push({
      label: 'Buddy settings',
      icon: <Settings size={16} aria-hidden />,
      onSelect: handlers.settings,
      separatorBefore: true,
    });
  return actions;
}

type Sheet =
  | { kind: 'why'; moment: ProactiveMomentView; less: boolean }
  | { kind: 'drafts'; draftId: string | null }
  | null;

/**
 * A surface's moment actions with their sheets ("Why this?" and Drafts).
 * `navigate` goes to an in-app path; `newChat` starts a chat with text
 * waiting in its composer (where one can be started).
 */
export function useMomentActions(
  store: ProactiveStore | null,
  {
    navigate,
    newChat,
    draftsInPlace = true,
  }: {
    navigate(to: string): void;
    newChat?: (text: string) => void;
    /** False where drafts open on Home instead (the sidebar). */
    draftsInPlace?: boolean;
  },
): {
  perform(moment: ProactiveMomentView, action: ProactiveAction): void;
  feedback(moment: ProactiveMomentView, kind: 'not_now' | 'dismiss'): void;
  menu(moment: ProactiveMomentView, withDone?: boolean): MenuAction[];
  why(moment: ProactiveMomentView, less?: boolean): void;
  less(moment: ProactiveMomentView, scope: LessScope): Promise<void>;
  drafts(draftId?: string | null): void;
  sheets: ReactNode;
} {
  const notify = useNotify();
  const less = useLessLikeThis(store);
  const [sheet, setSheet] = useState<Sheet>(null);
  const openDraft = (draftId: string | null) => {
    if (draftsInPlace) setSheet({ kind: 'drafts', draftId });
    else
      navigate(`/?tab=overview&draft=${encodeURIComponent(draftId ?? 'all')}`);
  };
  const go: MomentGo = {
    conversation: (id) => navigate(`/conversations/${encodeURIComponent(id)}`),
    path: navigate,
    draft: openDraft,
    why: (moment) => setSheet({ kind: 'why', moment, less: false }),
  };
  const feedback = (
    moment: ProactiveMomentView,
    kind: 'not_now' | 'dismiss',
  ) => {
    store
      ?.feedback(moment.moment_id, { action: kind })
      .catch((cause) => notify(actionProblem(cause), 'warning'));
  };
  return {
    perform: (moment, action) => {
      if (!store) return;
      performMoment(store, moment, action, go).catch((cause) =>
        notify(actionProblem(cause), 'warning'),
      );
    },
    feedback,
    menu: (moment, withDone = true) =>
      momentMenu({
        notNow: () => feedback(moment, 'not_now'),
        less: () => setSheet({ kind: 'why', moment, less: true }),
        why: () => setSheet({ kind: 'why', moment, less: false }),
        dismiss: withDone ? () => feedback(moment, 'dismiss') : undefined,
      }),
    why: (moment, startWithLess = false) =>
      setSheet({ kind: 'why', moment, less: startWithLess }),
    less: (moment, scope) =>
      less(moment, scope).catch((cause) =>
        notify(actionProblem(cause), 'warning'),
      ),
    drafts: (draftId = null) => openDraft(draftId),
    sheets: store && (
      <>
        <WhyThisSheet
          store={store}
          moment={sheet?.kind === 'why' ? sheet.moment : null}
          startWithLess={sheet?.kind === 'why' && sheet.less}
          onClose={() => setSheet(null)}
          onLess={(moment, scope) => less(moment, scope)}
          onOpenDraft={(draftId) => setSheet({ kind: 'drafts', draftId })}
          onOpenSettings={() => navigate('/settings/proactive#level')}
        />
        <DraftsDrawer
          store={store}
          open={sheet?.kind === 'drafts'}
          draftId={sheet?.kind === 'drafts' ? sheet.draftId : null}
          onOpenDraft={(draftId) => setSheet({ kind: 'drafts', draftId })}
          onClose={() => setSheet(null)}
          onSendFromChat={newChat}
        />
      </>
    ),
  };
}
