/**
 * The company brain's routes for FakeApi: spaces, documents (whose statuses
 * move on each time the console reads them again), facts and the knowledge
 * graph's entries, with made-up data. The signed-in admin reads the company
 * and Engineering spaces but not Sales, so its knowledge graph is refused as
 * the server refuses it.
 */
import type { FakeAnswer } from './fake-api';

export type Upload = { file: { name: string; size: number } };

export const COMPANY_SPACE = 'a0000000-0000-4000-8000-000000000001';
export const ENGINEERING_SPACE = 'a0000000-0000-4000-8000-000000000002';
export const SALES_SPACE = 'a0000000-0000-4000-8000-000000000003';
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

const ME = '11111111-1111-4111-8111-111111111111';
const ADA = '22222222-2222-4222-8222-222222222222';
const LIN = '44444444-4444-4444-8444-444444444444';
const MY_GROUPS = ['it-admins'];
const SUPPORTED = ['.docx', '.markdown', '.md', '.txt'];
const NOW = Date.now();
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

type Space = {
  id: string;
  name: string;
  kind: 'company' | 'group';
  reader_groups: string[];
  owner_groups: string[];
  owner_principals: string[];
  created_by: string | null;
  created_at: string;
  updated_at: string;
};
type Document = {
  id: string;
  space_id: string;
  title: string;
  file_name: string;
  media_type: string;
  byte_count: number;
  sha256: string;
  status: string;
  status_reason: string | null;
  version: number;
  uploaded_by: string;
  uploaded_at: string;
  processed_at: string | null;
  chunk_count: number;
  graph_status: string;
  graph_reason: string | null;
};
type Fact = {
  id: string;
  space_id: string;
  text: string;
  source_document_id: string | null;
  written_by: string;
  created_at: string;
  updated_at: string;
  retired_at: string | null;
};
type Entry = {
  id: string;
  space_id: string;
  subject: string;
  entity_type: string;
  description: string;
  aliases: string[];
  status: 'active' | 'retired' | 'merged_into';
  merged_into: string | null;
  edited_by: string | null;
  edited_at: string | null;
  created_at: string;
  updated_at: string;
  relation_count: number;
};

function space(id: string, name: string, fields: Partial<Space> = {}): Space {
  return {
    id,
    name,
    kind: 'group',
    reader_groups: [],
    owner_groups: [],
    owner_principals: [],
    created_by: ME,
    created_at: ago(60 * 24 * 20),
    updated_at: ago(60 * 24 * 2),
    ...fields,
  };
}

function document(
  id: string,
  spaceId: string,
  title: string,
  file: string,
  fields: Partial<Document> = {},
): Document {
  return {
    id,
    space_id: spaceId,
    title,
    file_name: file,
    media_type: file.endsWith('.docx')
      ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
      : 'text/plain',
    byte_count: 48_213,
    sha256: 'd'.repeat(64),
    status: 'ready',
    status_reason: null,
    version: 1,
    uploaded_by: ADA,
    uploaded_at: ago(60 * 24 * 3),
    processed_at: ago(60 * 24 * 3 - 1),
    chunk_count: 14,
    graph_status: 'done',
    graph_reason: null,
    ...fields,
  };
}

export const HANDBOOK = 'b0000000-0000-4000-8000-000000000001';
export const EXPENSES = 'b0000000-0000-4000-8000-000000000002';
export const RELEASE = 'b0000000-0000-4000-8000-000000000005';

