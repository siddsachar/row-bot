import { createContext, useContext } from 'react';
import { Check, X } from 'lucide-react';
import type { DelegatedRun } from '../../api/types';
import { AgentAvatar, agentSeed } from '../../ui/AgentAvatar';
import { Hint } from '../../ui/primitives';
import { AGENT_STATE_WORDS, agentState, type AgentState } from './agent-status';
import type { TracedAgent } from './transcript-model';

/** The conversation's delegated-activity feed, as the transcript sees it. */
export type DelegatedRuns = {
  /** Each agent's latest state from the feed's first page, by run. */
  runs: ReadonlyMap<string, DelegatedRun>;
  /** The feed has answered for this conversation at least once. */
  settled: boolean;
  /** Open an agent's own conversation. */
  open: (runId: string) => void;
};

export const DelegatedRunsContext = createContext<DelegatedRuns | null>(null);

/** Why a failed agent stopped, in words, from what its run says publicly. */
function failure(run: DelegatedRun | undefined) {
  if (run?.status === 'timed_out') return 'It ran out of time.';
  if (run?.status === 'blocked') return 'It was blocked.';
  const summary = run?.summary.trim().split('\n')[0] ?? '';
  return summary ? summary.slice(0, 160) : 'Open its conversation to see why.';
}

function StubStatus({ state }: { state: AgentState }) {
  return (
    <span className="agent-stub-status" data-state={state}>
      {state === 'done' ? (
        <Check aria-hidden />
      ) : state === 'failed' ? (
        <X aria-hidden />
      ) : (
        <span
          className="agent-stub-dot"
          data-pulse={state === 'working' ? 'true' : undefined}
          aria-hidden
        />
      )}
      {AGENT_STATE_WORDS[state]}
    </span>
  );
}

/**
 * Where a turn started agents, one row of small stubs, one per agent (B241):
 * its icon, name and live status, updating in place from the delegated
 * feed. The turn keeps the agents it started, so a reload shows them with
 * their final state; clicking one opens its conversation.
 */
export function AgentStubs({ agents }: { agents: TracedAgent[] }) {
  const feed = useContext(DelegatedRunsContext);
  if (!agents.length) return null;
  return (
    <ul className="agent-stubs" aria-label="Agents started">
      {agents.map((agent) => {
        const run = feed?.runs.get(agent.run_id);
        const name = run?.name ?? agent.name;
        // Until the feed answers, the stored status may be long out of date.
        const state =
          run || feed?.settled ? agentState(run?.status ?? agent.status) : null;
        const why = state === 'failed' ? failure(run) : '';
        const words = state ? AGENT_STATE_WORDS[state] : '';
        return (
          <li key={agent.run_id}>
            <Hint label={[name, words, why].filter(Boolean).join(' · ')}>
              <button
                type="button"
                className="agent-stub"
                data-state={state ?? undefined}
                aria-label={words ? `${name}, ${words}` : name}
                aria-description={why || undefined}
                disabled={!feed}
                onClick={() => feed?.open(agent.run_id)}
              >
                <AgentAvatar
                  seed={agentSeed(
                    run?.profile_id || agent.profile_id,
                    agent.run_id,
                  )}
                  size={18}
                />
                <span className="agent-stub-name">{name}</span>
                {state && <StubStatus state={state} />}
              </button>
            </Hint>
          </li>
        );
      })}
    </ul>
  );
}
