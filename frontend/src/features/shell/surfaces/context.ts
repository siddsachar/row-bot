import type { CSSProperties } from 'react';
import {
  asText,
  evaluate,
  truthy,
  type DataModel,
  type Scope,
  type SurfaceComponent,
} from './a2ui';

/** A press on a surface's button, before it is sent. */
export type Press = {
  name: string;
  componentId: string;
  context: Record<string, unknown>;
  label: string;
};

/** What every component of one surface draws from. */
export type Context = {
  byId: Map<string, SurfaceComponent>;
  model: DataModel;
  set: (path: string, value: unknown, scope: Scope) => void;
  press: (press: Press) => void;
  ready: boolean;
  pressed: string | null;
  uid: string;
  title: string;
};

/** A value of the surface, in words. */
export function text(value: unknown, context: Context, scope: Scope): string {
  return asText(evaluate(value, context.model, scope));
}

/** A component's @accessibility, as ARIA attributes. */
export function accessible(
  component: SurfaceComponent,
  context: Context,
  scope: Scope,
) {
  const a11y = component['@accessibility'] as
    | {
        label?: unknown;
        description?: unknown;
        hidden?: unknown;
        live?: string;
      }
    | undefined;
  if (!a11y) return {};
  return {
    'aria-label': a11y.label ? text(a11y.label, context, scope) : undefined,
    'aria-description': a11y.description
      ? text(a11y.description, context, scope)
      : undefined,
    'aria-hidden':
      truthy(evaluate(a11y.hidden, context.model, scope)) || undefined,
    'aria-live':
      a11y.live && a11y.live !== 'off' ? (a11y.live as 'polite') : undefined,
  };
}

/** A component's weight in its Row or Column, as flex growth. */
export function weighted(
  component: SurfaceComponent,
): CSSProperties | undefined {
  const weight = component.weight;
  return typeof weight === 'number' && weight > 0
    ? { flexGrow: weight, flexBasis: 0, minWidth: 0 }
    : undefined;
}
