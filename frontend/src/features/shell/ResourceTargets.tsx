import type { ResourceView } from '../../api/types';
import { Menu } from '../../ui/primitives';

/** Named write targets are explicit choices, independent of the visible panel. */
export default function ResourceTargets({
  resources,
  selected,
  onChange,
}: {
  resources: readonly ResourceView[];
  selected: readonly string[];
  onChange: (kind: 'artifact' | 'workspace', bindingId: string | null) => void;
}) {
  return (
    <div
      className="write-targets"
      role="group"
      aria-label="Resource write targets"
    >
      {(['artifact', 'workspace'] as const).map((kind) => {
        const choices = resources.filter(
          (resource) => resource.binding.kind === kind,
        );
        if (!choices.length) return null;
        const current = choices.find((resource) =>
          selected.includes(resource.binding.binding_id),
        );
        const label = kind === 'artifact' ? 'Deck' : 'Folder';
        const currentName = current
          ? `${current.title}${choices.filter((other) => other.title === current.title).length > 1 ? ` · ${current.binding.resource_id}` : ''}${current.available ? '' : ' (unavailable)'}`
          : 'None';
        return (
          <Menu
            key={kind}
            label={`${label} target`}
            hint={`${label} target: ${currentName}`}
            variant="ghost"
            className="resource-target"
            actions={[
              {
                label: 'None',
                selected: !current,
                onSelect: () => onChange(kind, null),
              },
              ...choices.map((resource) => ({
                label: `${resource.title}${choices.filter((other) => other.title === resource.title).length > 1 ? ` · ${resource.binding.resource_id}` : ''}${resource.available ? '' : ' (unavailable)'}`,
                disabled: !resource.available,
                selected:
                  resource.binding.binding_id === current?.binding.binding_id,
                onSelect: () => onChange(kind, resource.binding.binding_id),
              })),
            ]}
          >
            <span>
              {label} · {currentName}
            </span>
          </Menu>
        );
      })}
    </div>
  );
}
