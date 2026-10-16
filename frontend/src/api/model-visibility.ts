import type {
  ModelChoice,
  ModelVisibilityMutation,
  ModelVisibilityState,
} from './types';

/**
 * Favourite models first, hidden ones under "More models" (F10). The server
 * marks each listed model; these helpers apply a change to a list at once,
 * before the save settles.
 */

/** The favourites (in order) and hidden models a list shows. */
export function visibilityOf(
  models: readonly Pick<
    ModelChoice,
    'model_ref' | 'favourite_rank' | 'hidden'
  >[],
): ModelVisibilityState {
  return {
    schema_version: 1,
    favourites: models
      .filter((model) => model.favourite_rank != null)
      .sort((a, b) => (a.favourite_rank ?? 0) - (b.favourite_rank ?? 0))
      .map((model) => model.model_ref),
    hidden: models
      .filter((model) => model.hidden && model.favourite_rank == null)
      .map((model) => model.model_ref),
  };
}

/** One change, as the server makes it: adding moves a model to the end. */
export function applyVisibility(
  models: readonly Pick<
    ModelChoice,
    'model_ref' | 'favourite_rank' | 'hidden'
  >[],
  change: ModelVisibilityMutation,
): ModelVisibilityState {
  const current = visibilityOf(models);
  if (change.action === 'show_all') return { ...current, hidden: [] };
  const ref = change.model_ref ?? '';
  const favourites = current.favourites.filter((item) => item !== ref);
  const hidden = current.hidden.filter((item) => item !== ref);
  if (change.action === 'favourite') favourites.push(ref);
  if (change.action === 'hide') hidden.push(ref);
  return { schema_version: 1, favourites, hidden };
}

/** A listed model as far as these marks go: its reference and marks. */
type Marked = Pick<ModelChoice, 'model_ref' | 'favourite_rank' | 'hidden'>;

/** The list with each model marked from a saved preference. */
export function markModels<T extends Marked>(
  models: readonly T[],
  state: ModelVisibilityState,
): T[] {
  return models.map((model) => {
    const rank = state.favourites.indexOf(model.model_ref) + 1;
    const next: T = { ...model };
    delete next.favourite_rank;
    delete next.hidden;
    if (rank) next.favourite_rank = rank;
    else if (state.hidden.includes(model.model_ref)) next.hidden = true;
    return next;
  });
}

/** Put the marks back as they were before a change that didn't save. */
export function restoreVisibility<T extends Marked>(
  models: readonly T[],
  before: readonly Marked[],
): T[] {
  const marks = new Map(before.map((model) => [model.model_ref, model]));
  return models.map((model) => {
    const old = marks.get(model.model_ref);
    if (!old) return model;
    const next: T = { ...model };
    delete next.favourite_rank;
    delete next.hidden;
    if (old.favourite_rank != null) next.favourite_rank = old.favourite_rank;
    if (old.hidden) next.hidden = true;
    return next;
  });
}
