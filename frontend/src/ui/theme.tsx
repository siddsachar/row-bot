import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  bootstrapTheme,
  THEME_KEY,
  TOKENS,
  type ThemePreference,
} from './theme-model';

const ThemeContext = createContext<{
  preference: ThemePreference;
  update: (patch: Partial<ThemePreference>) => void;
} | null>(null);
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState(() => bootstrapTheme(TOKENS));
  const preferenceRef = useRef(preference);
  preferenceRef.current = preference;
  useEffect(() => {
    const scheme = matchMedia('(prefers-color-scheme: dark)');
    const transparency = matchMedia('(prefers-reduced-transparency: reduce)');
    const apply = () => {
      bootstrapTheme(TOKENS, preferenceRef.current);
    };
    const storage = (event: StorageEvent) => {
      if (event.key === THEME_KEY) {
        const next = bootstrapTheme(TOKENS);
        preferenceRef.current = next;
        setPreference(next);
      }
    };
    scheme.addEventListener('change', apply);
    transparency.addEventListener('change', apply);
    window.addEventListener('storage', storage);
    return () => {
      scheme.removeEventListener('change', apply);
      transparency.removeEventListener('change', apply);
      window.removeEventListener('storage', storage);
    };
  }, []);
  function update(patch: Partial<ThemePreference>) {
    const next = bootstrapTheme(TOKENS, {
      ...preference,
      ...patch,
      version: 1,
    });
    preferenceRef.current = next;
    try {
      localStorage.setItem(THEME_KEY, JSON.stringify(next));
    } catch {
      /* Session-only preference remains usable. */
    }
    setPreference(next);
  }
  return (
    <ThemeContext.Provider value={{ preference, update }}>
      {children}
    </ThemeContext.Provider>
  );
}
export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('ThemeProvider is required');
  return context;
}