function documents(): Document[] {
  return [
    document(HANDBOOK, COMPANY_SPACE, 'Employee handbook', 'handbook.docx'),
    document(EXPENSES, COMPANY_SPACE, 'Expenses policy', 'expenses.md', {
      graph_reason:
        'Only the start of this document is in the knowledge graph (its first 40 of 52 parts).',
    }),
    document(
      'b0000000-0000-4000-8000-000000000003',
      COMPANY_SPACE,
      'Office guide',
      'office.txt',
      {
        graph_status: 'waiting',
        graph_reason: 'Your monthly model budget is used up.',
      },
    ),
    document(
      'b0000000-0000-4000-8000-000000000004',
      COMPANY_SPACE,
      'Signed contract scan',
      'contract.docx',
      {
        status: 'failed',
        status_reason: 'This Word document can’t be read.',
        processed_at: null,
        chunk_count: 0,
        graph_status: 'pending',
      },
    ),
    document(RELEASE, ENGINEERING_SPACE, 'Release checklist', 'release.md', {
      status: 'processing',
      processed_at: null,
      chunk_count: 0,
      graph_status: 'pending',
      uploaded_at: ago(1),
    }),
    document(
      'b0000000-0000-4000-8000-000000000006',
      SALES_SPACE,
      'Price list',
      'prices.md',
      { uploaded_by: LIN },
    ),
  ];
}

/** What a document becomes each time it is read again. */
const READING: Partial<Document>[] = [
  {
    status: 'ready',
    graph_status: 'extracting',
    processed_at: ago(0),
    chunk_count: 3,
  },
  { graph_status: 'done' },
];

function facts(): Fact[] {
  const fact = (
    id: string,
    spaceId: string,
    text: string,
    fields: Partial<Fact> = {},
  ): Fact => ({
    id,
    space_id: spaceId,
    text,
    source_document_id: null,
    written_by: ME,
    created_at: ago(60 * 24),
    updated_at: ago(60 * 24),
    retired_at: null,
    ...fields,
  });
  return [
    fact(
      'f0000000-0000-4000-8000-000000000001',
      COMPANY_SPACE,
      'Expense claims are due by the 5th of each month.',
      { source_document_id: EXPENSES },
    ),
    fact(
      'f0000000-0000-4000-8000-000000000002',
      COMPANY_SPACE,
      'The office is closed on public holidays.',
      { written_by: ADA },
    ),
    fact(
      'f0000000-0000-4000-8000-000000000003',
      COMPANY_SPACE,
      'Parking is free on Fridays.',
      { retired_at: ago(60 * 5) },
    ),
    fact(
      'f0000000-0000-4000-8000-000000000004',
      ENGINEERING_SPACE,
      'Releases go out on Tuesdays.',
    ),
  ];
}

export const ATLAS = 'e0000000-0000-4000-8000-000000000001';
export const PLATFORM = 'e0000000-0000-4000-8000-000000000002';
export const ATLAS_APP = 'e0000000-0000-4000-8000-000000000004';
const POLICY = 'e0000000-0000-4000-8000-000000000003';

function entries(): Entry[] {
  const entry = (
    id: string,
    subject: string,
    type: string,
    fields: Partial<Entry> = {},
  ): Entry => ({
    id,
    space_id: COMPANY_SPACE,
    subject,
    entity_type: type,
    description: '',
    aliases: [],
    status: 'active',
    merged_into: null,
    edited_by: null,
    edited_at: null,
    created_at: ago(60 * 24 * 3),
    updated_at: ago(60 * 24 * 3),
    relation_count: 0,
    ...fields,
  });
  return [
    entry(ATLAS, 'Atlas', 'project', {
      description: 'The new expenses app.',
      relation_count: 2,
    }),
    entry(PLATFORM, 'Platform team', 'team', { relation_count: 1 }),
    entry(POLICY, 'Expenses policy', 'policy', { relation_count: 1 }),
    entry(ATLAS_APP, 'Atlas app', 'product'),
    entry('e0000000-0000-4000-8000-000000000005', 'Hot desk', 'term', {
      status: 'retired',
    }),
    entry('e0000000-0000-4000-8000-000000000006', 'Release train', 'term', {
      space_id: ENGINEERING_SPACE,
    }),
  ];
}

const nameKey = (value: string) => value.trim().toLowerCase();

export class FakeBrain {
  private spaces: Space[];
  private documents: Document[];
  private facts: Fact[];
  private entries: Entry[];
  /** Documents the console has seen, and what each becomes when read again. */
  private reading = new Map<string, Partial<Document>[]>([
    [RELEASE, [...READING]],
  ]);
  private seen = new Set<string>();
  private made = 0;

