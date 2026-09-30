import { StatusDot, type Tone } from '../../ui/primitives';

/**
 * A delegated agent's state in five words (B240): the same in Agents, the
 * transcript's stubs (B241) and an agent's own conversation (B242).
 */
export type AgentState = 'working' | 'waiting' | 'done' | 'failed' | 'stopped';

const STATES: Record<string, AgentState> = {
  queued: 'working',
  starting: 'working',
  running: 'working',
  waiting: 'working',
  stopping: 'working',
  waiting_approval: 'waiting',
  waiting_user: 'waiting',
  paused: 'waiting',
  interrupted: 'waiting',
  completed: 'done',
  completed_delivery_failed: 'done',
  failed: 'failed',
  blocked: 'failed',
  timed_out: 'failed',
  stopped: 'stopped',
  cancelled: 'stopped',
};

export const AGENT_STATE_WORDS: Record<AgentState, string> = {
  working: 'Working',
  waiting: 'Waiting for you',
  done: 'Done',
  failed: 'Failed',
  stopped: 'Stopped',
};

const TONES: Record<AgentState, Tone> = {
  working: 'accent',
  waiting: 'warning',
  done: 'success',
  failed: 'danger',
  stopped: 'neutral',
};

/** A run status as one of the five states; anything unknown has ended. */
export function agentState(status: string): AgentState {
  return STATES[status] ?? 'stopped';
}

/** Still going: it can be messaged or stopped. */
export function agentLive(state: AgentState) {
  return state === 'working' || state === 'waiting';
}

/** Status as shape, then word: a pulsing dot while working, a ring once stopped. */
export function AgentStatus({ state }: { state: AgentState }) {
  return (
    <StatusDot
      className="agent-status"
      tone={TONES[state]}
      pulse={state === 'working'}
      label={AGENT_STATE_WORDS[state]}
      showLabel
    />
  );
}
