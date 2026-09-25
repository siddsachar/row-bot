import { useMemo, useState, type ReactNode } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { X } from 'lucide-react';
import type { ConversationComposer } from '../../api/types';
import { Button, Field, Input } from '../../ui/primitives';

export type ComposerSkillAction = (
  action: 'activate' | 'remove' | 'dismiss' | 'reset',
  skillId?: string,
) => Promise<void>;

export function ComposerSkillChips({
  composer,
  disabled,
  action,
}: {
  composer: ConversationComposer;
  disabled: boolean;
  action: ComposerSkillAction;
}) {
  if (!composer.active_skills.length && !composer.suggestions.length)
    return null;
  return (
    <div className="composer-skill-chips" aria-label="Smart Skills">
      {composer.active_skills.map((skill) => (
        <span
          className="composer-skill-chip"
          data-skill-source={skill.source}
          key={skill.id}
          title={`${skill.description}${skill.description ? ' · ' : ''}${skill.source}`}
        >
          <span aria-hidden>{skill.icon}</span> {skill.display_name}
          <small>{skill.source}</small>
          {skill.removable && (
            <Button
              iconOnly
              variant="ghost"
              aria-label={`Remove ${skill.display_name} from this chat`}
              disabled={disabled}
              onClick={() => void action('remove', skill.id)}
            >
              <X size={14} aria-hidden />
            </Button>
          )}
        </span>
      ))}
      {composer.suggestions.map((skill) => (
        <span className="composer-skill-suggestion" key={skill.id}>
          <Button
            variant="ghost"
            disabled={disabled}
            title={skill.reason}
            onClick={() => void action('activate', skill.skill_id)}
          >
            {skill.icon} Use {skill.display_name}
          </Button>
          <Button
            iconOnly
            variant="ghost"
            aria-label={`Dismiss ${skill.display_name} suggestion`}
            disabled={disabled}
            onClick={() => void action('dismiss', skill.skill_id)}
          >
            <X size={14} aria-hidden />
          </Button>
        </span>
      ))}
    </div>
  );
}

/** Wrap the element the Skills picker should open from. */
export const SkillsAnchor = Popover.Anchor;

/**
 * The Smart Skills picker. Its children must contain a `SkillsAnchor` (the
 * composer's + button); it opens from the + menu or the /skills command.
 */
export default function ComposerSkills({
  composer,
  disabled,
  action,
  open: controlledOpen,
  onOpenChange,
  children,
}: {
  composer: ConversationComposer;
  disabled: boolean;
  action: ComposerSkillAction;
  open?: boolean;
  onOpenChange?(open: boolean): void;
  /** Content containing the `SkillsAnchor`. */
  children: ReactNode;
}) {
  const [localOpen, setLocalOpen] = useState(false);
  const open = controlledOpen ?? localOpen;
  const setOpen = onOpenChange ?? setLocalOpen;
  const [query, setQuery] = useState('');
  const activeIds = useMemo(
    () => new Set(composer.active_skills.map((skill) => skill.id)),
    [composer.active_skills],
  );
  const available = composer.commands
    .filter(
      (command) =>
        command.handler_kind === 'activate_skill' &&
        command.skill_id &&
        !activeIds.has(command.skill_id),
    )
    .filter((command) =>
      `${command.label} ${command.description} ${command.skill_id}`
        .toLocaleLowerCase()
        .includes(query.trim().toLocaleLowerCase()),
    );
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      {children}
      <Popover.Portal>
        <Popover.Content
          className="popover surface-effect skills-menu"
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          aria-label="Smart Skills"
        >
          <strong>Smart Skills</strong>
          <small>
            {composer.active_skills.length} active. Changes apply to this
            conversation; global library pins stay unchanged.
          </small>
          <Field label="Search available skills">
            <Input
              value={query}
              placeholder="Search Skills"
              onChange={(event) => setQuery(event.target.value)}
            />
          </Field>
          {composer.library.availability === 'unavailable' ? (
            <small role="status">The Skills library is unavailable.</small>
          ) : available.length ? (
            <div className="skills-menu-list">
              {available.map((command) => (
                <Button
                  key={command.id}
                  variant="ghost"
                  className="thinking-option"
                  disabled={disabled || !command.skill_id}
                  title={command.description}
                  onClick={() => {
                    void action('activate', command.skill_id ?? undefined);
                    setOpen(false);
                  }}
                >
                  {command.icon} {command.label}
                </Button>
              ))}
            </div>
          ) : (
            <small role="status">No available Skills match.</small>
          )}
          <Button
            variant="ghost"
            disabled={disabled}
            onClick={() => void action('reset')}
          >
            Reset Skills for this chat
          </Button>
          <Popover.Close asChild>
            <Button variant="ghost">Close Skills</Button>
          </Popover.Close>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
