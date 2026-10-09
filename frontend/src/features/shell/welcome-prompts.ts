/** First-run suggestions for a new conversation. */
export const EXAMPLE_PROMPTS = [
  'Summarize my latest documents and suggest next actions',
  'Create a disabled workflow for a weekly research briefing',
  'Design a landing page for a new product',
  'What do you remember about my current projects?',
  'Research the latest AI agent trends and cite sources',
  'Check my upcoming calendar and prepare a daily plan',
] as const;

export const EXAMPLE_LABELS = [
  'Summarize documents',
  'Plan a weekly brief',
  'Design a landing page',
  'Recall my projects',
  'Research a topic',
  'Prepare for today',
] as const;

/** Suggestions for a new conversation working on a design. */
export const DESIGN_PROMPTS = [
  'Tighten the copy on every page: shorter headlines and one idea per page',
  'Try a different layout for this design, keeping its content',
  'Check the design review and fix what it finds',
  'Make the colours, fonts and spacing consistent on every page',
] as const;

export const DESIGN_LABELS = [
  'Tighten the copy',
  'Try another layout',
  'Check the review',
  'Polish the look',
] as const;