  constructor(empty: boolean) {
    const company = space(COMPANY_SPACE, 'Company', {
      kind: 'company',
      owner_groups: ['it-admins'],
      created_by: null,
    });
    this.spaces = empty
      ? [company]
      : [
          company,
          space(ENGINEERING_SPACE, 'Engineering', {
            reader_groups: ['Engineering'],
            owner_groups: ['it-admins'],
            owner_principals: [ADA],
          }),
          space(SALES_SPACE, 'Sales team', {
            reader_groups: ['Sales'],
            owner_principals: [LIN],
          }),
        ];
    this.documents = empty ? [] : documents();
    this.facts = empty ? [] : facts();
    this.entries = empty ? [] : entries();
  }

  private id(prefix: string) {
    this.made += 1;
    return `${prefix}${String(this.made).padStart(7, '0')}-0000-4000-8000-000000000000`;
  }

  /** The admin reads the company space, and spaces they read or own. */
  private reads(found: Space) {
    return (
      found.kind === 'company' ||
      found.owner_principals.includes(ME) ||
      [...found.reader_groups, ...found.owner_groups].some((group) =>
        MY_GROUPS.includes(group),
      )
    );
  }

  private listDocuments(spaceId: string) {
    const list = this.documents.filter((item) => item.space_id === spaceId);
    for (const item of list) {
      const steps = this.reading.get(item.id);
      if (steps?.length && this.seen.has(item.id))
        Object.assign(item, steps.shift());
      this.seen.add(item.id);
    }
    return { status: 200, body: { documents: list } };
  }

  private upload(spaceId: string, body: Upload, current?: Document) {
    const { name, size } = body.file;
    if (!SUPPORTED.some((extension) => name.toLowerCase().endsWith(extension)))
      return {
        status: 415,
        body: { error: 'unsupported_type', supported: SUPPORTED },
      };
    if (size > MAX_UPLOAD_BYTES)
      return {
        status: 413,
        body: { error: 'file_too_large', max_bytes: MAX_UPLOAD_BYTES },
      };
    const fields = {
      file_name: name,
      byte_count: size,
      status: 'queued',
      status_reason: null,
      processed_at: null,
      chunk_count: 0,
      graph_status: 'pending',
      graph_reason: null,
      uploaded_by: ME,
      uploaded_at: new Date().toISOString(),
    };
    if (current) {
      Object.assign(current, fields, { version: current.version + 1 });
      this.reading.set(current.id, [...READING]);
      return { status: 200, body: current };
    }
    const added = document(
      this.id('b'),
      spaceId,
      name.replace(/\.[^.]+$/, ''),
      name,
      fields,
    );
    this.documents.unshift(added);
    this.reading.set(added.id, [...READING]);
    this.seen.add(added.id);
    return { status: 201, body: added };
  }

  private graph(url: URL): FakeAnswer {
    const ids = (url.searchParams.get('spaces') ?? '').split(',');
    const shown = this.spaces.filter((item) => ids.includes(item.id));
    const refused =
      shown.length !== ids.length || shown.some((item) => !this.reads(item));
    if (refused) return { status: 404, body: { error: 'space_not_found' } };
    const q = nameKey(url.searchParams.get('q') ?? '');
    const retired = url.searchParams.get('retired') === 'true';
    const listed = (item: Entry) =>
      item.status === 'active' || (retired && item.status === 'retired');
    const named = (item: Entry) =>
      [item.subject, ...item.aliases].some((name) => nameKey(name).includes(q));
    const keep = (item: Entry) =>
      ids.includes(item.space_id) && listed(item) && (!q || named(item));
    const order = (one: Entry, other: Entry) =>
      other.relation_count - one.relation_count ||
      one.subject.localeCompare(other.subject);
    const nodes = this.entries
      .filter(keep)
      .sort(order)
      .map((item) => ({ ...item, orphan: false }));
    return {
      status: 200,
      body: {
        nodes,
        edges: [],
        spaces: shown.map(({ id, name, kind }) => ({ id, name, kind })),
        total_entities: nodes.length,
        total_relations: 0,
        shown_entities: nodes.length,
        shown_relations: 0,
        truncated: false,
        entity_types: [...new Set(nodes.map((item) => item.entity_type))],
      },
    };
  }

