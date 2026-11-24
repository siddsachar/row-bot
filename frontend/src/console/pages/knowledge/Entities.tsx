import { useEffect, useState } from 'react';
import {
  Archive,
  ArchiveRestore,
  GitMerge,
  Network,
  Pencil,
  Search,
  UserCheck,
} from 'lucide-react';
import {
  Button,
  EmptyState,
  EntityList,
  EntityRow,
  Field,
  IconButton,
  InlineEmpty,
  Input,
  Select,
} from '../../../ui/primitives';
import { useNotify } from '../../../ui/overlays';
import { ApiError, query, request } from '../../api';
import { useDirectory } from '../../directory';
import { FormDialog } from '../../form-dialog';
import { Loaded, useLoad } from '../../load';
import type {
  Entity,
  EntityDetail,
  EntityType,
  Graph,
  GraphNode,
  Space,
} from '../../types';
import { count, problem } from '../../words';
import { ListInput } from '../policy/controls';
import { ENTITY_TYPES, plural, relationSentence, same } from './words';

type Curation = {
  subject?: string;
  entity_type?: EntityType;
  description?: string;
  aliases?: string[];
};

/** What an entry's row and forms need, from the graph or the server's answer. */
const asNode = (entity: Entity, node: GraphNode): GraphNode => ({
  ...node,
  subject: entity.subject,
  entity_type: entity.entity_type,
  description: entity.description,
  aliases: entity.aliases,
  status: entity.status === 'retired' ? 'retired' : 'active',
});

