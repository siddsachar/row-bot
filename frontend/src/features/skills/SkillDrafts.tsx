import { useCallback, useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { BookmarkPlus } from 'lucide-react';
import type { SkillProposalPage } from '../../api/types';
import { useLiveUpdate } from '../../live';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';
import { ChatSkillReview, chatSource, type ChatSource } from './skill-offers';
import './skills.css';

type Waiting = { id: string; name: string; source: ChatSource };

const SETTLED = new Set(['applied', 'verified', 'rejected']);

/** Skills drafted from chats that wait for the person's review (C1), at the top of Skills. */
export default function SkillDrafts() {
  const { controller } = useRuntime();
  const headingId = useId();
  const [page, setPage] = useState<SkillProposalPage | null>(null);
  const [reviewing, setReviewing] = useState<ChatSource | null>(null);
  const read = useCallback(() => {
    Promise.resolve()
      .then(() => controller.skillProposals())
      .then(setPage, () => setPage(null));
  }, [controller]);
  useEffect(() => read(), [read]);
  useLiveUpdate(['skill_offers'], read);
  const close = useCallback(() => setReviewing(null), []);
  const waiting: Waiting[] = (page?.items ?? []).flatMap((item) => {
    const source = chatSource(item.preview);
    if (!source || item.type !== 'create_skill' || SETTLED.has(item.status))
      return [];
    const name =
      typeof item.preview.display_name === 'string'
        ? item.preview.display_name
        : item.title;
    return [{ id: item.id, name, source }];
  });
  if (!waiting.length && !reviewing) return null;
  return (
    <section aria-labelledby={headingId} className="skill-drafts">
      <h3 id={headingId}>Waiting for your review</h3>
      <ul className="skill-drafts-list">
        {waiting.map((item) => (
          <li key={item.id} className="skill-drafts-row">
            <BookmarkPlus size={16} aria-hidden className="skill-drafts-icon" />
            <span className="skill-drafts-text">
              <strong>{item.name}</strong>
              <small className="skill-proposal-source">
                From{' '}
                <Link
                  to={`/conversations/${encodeURIComponent(item.source.conversation_id)}`}
                >
                  {item.source.title || 'a chat'}
                </Link>
              </small>
            </span>
            <Button
              variant="secondary"
              className="small"
              aria-label={`Review ${item.name}`}
              onClick={() => setReviewing(item.source)}
            >
              Review
            </Button>
          </li>
        ))}
      </ul>
      {reviewing && (
        <ChatSkillReview source={reviewing} onClose={close} onChanged={read} />
      )}
    </section>
  );
}
