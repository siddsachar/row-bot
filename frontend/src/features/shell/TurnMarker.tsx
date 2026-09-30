import { createContext, useContext } from 'react';
import { UserRound } from 'lucide-react';
import glyph from '../../assets/row_bot_glyph_256.png';
import { AgentAvatar } from '../../ui/AgentAvatar';

/** Who speaks in this conversation's transcript (B271). */
export type Speakers = {
  /** The selected Buddy's still image ('' shows Row-Bot's own glyph). */
  buddy: string;
  /** In a delegated agent's own conversation, the agent replies. */
  agent: { seed: string; name: string } | null;
};

export const SpeakersContext = createContext<Speakers>({
  buddy: '',
  agent: null,
});

/**
 * The small round marker at the start of a turn (B271): Buddy's still for
 * Row-Bot, the agent's icon in an agent's own conversation, a person for you.
 * It hangs in the gutter beside wide columns and sits in a small header line
 * on narrow ones. Decorative: the author stays in the message's name.
 */
export function TurnMarker({ role }: { role: 'user' | 'assistant' }) {
  const { buddy, agent } = useContext(SpeakersContext);
  return (
    <span className="turn-lead" aria-hidden="true">
      {role === 'user' ? (
        <span className="turn-marker turn-marker-user">
          <UserRound />
        </span>
      ) : agent ? (
        <span className="turn-marker">
          <AgentAvatar seed={agent.seed} size={22} />
        </span>
      ) : (
        <span
          className="turn-marker turn-marker-buddy"
          data-still={buddy ? 'true' : undefined}
        >
          <img alt="" src={buddy || glyph} />
        </span>
      )}
      <span className="turn-author">
        {role === 'user' ? 'You' : (agent?.name ?? 'Row-Bot')}
      </span>
    </span>
  );
}
