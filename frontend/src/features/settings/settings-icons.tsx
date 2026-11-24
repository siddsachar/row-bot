import type { ComponentType } from 'react';
import {
  Activity,
  AppWindow,
  ArrowUpCircle,
  Bot,
  Brain,
  Cloud,
  Cpu,
  Database,
  FileText,
  GraduationCap,
  History,
  KeyRound,
  Lightbulb,
  Mic,
  Palette,
  Settings2,
  Shield,
  SlidersHorizontal,
  Sparkles,
  UsersRound,
  Wrench,
} from 'lucide-react';

type Icon = ComponentType<{ size?: number; 'aria-hidden'?: boolean }>;

const icons: Record<string, Icon> = {
  preferences: SlidersHorizontal,
  appearance: Palette,
  buddy: Bot,
  proactive: Lightbulb,
  learning: GraduationCap,
  providers: Cloud,
  models: Cpu,
  voice: Mic,
  knowledge: Brain,
  documents: FileText,
  tracker: Activity,
  tools: Wrench,
  apps: AppWindow,
  skills: Sparkles,
  profiles: UsersRound,
  system: Shield,
  activity: History,
  access: KeyRound,
  updates: ArrowUpCircle,
  data: Database,
};

/** A Settings page's icon, in the list of pages and its header. */
export function SettingIcon({ id, size = 18 }: { id: string; size?: number }) {
  const Icon = icons[id] ?? Settings2;
  return <Icon size={size} aria-hidden />;
}
