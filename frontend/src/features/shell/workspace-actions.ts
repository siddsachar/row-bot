import { createContext, useContext } from 'react';

type WorkspaceActions = {
  resetLayout: () => void;
};

export const WorkspaceActionsContext = createContext<WorkspaceActions | null>(
  null,
);

export function useWorkspaceActions() {
  return useContext(WorkspaceActionsContext);
}
