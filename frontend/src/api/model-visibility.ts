import type {
  ModelChoice,
  ModelVisibilityMutation,
  ModelVisibilityState,
} from './types';

/**
 * Your models: the starred models every picker lists. The server marks each
 * listed model with its star's place; these helpers apply a change to a list
 * at once, before the save settles.
 */

/** The stars (in order) a list shows. */
export function visibilityOf(
  models: readonly Pick<ModelChoice, 'model_ref' | 'favourite_rank'>[],
): ModelVisibilityState {
  return {
    schema_version: 2,
    favourites: models
      .filter((model) => model.favourite_rank != null)
      .sort((a, b) => (a.favourite_rank ?? 0) - (b.favourite_rank ?? 0))
      .map((model) => model.model_ref),
  };
}

/** One change, as the server makes it: starring moves a model to the end. */
export function applyVisibility(
  models: readonly Pick<ModelChoice, 'model_ref' | 'favourite_rank'>[],
  change: ModelVisibilityMutation,
): ModelVisibilityState {
  const favourites = visibilityOf(models).favourites.filter(
    (item) => item !== change.model_ref,
  );
  if (change.action === 'favourite') favourites.push(change.model_ref);
  return { schema_version: 2, favourites };
}

/** A listed model as far as stars go: its reference and its star's place. */
type Marked = Pick<ModelChoice, 'model_ref' | 'favourite_rank'>;

/** The list with each model marked from saved stars. */
export function markModels<T extends Marked>(
  models: readonly T[],
  state: ModelVisibilityState,
): T[] {
  return models.map((model) => {
    const rank = state.favourites.indexOf(model.model_ref) + 1;
    const next: T = { ...model };
    delete next.favourite_rank;
    if (rank) next.favourite_rank = rank;
    return next;
  });
}

/** Put the stars back as they were before a change that didn't save. */
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
    if (old.favourite_rank != null) next.favourite_rank = old.favourite_rank;
    return next;
  });
}
