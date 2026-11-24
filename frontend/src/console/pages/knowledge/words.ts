import type { Tone } from '../../../ui/primitives';
import type { BrainDocument, EntityType, Space } from '../../types';
import { count } from '../../words';

/** What the server reads (its 415 lists the same), for the file picker too. */
export const ACCEPTED = ['.txt', '.md', '.markdown', '.docx'];
export const ACCEPTED_WORDS = 'Text, Markdown or Word files (.txt, .md, .docx)';

/** The two reasons that send an admin to Models: shown as a link there. */
export const ADD_EMBEDDINGS = 'Add an embeddings model in Models';
export const ADD_CHAT = 'Add a chat model in Models';

/** How often a space's documents are read again while any is being worked on. */
export const DOCUMENTS_POLL_MS = 5000;

export const megabytes = (bytes: number) =>
  `${count(Math.round(bytes / (1024 * 1024)))} MB`;

export function size(bytes: number) {
  if (bytes < 1024) return `${count(bytes)} bytes`;
  if (bytes < 1024 * 1024) return `${count(Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const plural = (value: number, one: string, many: string) =>
  `${count(value)} ${value === 1 ? one : many}`;

/** The same names in the same order. */
export const same = (one: string[], other: string[]) =>
  one.length === other.length && one.every((item, at) => item === other[at]);

export function accepted(name: string) {
  const lower = name.toLowerCase();
  return ACCEPTED.some((extension) => lower.endsWith(extension));
}

export type DocumentStatus = {
  label: string;
  tone: Tone;
  /** Still being read or searched for knowledge: the list reads itself again. */
  working: boolean;
  /** Why it stopped or what it waits for. */
  reason: string | null;
  /** The knowledge graph's own note on a searchable document. */
  graph: { lead: string; reason: string } | null;
};

/** A document's status in words, from its two statuses and their reasons. */
export function documentStatus(document: BrainDocument): DocumentStatus {
  const plain = { reason: null, graph: null };
  if (document.status === 'failed')
    return {
      ...plain,
      label: 'Failed',
      tone: 'danger',
      working: false,
      reason: document.status_reason ?? 'This file couldn’t be processed.',
    };
  if (document.status === 'queued' && document.status_reason)
    return {
      ...plain,
      label: 'Waiting',
      tone: 'warning',
      working: false,
      reason: document.status_reason,
    };
  if (document.status === 'queued')
    return { ...plain, label: 'Queued', tone: 'info', working: true };
  if (document.status === 'processing')
    return { ...plain, label: 'Reading', tone: 'info', working: true };
  if (
    document.graph_status === 'pending' ||
    document.graph_status === 'extracting'
  )
    return {
      ...plain,
      label: 'Finding knowledge',
      tone: 'info',
      working: true,
    };
  const graph =
    document.graph_status === 'waiting'
      ? {
          lead: 'Its knowledge waits.',
          reason: document.graph_reason ?? ADD_CHAT,
        }
      : document.graph_status === 'failed'
        ? {
            lead: 'Its knowledge couldn’t be found.',
            reason:
              document.graph_reason ??
              'This document’s knowledge couldn’t be read.',
          }
        : document.graph_reason
          ? { lead: '', reason: document.graph_reason }
          : null;
  return { ...plain, label: 'Ready', tone: 'success', working: false, graph };
}

export const ENTITY_TYPES: Record<EntityType, string> = {
  person: 'Person',
  team: 'Team',
  organisation: 'Organisation',
  customer: 'Customer',
  project: 'Project',
  product: 'Product',
  system: 'System',
  policy: 'Policy',
  term: 'Term',
  place: 'Place',
};

const RELATIONS: Record<string, string> = {
  member_of: 'is a member of',
  part_of: 'is part of',
  leads: 'leads',
  manages: 'manages',
  reports_to: 'reports to',
  works_on: 'works on',
  owns: 'owns',
  uses: 'uses',
  depends_on: 'depends on',
  located_in: 'is in',
  customer_of: 'is a customer of',
  supplies: 'supplies',
  applies_to: 'applies to',
  replaces: 'replaces',
  related_to: 'is related to',
};

/** "Platform team works on Atlas", whichever end this entry is. */
export function relationSentence(
  subject: string,
  relation: { relation_type: string; direction: 'out' | 'in' },
  peer: string,
) {
  const verb = RELATIONS[relation.relation_type] ?? 'is related to';
  return relation.direction === 'out'
    ? `${subject} ${verb} ${peer}`
    : `${peer} ${verb} ${subject}`;
}

/** Who reads and owns a space, as one line for its row. */
export function spaceSummary(
  space: Space,
  nameOf: (id: string) => string,
): string {
  const readers =
    space.kind === 'company'
      ? 'Read by everyone signed in'
      : space.reader_groups.length
        ? `Read by ${space.reader_groups.join(', ')}`
        : 'Read by its owners only';
  const owners = [...space.owner_groups, ...space.owner_principals.map(nameOf)];
  return [
    space.kind === 'company' ? 'Company space' : 'Group space',
    readers,
    owners.length
      ? `Owned by ${owners.join(', ')}`
      : 'No owners: admins manage it',
  ].join(' · ');
}
