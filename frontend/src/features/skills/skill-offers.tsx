import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { BookmarkPlus, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { SkillOfferPage } from '../../api/types';
import { clientError } from '../../api/errors';
import { useLiveUpdate } from '../../live';
import { useRuntime } from '../../runtime';
import { useNotify } from '../../ui/overlays';
import { Button, IconButton } from '../../ui/primitives';
import SkillDraftSheet from './SkillDraftSheet';
import './skills.css';

export type SkillOffer = SkillOfferPage['items'][number];

/**
 * "Save as a skill" (C1) for the open chat: its offers by reply, and the
 * sheet that drafts, edits and approves one. Null outside a chat, and in a
 * chat that is or was incognito (nothing from it is kept).
 */
export type SkillOffers = {
  offer: (messageId: string) => SkillOffer | undefined;
  /** Open the sheet for this reply: its draft, or a new one. */
  save: (messageId: string) => void;
  /** A saved skill, in Settings › Skills. */
  openSkill: (skillId: string) => void;
  dismiss: (offer: SkillOffer) => void;
};

export const SkillOfferContext = createContext<SkillOffers | null>(null);

export function useSkillOffers(): SkillOffers | null {
  return useContext(SkillOfferContext);
}

/** The chat's offers, read on open, when a turn settles and on `skill_offers`. */
export function useChatSkillOffers(
  conversationId: string | null,
  {
    refreshKey,
    incognito,
    title,
  }: { refreshKey: string; incognito: boolean; title: string },
): { value: SkillOffers | null; sheet: ReactNode } {
  const { controller } = useRuntime();
  const notify = useNotify();
  const navigate = useNavigate();
  const [page, setPage] = useState<SkillOfferPage | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const read = useCallback(() => {
    if (!conversationId || incognito) {
      setPage(null);
      return;
    }
    controller.skillOffers(conversationId).then(
      (value) => {
        if (value?.conversation_id === conversationId) setPage(value);
      },
      () => {},
    );
  }, [controller, conversationId, incognito]);
  useEffect(() => {
    setOpen(null);
  }, [conversationId]);
  useEffect(() => read(), [read, refreshKey]);
  useLiveUpdate(['skill_offers'], read);
  const current = page?.conversation_id === conversationId ? page : null;
  const value = useMemo<SkillOffers | null>(() => {
    if (!conversationId || incognito) return null;
    const byMessage = new Map(
      (current?.items ?? []).map((item) => [item.message_id, item]),
    );
    return {
      offer: (messageId) => byMessage.get(messageId),
      save: (messageId) => setOpen(messageId),
      openSkill: (skillId) =>
        navigate(`/settings/skills/${encodeURIComponent(skillId)}`),
      dismiss: (offer) =>
        void controller.dismissSkillOffer(conversationId, offer.id).then(
          (next) => {
            setPage(next);
            notify('Row-Bot won’t offer that as a skill again for a while.');
          },
          (cause: unknown) => notify(clientError(cause).message, 'danger'),
        ),
    };
  }, [controller, conversationId, current, incognito, navigate, notify]);
  // Mounted while open: each opening starts from the reply's own draft.
  const sheet =
    conversationId && value && open !== null ? (
      <SkillDraftSheet
        open
        conversationId={conversationId}
        chatTitle={title}
        messageId={open}
        offer={value.offer(open)}
        onChanged={setPage}
        onOpenChange={(next) => {
          if (!next) setOpen(null);
        }}
      />
    ) : null;
  return { value, sheet };
}

/** Where a skill proposal came from, when it was drafted from a chat (C1). */
export type ChatSource = {
  conversation_id: string;
  message_id: string;
  title: string;
};

export function chatSource(
  preview: Record<string, unknown>,
): ChatSource | null {
  const source = preview.source as Partial<ChatSource> | undefined;
  if (
    preview.origin !== 'skill_offer' ||
    !source ||
    typeof source.conversation_id !== 'string' ||
    typeof source.message_id !== 'string'
  )
    return null;
  return {
    conversation_id: source.conversation_id,
    message_id: source.message_id,
    title: typeof source.title === 'string' ? source.title : '',
  };
}

/** The review sheet for a draft from a chat, opened from Settings › Skills. */
export function ChatSkillReview({
  source,
  onClose,
  onChanged,
}: {
  source: ChatSource;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { controller } = useRuntime();
  const [page, setPage] = useState<SkillOfferPage | null>(null);
  const [failed, setFailed] = useState('');
  useEffect(() => {
    let live = true;
    controller.skillOffers(source.conversation_id).then(
      (value) => live && setPage(value),
      (cause: unknown) => live && setFailed(clientError(cause).message),
    );
    return () => {
      live = false;
    };
  }, [controller, source.conversation_id]);
  const offer = page?.items.find(
    (item) => item.message_id === source.message_id,
  );
  const notify = useNotify();
  useEffect(() => {
    if (!failed) return;
    notify(failed, 'danger');
    onClose();
  }, [failed, notify, onClose]);
  if (!offer) return null;
  return (
    <SkillDraftSheet
      open
      conversationId={source.conversation_id}
      chatTitle={source.title}
      messageId={source.message_id}
      offer={offer}
      onChanged={(next) => {
        setPage(next);
        onChanged();
      }}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    />
  );
}

/** The one-line offer under the newest reply: "Save “Monthly expense report” as a skill?" */
export function SkillOfferHint({ offer }: { offer: SkillOffer }) {
  const offers = useSkillOffers();
  if (!offers || offer.status !== 'offered' || offer.origin !== 'offered')
    return null;
  return (
    <div
      className="skill-offer-hint"
      role="group"
      aria-label="Save as a skill"
      data-offer={offer.id}
    >
      <BookmarkPlus className="skill-offer-hint-icon" size={16} aria-hidden />
      <span className="skill-offer-hint-text">
        Save “{offer.task_name}” as a skill?
      </span>
      <span className="skill-offer-hint-actions">
        <Button
          variant="secondary"
          className="small"
          onClick={() => offers.save(offer.message_id)}
        >
          Save as skill
        </Button>
        <IconButton
          size="sm"
          label="Not now"
          onClick={() => offers.dismiss(offer)}
        >
          <X size={15} aria-hidden />
        </IconButton>
      </span>
    </div>
  );
}