  private detail(entry: Entry): FakeAnswer {
    const home = this.spaces.find((item) => item.id === entry.space_id);
    const handbook = {
      id: HANDBOOK,
      title: 'Employee handbook',
      file_name: 'handbook.docx',
    };
    const peer = (id: string) => {
      const found = this.entries.find((item) => item.id === id);
      return found
        ? {
            id,
            subject: found.subject,
            entity_type: found.entity_type,
            status: found.status,
          }
        : null;
    };
    const link = (id: string, kind: string, way: string, of: string) => ({
      id,
      relation_type: kind,
      direction: way,
      peer: peer(of),
      status: 'active',
      origin: 'extracted',
      updated_at: ago(60),
    });
    const relations =
      entry.id === ATLAS
        ? [
            link('r1', 'works_on', 'in', PLATFORM),
            link('r2', 'applies_to', 'in', POLICY),
          ]
        : entry.id === PLATFORM
          ? [link('r1', 'works_on', 'out', ATLAS)]
          : [];
    return {
      status: 200,
      body: {
        entity: entry,
        space: home && { id: home.id, name: home.name, kind: home.kind },
        relations,
        mentions: [
          {
            type: 'document',
            space: { id: entry.space_id, name: home?.name },
            document: handbook,
            chunk: 3,
            excerpt: `… the Platform team builds ${entry.subject}, so ask them first …`,
          },
          ...(entry.id === ATLAS
            ? [
                {
                  type: 'fact',
                  space: { id: entry.space_id, name: home?.name },
                  fact: {
                    id: 'f0000000-0000-4000-8000-000000000001',
                    text: 'Expense claims are due by the 5th of each month.',
                  },
                  document: {
                    id: EXPENSES,
                    title: 'Expenses policy',
                    file_name: 'expenses.md',
                  },
                },
              ]
            : []),
        ],
        mention_count: entry.id === ATLAS ? 2 : 1,
      },
    };
  }

  private curate(entry: Entry, body: Record<string, unknown>): FakeAnswer {
    if (entry.status === 'merged_into')
      return {
        status: 409,
        body: { error: 'entity_merged', merged_into: entry.merged_into },
      };
    const subject = (body.subject as string | undefined) ?? entry.subject;
    const type = (body.entity_type as string | undefined) ?? entry.entity_type;
    const taken = this.entries.find(
      (item) =>
        item.id !== entry.id &&
        item.space_id === entry.space_id &&
        item.status !== 'merged_into' &&
        nameKey(item.subject) === nameKey(subject) &&
        item.entity_type === type,
    );
    if (taken)
      return {
        status: 409,
        body: { error: 'entity_exists', entity_id: taken.id },
      };
    const aliases =
      (body.aliases as string[] | undefined) ??
      (subject !== entry.subject
        ? [...entry.aliases, entry.subject]
        : entry.aliases);
    Object.assign(entry, {
      subject,
      entity_type: type,
      description:
        (body.description as string | undefined) ?? entry.description,
      aliases,
      edited_by: ME,
      edited_at: new Date().toISOString(),
    });
    return { status: 200, body: entry };
  }

