import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { Me, Person } from './types';

/**
 * Who is who: the signed-in admin and everyone who has signed in, loaded once
 * by the shell. The pages that show principal ids (devices, budgets, usage,
 * audit) name people through it, and People changes it.
 */
type Directory = {
  me: Me;
  people: Person[];
  nameOf: (id: string) => string;
  groups: string[];
  /** Put the server's answer for one person in place of the old one. */
  replace: (person: Person) => void;
};

const DirectoryContext = createContext<Directory | null>(null);

export function DirectoryProvider({
  me,
  people: loaded,
  children,
}: {
  me: Me;
  people: Person[];
  children: ReactNode;
}) {
  const [people, setPeople] = useState(loaded);
  const value = useMemo<Directory>(() => {
    const names = new Map(
      people.map((person) => [
        person.id,
        person.display_name || person.email || 'A person',
      ]),
    );
    if (!names.has(me.id)) names.set(me.id, me.display_name || 'You');
    return {
      me,
      people,
      nameOf: (id) => names.get(id) ?? 'Someone no longer listed',
      groups: [...new Set(people.flatMap((person) => person.groups))].sort(),
      replace: (person) =>
        setPeople((current) =>
          current.map((item) => (item.id === person.id ? person : item)),
        ),
    };
  }, [me, people]);
  return (
    <DirectoryContext.Provider value={value}>
      {children}
    </DirectoryContext.Provider>
  );
}

export function useDirectory(): Directory {
  const directory = useContext(DirectoryContext);
  if (!directory) throw new Error('DirectoryProvider is required');
  return directory;
}