function EntityForm({
  node,
  onClose,
  onSaved,
}: {
  node: GraphNode;
  onClose: () => void;
  onSaved: (entity: Entity) => void;
}) {
  const [subject, setSubject] = useState(node.subject);
  const [type, setType] = useState<EntityType>(node.entity_type);
  const [description, setDescription] = useState(node.description);
  const [aliases, setAliases] = useState(node.aliases);

  async function save() {
    // Only what changed: a rename keeps the old name as another name unless
    // the other names were changed too.
    const changes: Curation = {};
    if (subject.trim() !== node.subject) changes.subject = subject.trim();
    if (type !== node.entity_type) changes.entity_type = type;
    if (description.trim() !== node.description)
      changes.description = description.trim();
    if (!same(aliases, node.aliases)) changes.aliases = aliases;
    onSaved(
      await request<Entity>(`/api/brain/entities/${node.id}`, {
        method: 'PATCH',
        body: changes,
      }),
    );
  }

  return (
    <FormDialog
      title={`Edit ${node.subject}`}
      description="Correct what Row-Bot found. Renaming keeps the old name as another name, so Row-Bot still recognises it."
      submitLabel="Save"
      onClose={onClose}
      onSubmit={save}
    >
      <Field label="Name">
        <Input
          required
          maxLength={160}
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
          data-initial-focus
        />
      </Field>
      <Field label="Type">
        <Select
          value={type}
          onChange={(event) => setType(event.target.value as EntityType)}
        >
          {Object.entries(ENTITY_TYPES).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Description" hint="Up to 1,000 characters.">
        <textarea
          className="input console-textarea"
          maxLength={1000}
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </Field>
      <Field label="Other names" hint="Separated by commas, up to 16.">
        <ListInput label="Other names" value={aliases} onChange={setAliases} />
      </Field>
    </FormDialog>
  );
}

/** Pick the entry to merge into: another active entry of the same space. */
function MergeForm({
  node,
  onClose,
  onMerged,
}: {
  node: GraphNode;
  onClose: () => void;
  onMerged: (target: Entity) => void;
}) {
  const candidates = useLoad(async () => {
    const graph = await request<Graph>(
      `/api/brain/graph${query({ spaces: node.space_id, limit: 1000 })}`,
    );
    return graph.nodes
      .filter((item) => item.id !== node.id)
      .sort((one, other) => one.subject.localeCompare(other.subject));
  });
  const [into, setInto] = useState('');

  async function merge() {
    onMerged(
      await request<Entity>(`/api/brain/entities/${node.id}/merge`, {
        method: 'POST',
        body: { into },
      }),
    );
  }

  return (
    <FormDialog
      title={`Merge ${node.subject} into another entry`}
      description={`Its connections, mentions, name and other names move to the entry you choose, and ${node.subject} leaves the list. A merge can’t be undone.`}
      submitLabel="Merge"
      onClose={onClose}
      onSubmit={merge}
    >
      <Loaded load={candidates} label="Loading entries" what="the entries">
        {(list) => (
          <Field label="Into">
            <Select
              required
              value={into}
              onChange={(event) => setInto(event.target.value)}
            >
              <option value="" disabled>
                {list.length ? 'Choose an entry' : 'No other entries here'}
              </option>
              {list.map((item) => (
                <option key={item.id} value={item.id}>
                  {`${item.subject} (${ENTITY_TYPES[item.entity_type]})`}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </Loaded>
    </FormDialog>
  );
}

/** An entry's description, connections and where it is mentioned. */
function EntityDetails({ id }: { id: string }) {
  const detail = useLoad(() =>
    request<EntityDetail>(`/api/brain/entities/${id}`),
  );
  return (
    <Loaded load={detail} label="Loading the entry" what="this entry">
      {({ entity, relations, mentions, mention_count }) => (
        <div className="console-entity">
          {entity.description && <p>{entity.description}</p>}
          {relations.length > 0 && (
            <section aria-label="Connections">
              <h3>Connections</h3>
              <ul className="console-adds">
                {relations.map((relation) => (
                  <li key={relation.id}>
                    {relationSentence(
                      entity.subject,
                      relation,
                      relation.peer.subject,
                    )}
                    {relation.status === 'retired' && ' (retired)'}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section aria-label="Mentioned in">
            <h3>Mentioned in</h3>
            {mentions.length === 0 ? (
              <p className="console-footnote">Nothing mentions it any more.</p>
            ) : (
              <ul className="console-citations">
                {mentions.map((mention, index) => (
                  <li key={index}>
                    <strong>
                      {mention.type === 'document'
                        ? mention.document.title || mention.document.file_name
                        : mention.document
                          ? `A fact from ${mention.document.title}`
                          : 'A fact'}
                    </strong>
                    <blockquote>
                      {mention.type === 'document'
                        ? mention.excerpt
                        : mention.fact.text}
                    </blockquote>
                  </li>
                ))}
              </ul>
            )}
            {mention_count > mentions.length && (
              <p className="console-footnote">
                And {count(mention_count - mentions.length)} more.
              </p>
            )}
          </section>
        </div>
      )}
    </Loaded>
  );
}

/**
 * The space's part of the company knowledge graph as a list: search, an
 * entry's details with its mentions as citations, and curation (edit,
 * merge, retire and restore). It follows who reads the space, admins
 * included, so an admin who doesn't read it is offered to own it.
 */
export function Entities({
  space,
  onSpaceChange,
}: {
  space: Space;
  onSpaceChange: (space: Space) => void;
}) {
  const { me } = useDirectory();
  const notify = useNotify();
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);
  const filters = query({
    spaces: space.id,
    q: q || undefined,
    retired: 'true',
  });
  const path = `/api/brain/graph${filters}`;
  const graph = useLoad(
    () => request<Graph>(path),
    `${path}|${space.owner_principals.join()}`,
  );
  const [open, setOpen] = useState<string | null>(null);
  const [editing, setEditing] = useState<GraphNode | null>(null);
  const [merging, setMerging] = useState<GraphNode | null>(null);
  const [joining, setJoining] = useState(false);

  const replace = (entity: Entity) =>
    graph.update((data) => ({
      ...data,
      nodes: data.nodes.map((node) =>
        node.id === entity.id ? asNode(entity, node) : node,
      ),
    }));

  function setStatus(node: GraphNode, change: 'retire' | 'restore') {
    request<Entity>(`/api/brain/entities/${node.id}/${change}`, {
      method: 'POST',
    }).then(
      (entity) => {
        replace(entity);
        notify(
          `${node.subject} is ${change === 'retire' ? 'retired' : 'restored'}`,
        );
      },
      (error: unknown) => notify(problem(error), 'danger'),
    );
  }

  async function own() {
    setJoining(true);
    try {
      onSpaceChange(
        await request<Space>(`/api/admin/brain/spaces/${space.id}`, {
          method: 'PATCH',
          body: { owner_principals: [...space.owner_principals, me.id] },
        }),
      );
    } catch (error) {
      notify(problem(error), 'danger');
    } finally {
      setJoining(false);
    }
  }

  if (
    graph.status === 'error' &&
    graph.error instanceof ApiError &&
    graph.error.code === 'space_not_found'
  )
    return (
      <EmptyState
        title="Only its readers see its knowledge"
        action={
          <Button disabled={joining} onClick={() => void own()}>
            <UserCheck size={16} aria-hidden />
            Make me an owner
          </Button>
        }
      >
        The company knowledge graph follows who reads a space, admins included.
        Owners read their space: make yourself an owner of {space.name} to see
        and tidy its knowledge.
      </EmptyState>
    );

  return (
    <section className="console-section" aria-label="Knowledge graph">
      <div className="console-filters">
        <label className="console-search">
          <Search size={16} aria-hidden />
          <Input
            type="search"
            aria-label="Search knowledge"
            placeholder="A name or another name"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>
      <Loaded load={graph} label="Loading knowledge" what="the knowledge">
        {({ nodes, truncated, shown_entities, total_entities }) =>
          nodes.length === 0 ? (
            q ? (
              <InlineEmpty icon={<Network size={16} />}>
                Nothing matches “{q}”.
              </InlineEmpty>
            ) : (
              <EmptyState title="No knowledge yet">
                Row-Bot finds the people, teams, projects and other things in
                this space’s documents and facts, and lists them here for you to
                tidy.
              </EmptyState>
            )
          ) : (
            <>
              <EntityList label="Company knowledge">
                {nodes.map((node) => (
                  <EntityRow
                    key={node.id}
                    title={node.subject}
                    icon={<Network size={18} />}
                    status={
                      node.status === 'retired'
                        ? { tone: 'neutral', label: 'Retired' }
                        : undefined
                    }
                    meta={[
                      ENTITY_TYPES[node.entity_type],
                      plural(node.relation_count, 'connection', 'connections'),
                      node.aliases.length
                        ? `Also called ${node.aliases.join(', ')}`
                        : '',
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                    expanded={open === node.id}
                    onExpandedChange={(next) => setOpen(next ? node.id : null)}
                    details={
                      open === node.id ? <EntityDetails id={node.id} /> : <></>
                    }
                    action={
                      <>
                        <IconButton
                          size="sm"
                          label={`Edit ${node.subject}`}
                          onClick={() => setEditing(node)}
                        >
                          <Pencil size={16} aria-hidden />
                        </IconButton>
                        {node.status === 'active' ? (
                          <>
                            <IconButton
                              size="sm"
                              label={`Merge ${node.subject} into another entry`}
                              onClick={() => setMerging(node)}
                            >
                              <GitMerge size={16} aria-hidden />
                            </IconButton>
                            <IconButton
                              size="sm"
                              label={`Retire ${node.subject}`}
                              onClick={() => setStatus(node, 'retire')}
                            >
                              <Archive size={16} aria-hidden />
                            </IconButton>
                          </>
                        ) : (
                          <IconButton
                            size="sm"
                            label={`Restore ${node.subject}`}
                            onClick={() => setStatus(node, 'restore')}
                          >
                            <ArchiveRestore size={16} aria-hidden />
                          </IconButton>
                        )}
                      </>
                    }
                  />
                ))}
              </EntityList>
              {truncated && (
                <p className="console-footnote">
                  {`Showing the ${count(shown_entities)} most connected of ${count(total_entities)}. Search to find the rest.`}
                </p>
              )}
            </>
          )
        }
      </Loaded>
      {editing && (
        <EntityForm
          node={editing}
          onClose={() => setEditing(null)}
          onSaved={(entity) => {
            replace(entity);
            notify(`${entity.subject} is saved`);
          }}
        />
      )}
      {merging && (
        <MergeForm
          node={merging}
          onClose={() => setMerging(null)}
          onMerged={(target) => {
            graph.update((data) => ({
              ...data,
              nodes: data.nodes
                .filter((node) => node.id !== merging.id)
                .map((node) =>
                  node.id === target.id ? asNode(target, node) : node,
                ),
            }));
            notify(`${merging.subject} is merged into ${target.subject}`);
          }}
        />
      )}
    </section>
  );
}
