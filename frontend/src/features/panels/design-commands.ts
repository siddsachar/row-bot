/**
 * The open Design panel's actions, offered in the global ⌘K palette while
 * the design is shown (no second design palette; parity row 24). A panel
 * registers while it is visible and removes itself when hidden.
 */
export type DesignCommand = {
  id: string;
  label: string;
  keywords?: string;
  run: () => void;
};

export type DesignCommandSet = {
  resourceId: string;
  title: string;
  commands: DesignCommand[];
};

const sets = new Map<symbol, DesignCommandSet>();

export function registerDesignCommands(set: DesignCommandSet): () => void {
  const key = Symbol(set.resourceId);
  sets.set(key, set);
  return () => {
    sets.delete(key);
  };
}

/** One set per shown design (the latest registration wins for a design). */
export function designCommandSets(): DesignCommandSet[] {
  const byResource = new Map<string, DesignCommandSet>();
  for (const set of sets.values()) byResource.set(set.resourceId, set);
  return [...byResource.values()];
}