  handle(method: string, url: URL, body: unknown): FakeAnswer | undefined {
    const path = url.pathname;
    if (path === '/api/admin/brain/spaces' && method === 'GET')
      return {
        status: 200,
        body: { spaces: this.spaces, max_upload_bytes: MAX_UPLOAD_BYTES },
      };
    if (path === '/api/admin/brain/spaces' && method === 'POST') {
      const added = space(this.id('a'), '', body as Partial<Space>);
      this.spaces.push(added);
      return { status: 201, body: added };
    }
    const admin = path.match(/^\/api\/admin\/brain\/spaces\/([^/]+)$/);
    if (admin) {
      const found = this.spaces.find((item) => item.id === admin[1]);
      if (!found) return { status: 404, body: { error: 'space_not_found' } };
      if (method === 'DELETE') {
        if (found.kind === 'company')
          return { status: 409, body: { error: 'company_space_stays' } };
        this.spaces = this.spaces.filter((item) => item !== found);
        return { status: 204 };
      }
      Object.assign(found, body, { updated_at: new Date().toISOString() });
      return { status: 200, body: found };
    }
    const inSpace = path.match(
      /^\/api\/brain\/spaces\/([^/]+)\/(documents|facts)$/,
    );
    if (inSpace) {
      const [, spaceId, what] = inSpace;
      if (!this.spaces.some((item) => item.id === spaceId))
        return { status: 404, body: { error: 'space_not_found' } };
      if (what === 'documents')
        return method === 'POST'
          ? this.upload(spaceId, body as Upload)
          : this.listDocuments(spaceId);
      if (method === 'POST') {
        const { text, source_document_id: source = null } = body as {
          text: string;
          source_document_id?: string | null;
        };
        const added: Fact = {
          id: this.id('f'),
          space_id: spaceId,
          text,
          source_document_id: source,
          written_by: ME,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          retired_at: null,
        };
        this.facts.unshift(added);
        return { status: 201, body: added };
      }
      const retired = url.searchParams.get('retired') === 'true';
      return {
        status: 200,
        body: {
          facts: this.facts.filter(
            (item) =>
              item.space_id === spaceId && (retired || !item.retired_at),
          ),
        },
      };
    }
    const one = path.match(/^\/api\/brain\/documents\/([^/]+)$/);
    if (one) {
      const found = this.documents.find((item) => item.id === one[1]);
      if (!found) return { status: 404, body: { error: 'document_not_found' } };
      if (method === 'DELETE') {
        this.documents = this.documents.filter((item) => item !== found);
        return { status: 204 };
      }
      return this.upload(found.space_id, body as Upload, found);
    }
    const fact = path.match(/^\/api\/brain\/facts\/([^/]+?)(\/retire)?$/);
    if (fact) {
      const found = this.facts.find((item) => item.id === fact[1]);
      if (!found) return { status: 404, body: { error: 'fact_not_found' } };
      if (fact[2]) found.retired_at ??= new Date().toISOString();
      else Object.assign(found, body, { written_by: ME });
      found.updated_at = new Date().toISOString();
      return { status: 200, body: found };
    }
    if (path === '/api/brain/graph') return this.graph(url);
    const entity = path.match(
      /^\/api\/brain\/entities\/([^/]+?)(?:\/(merge|retire|restore))?$/,
    );
    if (entity) {
      const found = this.entries.find((item) => item.id === entity[1]);
      const home = this.spaces.find((item) => item.id === found?.space_id);
      if (!found || !home || (method === 'GET' && !this.reads(home)))
        return { status: 404, body: { error: 'entity_not_found' } };
      if (method === 'GET') return this.detail(found);
      if (method === 'PATCH')
        return this.curate(found, body as Record<string, unknown>);
      if (entity[2] === 'merge') {
        const into = this.entries.find(
          (item) =>
            item.id === (body as { into: string }).into &&
            item.id !== found.id &&
            item.space_id === found.space_id &&
            item.status === 'active',
        );
        if (!into) return { status: 422, body: { error: 'unknown_entity' } };
        into.aliases = [
          ...new Set([...into.aliases, found.subject, ...found.aliases]),
        ];
        into.relation_count += found.relation_count;
        Object.assign(found, { status: 'merged_into', merged_into: into.id });
        return { status: 200, body: into };
      }
      found.status = entity[2] === 'retire' ? 'retired' : 'active';
      return { status: 200, body: found };
    }
    return undefined;
  }
}
