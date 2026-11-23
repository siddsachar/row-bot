import type {
  VideoProjectView,
  VideoPublishKit,
  VideoScene,
  VideoSceneWrite,
  VideoVersionView,
} from '../../api/types';
import { shortTime } from '../shell/conversation-groups';

/** The other shapes a made version can be exported in, and where each fits. */
export const SHAPES = [
  { shape: '16:9', label: 'Wide 16:9', fits: 'X, YouTube', limit: 140 },
  { shape: '9:16', label: 'Tall 9:16', fits: 'Shorts, Reels', limit: 60 },
  { shape: '1:1', label: 'Square 1:1', fits: 'X, LinkedIn', limit: 140 },
  {
    shape: '4:5',
    label: 'Portrait 4:5',
    fits: 'Instagram, Facebook',
    limit: 0,
  },
  { shape: 'gif', label: 'GIF', fits: 'Anywhere, without sound', limit: 0 },
] as const;

/** Where a shape fits, and when the video is too long for it: "Shorts, Reels · longer than Shorts allow (1:00)". */
export function shapeHint(
  item: (typeof SHAPES)[number],
  seconds: number,
): string {
  if (!item.limit || seconds <= item.limit) return item.fits;
  const whole = Math.round(item.limit);
  const limit = `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
  return `${item.fits} · longer than ${item.shape === '9:16' ? 'Shorts allow' : 'X allows'} (${limit})`;
}

/** What a scene's footage is, as its storyboard card names it (C1). */
export const FOOTAGE = {
  clip: 'Clip',
  picture: 'Picture',
  ai: 'AI shot',
  card: 'Title card',
  screen: 'Screen recording',
} as const;
export type Footage = keyof typeof FOOTAGE;

export function footageOf(scene: VideoScene | undefined): Footage {
  return scene?.footage ?? (scene?.source ? 'clip' : 'card');
}

/** A scene whose footage is still to make (an AI shot planned on the storyboard): Row-Bot makes it. */
export function isPlanned(scene: VideoScene): boolean {
  return !scene.source && footageOf(scene) !== 'card';
}

/** How long a scene lasts, when its storyboard says: as made, else its own length, else null. */
export function sceneSeconds(
  scene: VideoSceneWrite,
  made?: VideoScene,
): number | null {
  if (made?.length !== null && made?.length !== undefined) return made.length;
  if (scene.end === null || scene.end === undefined)
    return scene.source ? null : made && isPlanned(made) ? null : 3;
  return Math.max(0, (scene.end - (scene.start ?? 0)) / (scene.speed ?? 1));
}

/** "0:24": a whole video's length. */
export function clockWords(seconds: number): string {
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

/** The speeds a scene offers; 2× and up show a badge on the video. */
export const SPEEDS = [0.5, 1, 1.5, 2, 3, 4] as const;

/** The sound effects and ambience a scene can carry (C6), all made by Row-Bot. */
export const EFFECTS = {
  whoosh: 'Whoosh',
  hit: 'Hit',
  click: 'Click',
  rain: 'Rain',
  wind: 'Wind',
  crowd: 'Crowd',
  waves: 'Waves',
  footsteps: 'Footsteps',
} as const;
/** Effects that run under the whole scene; the rest play as it starts. */
export const AMBIENCE = new Set([
  'rain',
  'wind',
  'crowd',
  'waves',
  'footsteps',
]);
export const MAX_EFFECTS = 4;

/** "Rain", or a sound file's own name. */
export function effectWords(effect: string): string {
  return EFFECTS[effect as keyof typeof EFFECTS] ?? effect;
}

/** Music made to fit the cut, instead of a music file. */
export const FIT = 'fit';

/** What plays under the video: "Made to fit · calm", a file's name, or "" for none. */
export function musicWords(view: VideoProjectView): string {
  if (view.music !== FIT) return view.music;
  return view.music_mood ? `Made to fit · ${view.music_mood}` : 'Made to fit';
}

/** Where music made to fit changes section: each scene whose music cue isn't the one before's. */
export function musicSections(scenes: { cue?: string | null }[]): number[] {
  const at: number[] = [];
  scenes.forEach((scene, index) => {
    const cue = (scene.cue ?? '').trim().toLowerCase();
    if (
      index &&
      cue &&
      cue !== (scenes[index - 1].cue ?? '').trim().toLowerCase()
    )
      at.push(index);
  });
  return at;
}

/** A video's grades (C5), as the panel names them. */
export const GRADES = {
  natural: 'Natural',
  warm: 'Warm',
  cinematic: 'Cinematic',
  vivid: 'Vivid',
  bw: 'Black and white',
} as const;
export type Grade = keyof typeof GRADES;

/** How captions look (C7), as the panel and the brand kit name them. */
export const CAPTION_STYLES = {
  box: 'In a box',
  clean: 'Clean',
  bold: 'Bold, outlined',
  title: 'Large, centred',
  word: 'Big words (shorts)',
} as const;
export type CaptionStyle = keyof typeof CAPTION_STYLES;

/** A video's look as it's made (C5): its grade, grain, letterbox and transitions, and its captions' style (C7). */
export type VideoLook = {
  grade: Grade;
  grain: boolean;
  letterbox: boolean;
  transitions: 'cut' | 'crossfade';
  caption_style: CaptionStyle;
};

/** Where a video can go (C10), as the panel names each place. */
export const PLATFORMS = {
  youtube: 'YouTube',
  shorts: 'YouTube Shorts',
  tiktok: 'TikTok',
  reels: 'Instagram Reels',
  instagram: 'Instagram',
  facebook: 'Facebook',
  x: 'X',
  linkedin: 'LinkedIn',
} as const;
export type Platform = keyof typeof PLATFORMS;

/** A publish kit's description as it's pasted: its words, then a YouTube video's chapters. */
export function publishDescription(kit: VideoPublishKit): string {
  return [kit.description, chapterLines(kit.chapters ?? [])]
    .filter(Boolean)
    .join('\n\n');
}

/** "0:00 Intro\n1:05 Pricing": a made video's chapters, as a YouTube description lists them. */
export function chapterLines(
  chapters: { at: number; title: string }[],
): string {
  return chapters
    .map((item) => `${clockWords(item.at)} ${item.title}`)
    .join('\n');
}

export type VideoDraft = {
  scenes: VideoSceneWrite[];
  captions: boolean;
  brand: boolean;
  voice: string;
  /** Each character's voice: name -> voice. */
  cast: Record<string, string>;
  look: VideoLook;
};

/** A version's look, as the panel shows it. */
export function lookOf(view: VideoProjectView): VideoLook {
  return {
    grade: view.grade ?? 'natural',
    grain: view.grain ?? false,
    letterbox: view.letterbox ?? false,
    transitions: view.transitions ?? 'cut',
    caption_style: view.caption_style ?? 'box',
  };
}

/** Only the look's choices the person changed: the rest stay the version's own, or its recipe's. */
export function lookChanges(
  view: VideoProjectView,
  look: VideoLook,
): Partial<VideoLook> {
  const before = lookOf(view);
  return Object.fromEntries(
    Object.entries(look).filter(
      ([key, value]) => before[key as keyof VideoLook] !== value,
    ),
  );
}

/** The scenes as the panel edits them, from a version. */
export function draftOf(view: VideoProjectView): VideoDraft {
  return {
    captions: view.captions,
    brand: view.brand,
    look: lookOf(view),
    voice: view.voice ?? '',
    cast: Object.fromEntries(
      (view.cast ?? []).map((member) => [member.name, member.voice]),
    ),
    scenes: view.scenes.map((scene) => ({
      scene_id: scene.scene_id,
      source: scene.source,
      start: scene.start,
      end: scene.end,
      speed: scene.speed,
      zoom: scene.zoom,
      card: scene.card,
      line: scene.line,
      speaker: scene.speaker ?? '',
      effects: [...(scene.effects ?? [])],
    })),
  };
}

export function isDirty(view: VideoProjectView, draft: VideoDraft): boolean {
  return JSON.stringify(draftOf(view)) !== JSON.stringify(draft);
}

/** The characters who speak in a draft: its cast's, then any new name a scene gives, each once. */
export function speakersOf(draft: VideoDraft): string[] {
  const names = new Map<string, string>();
  for (const name of [
    ...Object.keys(draft.cast),
    ...draft.scenes.map((scene) => (scene.speaker ?? '').trim()),
  ])
    if (name && !names.has(name.toLowerCase()))
      names.set(name.toLowerCase(), name);
  return [...names.values()];
}

/** "4.2 s", "1:05": how long a scene lasts on the video. */
export function lengthWords(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

/** "Version 3 · you · Edited in the panel". */
export function versionWords(item: VideoVersionView): string {
  return `Version ${item.number} · ${item.by === 'you' ? 'you' : 'Row-Bot'} · ${item.note}`;
}

export function statusWords(view: VideoProjectView): {
  label: string;
  detail: string;
  tone: 'neutral' | 'info' | 'success' | 'danger';
} {
  switch (view.status) {
    case 'voicing':
      return {
        label: 'Voicing',
        detail: 'Voicing the lines…',
        tone: 'info',
      };
    case 'rendering':
      return {
        label: 'Making',
        detail:
          view.edit?.status === 'getting_ffmpeg'
            ? 'Getting ffmpeg…'
            : 'Making the video…',
        tone: 'info',
      };
    case 'made':
      return {
        label: view.checks_passed === false ? 'Needs a look' : 'Made',
        detail: '',
        tone: view.checks_passed === false ? 'danger' : 'success',
      };
    case 'failed':
      return {
        label: 'Not made',
        detail: 'This version wasn’t made.',
        tone: 'danger',
      };
    case 'stopped':
      return {
        label: 'Stopped',
        detail: 'Making this version was stopped.',
        tone: 'neutral',
      };
    default:
      return {
        label: 'Storyboard',
        detail: 'Nothing is made yet.',
        tone: 'neutral',
      };
  }
}

/** When a version was made, for its card: "Version 2 · 10:42". */
export function madeWords(view: VideoProjectView): string {
  const item = view.versions.find((entry) => entry.number === view.number);
  return item
    ? `Version ${item.number} of ${view.latest} · ${shortTime(item.at)}`
    : `Version ${view.number}`;
}
