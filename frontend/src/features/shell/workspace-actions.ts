import { createContext, useContext } from 'react';
import type { ProfileSummary } from '../settings/GoalProfileSettings';

type WorkspaceActions = {
  resetLayout: () => void;
  /** Start a new chat that uses this agent profile. */
  startProfileChat?: (profile: ProfileSummary) => void;
};

export const WorkspaceActionsContext = createContext<WorkspaceActions | null>(
  null,
);

export function useWorkspaceActions() {
  return useContext(WorkspaceActionsContext);
}
