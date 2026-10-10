/**
 * Plain words for saved document states. The server sends codes (never a
 * failure's raw message, which can hold local paths); people read these.
 */

const FAILURES: Record<string, string> = {
  upload_rejected:
    "The file couldn't be added. It may be empty, larger than 256 MB, or the disk may be nearly full.",
  upload_failed: "The upload didn't finish. Add the file again.",
  missing_staged_source:
    "Row-Bot's saved copy of this file is missing. Add the file again.",
  parse_failed:
    "Row-Bot couldn't read this file. It may be damaged, password-protected or in a format Row-Bot can't read.",
  embed_failed:
    "Row-Bot couldn't prepare this file for search. Check the search model, then try again.",
  index_commit_failed: "Row-Bot couldn't save this file to search. Try again.",
  compatibility_index_failed:
    "Row-Bot couldn't save this file to search. Try again.",
  knowledge_map_failed:
    "The file is searchable, but Row-Bot couldn't pull knowledge from it. Check the model, then try again.",
  knowledge_reduce_failed:
    "The file is searchable, but Row-Bot couldn't pull knowledge from it. Check the model, then try again.",
  knowledge_commit_failed:
    "The file is searchable, but Row-Bot couldn't save the knowledge it found. Try again.",
  finalize_failed: "Row-Bot couldn't finish adding this file. Try again.",
  finalization_incomplete:
    "Row-Bot couldn't finish adding this file. Try again.",
  cancelled: 'Cancelled before it finished.',
};

/** Why a document stopped, in words; `null` when it didn't. */
export function documentFailure(
  status: string,
  code: string | null | undefined,
): string | null {
  if (status === 'cancelled')
    return code && code !== 'cancelled' && FAILURES[code]
      ? FAILURES[code]
      : FAILURES.cancelled;
  if (status === 'skipped_duplicate')
    return 'Skipped: the same file is already in your documents.';
  if (status !== 'failed') return null;
  return (
    (code && FAILURES[code]) ??
    'Something went wrong while adding this file. Try again, or add it again.'
  );
}

const JOB_STATUS: Record<string, string> = {
  staging: 'Uploading',
  queued: 'Waiting',
  indexing: 'Reading',
  searchable: 'Searchable',
  extracting: 'Finding knowledge',
  completed: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
  skipped_duplicate: 'Skipped (duplicate)',
};

/** One document's state in the queue, in words. */
export function jobStatus(status: string) {
  return JOB_STATUS[status] ?? 'Unknown';
}

/** A batch reads as its first document, and how many more it holds. */
export function batchTitle(item: {
  id: string;
  name: string;
  document_count?: number | null;
}) {
  const count = item.document_count ?? null;
  if (!item.name) return item.id.startsWith('client_') ? 'Upload' : 'Documents';
  return count && count > 1 ? `${item.name} and ${count - 1} more` : item.name;
}
