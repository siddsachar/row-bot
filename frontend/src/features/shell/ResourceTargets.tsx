import type { ResourceView } from '../../api/types';
import { Menu } from '../../ui/primitives';

export type TargetKind = 'artifact' | 'workspace';

/** One kind's write target: its choices (None first) and the current one's name. */
export type TargetGroup = {
  kind: TargetKind;
  label: 'Design' | 'Folder';
  currentName: string;
  choices: {
    bindingId: string | null;
    label: string;
    disabled: boolean;
    selected: boolean;
  }[];
};

/**
 * The design and folder write targets a chat can choose between, for the
 * composer's chips and the phone's Chat settings (B2). A kind with nothing
 * bound is left out.
 */
export function targetGroups(
  resources: readonly ResourceView[],
  selected: readonly string[],
): TargetGroup[] {
  return (['artifact', 'workspace'] as const).flatMap((kind) => {
    const choices = resources.filter(
      (resource) => resource.binding.kind === kind,
    );
    if (!choices.length) return [];
    const current = choices.find((resource) =>
      selected.includes(resource.binding.binding_id),
    );
    const name = (resource: ResourceView) =>
      `${resource.title}${choices.filter((other) => other.title === resource.title).length > 1 ? ` · ${resource.binding.resource_id}` : ''}${resource.available ? '' : ' (unavailable)'}`;
    return [
      {
        kind,
        label: kind === 'artifact' ? 'Design' : 'Folder',
        currentName: current ? name(current) : 'None',
        choices: [
          {
            bindingId: null,
            label: 'None',
            disabled: false,
            selected: !current,
          },
          ...choices.map((resource) => ({
            bindingId: resource.binding.binding_id,
            label: name(resource),
            disabled: !resource.available,
            selected:
              resource.binding.binding_id === current?.binding.binding_id,
          })),
        ],
      },
    ];
  });
}

/** Named write targets are explicit choices, independent of the visible panel. */
export default function ResourceTargets({
  resources,
  selected,
  onChange,
}: {
  resources: readonly ResourceView[];
  selected: readonly string[];
  onChange: (kind: TargetKind, bindingId: string | null) => void;
}) {
  return (
    <div
      className="write-targets"
      role="group"
      aria-label="Resource write targets"
      data-empty={!selected.length || undefined}
    >
      {targetGroups(resources, selected).map((group) => (
        <Menu
          key={group.kind}
          label={`${group.label} target`}
          hint={`${group.label} target: ${group.currentName}`}
          variant="ghost"
          className="resource-target"
          actions={group.choices.map((choice) => ({
            label: choice.label,
            disabled: choice.disabled,
            selected: choice.selected,
            onSelect: () => onChange(group.kind, choice.bindingId),
          }))}
        >
          <span title={`${group.label} · ${group.currentName}`}>
            {group.label} · {group.currentName}
          </span>
        </Menu>
      ))}
    </div>
  );
}
