/**
 * A small fuzzy matcher for the command palette. Every query character must
 * appear in order; contiguous runs, word starts and an early first match
 * score higher. Returns null when the text does not match.
 */
export type FuzzyMatch = { score: number; indices: number[] };

const WORD_BREAK = /[\s\-_/.:·,()[\]]/;

export function fuzzyMatch(query: string, text: string): FuzzyMatch | null {
  const needle = query.trim().toLowerCase();
  if (!needle) return { score: 0, indices: [] };
  const haystack = text.toLowerCase();
  // A literal substring always wins over a scattered match.
  const literal = haystack.indexOf(needle);
  if (literal >= 0) {
    // A title that opens with an emoji or punctuation still starts here.
    const prefix = !/[\p{L}\p{N}]/u.test(haystack.slice(0, literal));
    const wordStart = prefix || WORD_BREAK.test(haystack[literal - 1]);
    return {
      score:
        1000 +
        (prefix ? 300 : wordStart ? 150 : 0) -
        (prefix ? 0 : Math.min(literal, 100)) -
        Math.min(haystack.length - needle.length, 100) / 10,
      indices: Array.from({ length: needle.length }, (_, i) => literal + i),
    };
  }
  const indices: number[] = [];
  let score = 0;
  let from = 0;
  let previous = -2;
  for (const char of needle) {
    if (char === ' ') continue;
    const index = haystack.indexOf(char, from);
    if (index < 0) return null;
    const wordStart = index === 0 || WORD_BREAK.test(haystack[index - 1]);
    score += index === previous + 1 ? 12 : wordStart ? 8 : 1;
    indices.push(index);
    previous = index;
    from = index + 1;
  }
  score -= Math.min(indices[0] ?? 0, 40) / 4;
  return { score, indices };
}

/** Best match of a query across several fields (title, keywords, group). */
export function fuzzyScore(
  query: string,
  ...fields: (string | undefined)[]
): number | null {
  let best: number | null = null;
  fields.forEach((field, position) => {
    if (!field) return;
    const match = fuzzyMatch(query, field);
    // Later fields (keywords, descriptions) weigh less than the label.
    if (match) {
      const score = match.score - position * 50;
      if (best === null || score > best) best = score;
    }
  });
  return best;
}
