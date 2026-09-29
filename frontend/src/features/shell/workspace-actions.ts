import { createContext, useContext, type ReactNode } from 'react';
import type { ProfileSummary } from '../settings/GoalProfileSettings';

type WorkspaceActions = {
  resetLayout: () => void;
  /** Start a new chat that uses this agent profile. */
  startProfileChat?: (profile: ProfileSummary) => void;
  /** Start a new chat with this text waiting in the composer (never sent). */
  newChat?: (draft?: string) => void;
  /**
   * Below desktop width a routed page that has its own header carries the
   * navigation toggle and Workspace commands there, so phones and tablets
   * see one header, not two stacked bars (B119).
   */
  compactControls?: { navigation: ReactNode; commands: ReactNode };
};

export const WorkspaceActionsContext = createContext<WorkspaceActions | null>(
  null,
);

export function useWorkspaceActions() {
  return useContext(WorkspaceActionsContext);
}
