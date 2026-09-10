"""Generate v1 schemas and TypeScript from Python DTOs, without dependencies.

Run with the already locked Python environment. --check never writes output.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from row_bot.api.v1 import schemas

MODELS = {name: getattr(schemas, name) for name in (
    "Command", "Event", "Handshake", "Problem", "Outcome", "ResourceBinding",
    "PreviewContract", "CommandReceipt", "AttachmentView", "SessionProof",
    "ConversationView", "ConversationPage", "Snapshot", "TranscriptPage", "SubscriptionView",
    "EventPage", "Choices", "HandshakeView", "ApprovalView", "ResourceView", "Acknowledgement",
    "Acknowledged", "Unsubscribed", "UploadRequest", "UploadView", "UploadCompletion", "UploadCancelled",
    "StreamReset", "LazyContent", "SearchPage", "ConversationWorkspace", "ResourceChoicePage",
    "DelegatedRun", "DelegatedActivityView",
    "ContextUsageView", "ConversationOpenView",
    "FolderGrantView", "DeckSetupOptions", "ArtifactPreview", "WorkspaceInspector", "WorkspaceChanges",
    "WorkspaceDirectory", "WorkspaceFile", "WorkspaceDiff", "WorkspaceChangeSetPage", "WorkspaceChangeSetFiles", "DraftView", "DraftSave", "ParentSteeringView", "ClientQueueView")}

# Method, path, request DTO (binary uses bytes), response DTO. This table also
# drives OpenAPI and is checked against the actual router in the contract tests.
OPERATIONS = (
    ("post", "/handshake", "Handshake", "HandshakeView"),
    ("get", "/conversations", None, "ConversationPage"),
    ("get", "/conversations/{conversation_id}", None, "ConversationView"),
    ("get", "/conversations/{conversation_id}/transcript", None, "TranscriptPage"),
    ("get", "/conversations/{conversation_id}/content/{message_id}", None, "LazyContent"),
    ("get", "/conversations/{conversation_id}/text/{message_id}", None, "LazyContent"),
    ("post", "/conversations/commands", "Command", "CommandReceipt"),
    ("post", "/conversations/{conversation_id}/commands", "Command", "CommandReceipt"),
    ("get", "/commands/{command_id}", None, "CommandReceipt"),
    ("get", "/approvals/{approval_id}", None, "ApprovalView"),
    ("post", "/approvals/{approval_id}/commands", "Command", "CommandReceipt"),
    ("post", "/conversations/{conversation_id}/subscriptions", None, "SubscriptionView"),
    ("get", "/events/poll", None, "EventPage"),
    ("get", "/events", None, "Event"),
    ("put", "/subscriptions/{subscription_id}/ack", "Acknowledgement", "Acknowledged"),
    ("delete", "/subscriptions/{subscription_id}", None, "Unsubscribed"),
    ("get", "/choices", None, "Choices"),
    ("get", "/resources/{reference}", None, "ResourceView"),
    ("post", "/uploads", "bytes", "AttachmentView"),
    ("post", "/uploads/sessions", "UploadRequest", "UploadView"),
    ("get", "/uploads/{upload_id}", None, "UploadView"),
    ("put", "/uploads/{upload_id}/chunks", "bytes", "UploadView"),
    ("post", "/uploads/{upload_id}/complete", "UploadCompletion", "AttachmentView"),
    ("delete", "/uploads/{upload_id}", None, "UploadCancelled"),
    ("get", "/attachments/{reference}", None, "bytes"),
    ("get", "/search", None, "SearchPage"),
    ("get", "/conversations/{conversation_id}/history", None, "TranscriptPage"),
    ("get", "/conversations/{conversation_id}/workspace", None, "ConversationWorkspace"),
    ("get", "/conversations/{conversation_id}/open", None, "ConversationOpenView"),
    ("get", "/conversations/{conversation_id}/delegated", None, "DelegatedActivityView"),
    ("get", "/conversations/{conversation_id}/delegated/{run_id}", None, "DelegatedRun"),
    ("get", "/conversations/{conversation_id}/draft", None, "DraftView"),
    ("get", "/conversations/{conversation_id}/steering", None, "ParentSteeringView"),
    ("get", "/conversations/{conversation_id}/queue", None, "ClientQueueView"),
    ("put", "/conversations/{conversation_id}/draft", "DraftSave", "DraftView"),
    ("post", "/resources/commands", "Command", "CommandReceipt"),
    ("post", "/resources/folder-selection", None, "FolderGrantView"),
    ("get", "/resources/setup/deck", None, "DeckSetupOptions"),
    ("get", "/resources/library/{kind}", None, "ResourceChoicePage"),
    ("get", "/conversations/{conversation_id}/artifacts/{binding_id}/preview", None, "ArtifactPreview"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/inspector", None, "WorkspaceInspector"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/changes", None, "WorkspaceChanges"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/directory", None, "WorkspaceDirectory"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/file", None, "WorkspaceFile"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/diff", None, "WorkspaceDiff"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/change-sets", None, "WorkspaceChangeSetPage"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/change-sets/{change_set_id}", None, "WorkspaceChangeSetFiles"),
)


def schema_bundle() -> dict:
    """Return deterministic JSON schemas, with no environment or runtime reads."""
    return {name: model.model_json_schema() for name, model in MODELS.items()}


def ts_type(schema: dict) -> str:
    """Translate the closed DTO subset of JSON Schema to TypeScript."""
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    if "oneOf" in schema or "anyOf" in schema:
        return " | ".join(ts_type(value) for value in schema.get("oneOf", schema.get("anyOf")))
    kind = schema.get("type")
    if kind == "array":
        return f"Array<{ts_type(schema.get('items', {}))}>"
    if kind == "object" or "properties" in schema:
        required = schema.get("required", [])
        fields = [f"{json.dumps(name)}{'' if name in required else '?'}: {ts_type(value)}"
                  for name, value in schema.get("properties", {}).items()]
        if schema.get("additionalProperties") is not False:
            fields.append("[key: string]: unknown")
        return "{ " + "; ".join(fields) + " }"
    return {"string": "string", "boolean": "boolean", "number": "number", "integer": "number", "null": "null"}.get(kind, "unknown")


def typescript(bundle: dict) -> str:
    definitions = {}
    for schema in bundle.values():
        for name, value in schema.get("$defs", {}).items():
            if name in definitions and definitions[name] != value:
                raise ValueError(f"Conflicting schema definition: {name}")
            definitions[name] = value
    lines = ["// Generated by scripts/generate_client_platform_contracts.py. Do not edit."]
    for name, value in sorted(definitions.items()):
        lines.append(f"export type {name} = {ts_type(value)};")
    for name, schema in bundle.items():
        if name not in definitions:
            lines.append(f"export type {name} = {ts_type(schema)};")
    lines.append("const wireSchemas: Record<string, any> = " + json.dumps(bundle, sort_keys=True, separators=(",", ":")) + ";")
    lines.append(r'''
function matches(schema: any, value: unknown, root: any): boolean {
  if (schema.$ref) return matches(root.$defs[schema.$ref.split('/').pop()], value, root);
  if (schema.const !== undefined && value !== schema.const) return false;
  if (schema.enum && !schema.enum.includes(value)) return false;
  if (schema.oneOf && schema.oneOf.filter((s: any) => matches(s, value, root)).length !== 1) return false;
  if (schema.anyOf && !schema.anyOf.some((s: any) => matches(s, value, root))) return false;
  if (schema.type === 'null') return value === null;
  if (schema.type === 'string') {
    if (typeof value !== 'string') return false;
    if (schema.minLength && [...value].length < schema.minLength) return false;
    if (schema.maxLength && [...value].length > schema.maxLength) return false;
    if (schema.pattern && !new RegExp(schema.pattern).test(value)) return false;
    if (schema.format === 'uuid' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) return false;
  }
  if (schema.type === 'boolean' && typeof value !== 'boolean') return false;
  if (schema.type === 'integer' || schema.type === 'number') {
    if (typeof value !== 'number' || !Number.isFinite(value)) return false;
    if (schema.type === 'integer' && !Number.isInteger(value)) return false;
    if (schema.minimum !== undefined && value < schema.minimum) return false;
    if (schema.maximum !== undefined && value > schema.maximum) return false;
  }
  if (schema.type === 'array') {
    if (!Array.isArray(value) || (schema.maxItems !== undefined && value.length > schema.maxItems)) return false;
    if (!value.every(v => matches(schema.items || {}, v, root))) return false;
  }
  if (schema.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const object = value as Record<string, unknown>;
    if ((schema.required || []).some((key: string) => !(key in object))) return false;
    for (const [key, item] of Object.entries(object)) {
      const child = (schema.properties || {})[key];
      if (!child && schema.additionalProperties === false) return false;
      if (child && !matches(child, item, root)) return false;
    }
  }
  return true;
}
export function isEvent(value: unknown): value is Event {
  if (!matches(wireSchemas.Event, value, wireSchemas.Event)) return false;
  const event = value as Event;
  return BigInt(event.source_sequence_start) <= BigInt(event.source_sequence_end);
}
export function isCommand(value: unknown): value is Command {
  return matches(wireSchemas.Command, value, wireSchemas.Command);
}
export function validateWire<T>(name: string, value: unknown): T {
  if (!wireSchemas[name] || !matches(wireSchemas[name], value, wireSchemas[name]))
    throw new Error('protocol_incompatible');
  if (name === 'Event' && !isEvent(value)) throw new Error('protocol_incompatible');
  if (name === 'EventPage' && !(value as EventPage).events.every(record => isEvent(record.event)))
    throw new Error('protocol_incompatible');
  return value as T;
}
function proofHeaders(proof?: SessionProof): Record<string, string> {
  return proof ? {'X-Client-Session': proof.client_session_id, 'X-CSRF-Token': proof.csrf_token} : {};
}
// Client pacing leaves headroom under the server's independently enforced
// budgets. Pending work is bounded and abortable; no mutation is replayed.
const budgets = new WeakMap<SessionProof, Map<string, {tokens:number; at:number; waiting:number}>>();
async function pace(proof: SessionProof | undefined, path: string, method: string, signal?: AbortSignal, body?: unknown): Promise<void> {
  if (!proof) return;
  const view = /^\/conversations\/[^/?]+(?:\/(?:open|workspace|delegated))?(?:\?|$)/.test(path);
  const observation = path.startsWith('/events') || /^\/conversations\/[^/]+\/subscriptions$/.test(path) || /^\/subscriptions\/[^/]+$/.test(path);
  const type = body && typeof body === 'object' && 'type' in body ? body.type : undefined;
  const control = method === 'POST' && path.endsWith('/commands') && (type === 'conversation.stop' || type === 'approval.resolve')
    || method === 'DELETE' && /^\/uploads\/[^/]+$/.test(path);
  // Draft autosaves, uploads and ordinary commands consume one server bucket.
  // Stop/approval/cancel and ACK retain their independent admission paths.
  if (control || /^\/subscriptions\/[^/]+\/ack$/.test(path)) return;
  const lane = observation ? 'observation' : method === 'GET' ? (view ? 'view' : 'query') : 'mutation';
  let lanes = budgets.get(proof);
  if (!lanes) { lanes = new Map(); budgets.set(proof, lanes); }
  const [capacity, rate] = lane === 'query' ? [20, 2] : lane === 'view' ? [50, 4] : lane === 'observation' ? [100, 10] : [8, 1];
  let bucket = lanes.get(lane);
  if (!bucket) { bucket = {tokens:capacity,at:performance.now(),waiting:0}; lanes.set(lane,bucket); }
  if (bucket.waiting >= 128) throw {code:'rate_limited'};
  bucket.waiting++;
  try {
    while (true) {
      signal?.throwIfAborted();
      const now = performance.now();
      bucket.tokens = Math.min(capacity, bucket.tokens + Math.max(0, now - bucket.at) * rate / 1000);
      bucket.at = now;
      if (bucket.tokens >= 1) { bucket.tokens--; return; }
      await new Promise<void>((resolve,reject) => {
        const abort = () => { clearTimeout(timer); reject(new DOMException('Cancelled','AbortError')); };
        const timer = setTimeout(() => { signal?.removeEventListener('abort',abort); resolve(); }, Math.ceil((1-bucket.tokens)*1000/rate));
        signal?.addEventListener('abort',abort,{once:true});
      });
    }
  } finally { bucket.waiting--; }
}
async function jsonRequest<T>(baseUrl: string, path: string, schema: string,
  proof?: SessionProof, method = 'GET', body?: unknown, key?: string, signal?: AbortSignal, keepalive = false): Promise<T> {
  const encoded = body === undefined ? undefined : JSON.stringify(body);
  await pace(proof,path,method,signal,body);
  const response = await fetch(`${baseUrl}/api/v1${path}`, {
    method, credentials: 'same-origin', cache: 'no-store', signal, ...(keepalive ? {keepalive: true} : {}),
    headers: {...proofHeaders(proof), ...(body === undefined ? {} : {'Content-Type': 'application/json'}),
      ...(key ? {'Idempotency-Key': key} : {})},
    body: encoded,
  });
  const value: unknown = await response.json();
  if (!response.ok) throw validateWire<Problem>('Problem', value);
  return validateWire<T>(schema, value);
}
const id = encodeURIComponent;
const query = (values: Record<string, string | number | undefined>) => '?' + new URLSearchParams(
  Object.entries(values).filter(([,v]) => v !== undefined).map(([k,v]) => [k, String(v)]));

export async function handshake(baseUrl: string, body: Handshake, signal?: AbortSignal): Promise<HandshakeView> {
  validateWire<Handshake>('Handshake', body);
  return jsonRequest(baseUrl, '/handshake', 'HandshakeView', undefined, 'POST', body, undefined, signal);
}
export const listConversations = (base: string, proof: SessionProof, limit = 50, cursor?: string, signal?: AbortSignal, group = 'all'): Promise<ConversationPage> =>
  jsonRequest(base, '/conversations' + query({limit,cursor,group:group === 'all' ? undefined : group}), 'ConversationPage', proof, 'GET', undefined, undefined, signal);
export const getConversation = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<ConversationView> =>
  jsonRequest(base, `/conversations/${id(conversation)}`, 'ConversationView', proof, 'GET', undefined, undefined, signal);
export const getTranscript = (base: string, proof: SessionProof, conversation: string, limit = 100, cursor?: string, signal?: AbortSignal): Promise<TranscriptPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/transcript` + query({limit,cursor}), 'TranscriptPage', proof, 'GET', undefined, undefined, signal);
export const getMessageText = (base: string, proof: SessionProof, conversation: string, message: string, cursor?: string, signal?: AbortSignal): Promise<LazyContent> =>
  jsonRequest(base, `/conversations/${id(conversation)}/text/${id(message)}` + query({cursor}), 'LazyContent', proof, 'GET', undefined, undefined, signal);
export const getChoices = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<Choices> =>
  jsonRequest(base, '/choices', 'Choices', proof, 'GET', undefined, undefined, signal);
export const searchLibrary = (base: string, proof: SessionProof, text: string, conversation_id?: string, cursor?: string, signal?: AbortSignal): Promise<SearchPage> =>
  jsonRequest(base, '/search' + query({query:text,conversation_id,cursor}), 'SearchPage', proof, 'GET', undefined, undefined, signal);
export const getHistory = (base: string, proof: SessionProof, conversation: string, message_id?: string, cursor?: string, signal?: AbortSignal): Promise<TranscriptPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/history` + query({message_id,cursor}), 'TranscriptPage', proof, 'GET', undefined, undefined, signal);
export const getWorkspace = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<ConversationWorkspace> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspace`, 'ConversationWorkspace', proof, 'GET', undefined, undefined, signal);
export const openConversation = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<ConversationOpenView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/open`, 'ConversationOpenView', proof, 'GET', undefined, undefined, signal);
export const getDraft = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<DraftView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/draft`, 'DraftView', proof, 'GET', undefined, undefined, signal);
export const getDelegatedActivity = (base: string, proof: SessionProof, conversation: string, cursor?: string, signal?: AbortSignal): Promise<DelegatedActivityView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/delegated` + query({cursor}), 'DelegatedActivityView', proof, 'GET', undefined, undefined, signal);
export const getDelegatedRun = (base: string, proof: SessionProof, conversation: string, run: string, signal?: AbortSignal): Promise<DelegatedRun> =>
  jsonRequest(base, `/conversations/${id(conversation)}/delegated/${id(run)}`, 'DelegatedRun', proof, 'GET', undefined, undefined, signal);
export const getQueue = (base: string, proof: SessionProof, conversation: string, generation_id?: string, cursor?: string, signal?: AbortSignal): Promise<ClientQueueView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/queue` + query({generation_id,cursor}), 'ClientQueueView', proof, 'GET', undefined, undefined, signal);
export const getSteering = (base: string, proof: SessionProof, conversation: string, generation_id?: string, cursor?: string, signal?: AbortSignal): Promise<ParentSteeringView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/steering` + query({generation_id,cursor}), 'ParentSteeringView', proof, 'GET', undefined, undefined, signal);
export const saveDraft = (base: string, proof: SessionProof, conversation: string, body: DraftSave, signal?: AbortSignal): Promise<DraftView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/draft`, 'DraftView', proof, 'PUT', body, undefined, signal);
export const getResourceLibrary = (base: string, proof: SessionProof, kind: 'artifact'|'workspace', cursor?: string, signal?: AbortSignal): Promise<ResourceChoicePage> =>
  jsonRequest(base, `/resources/library/${kind}` + query({cursor}), 'ResourceChoicePage', proof, 'GET', undefined, undefined, signal);
export const getDeckSetup = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<DeckSetupOptions> =>
  jsonRequest(base, '/resources/setup/deck', 'DeckSetupOptions', proof, 'GET', undefined, undefined, signal);
export const pickFolder = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<FolderGrantView> =>
  jsonRequest(base, '/resources/folder-selection', 'FolderGrantView', proof, 'POST', undefined, undefined, signal);
export const getArtifactPreview = (base: string, proof: SessionProof, conversation: string, binding: string, page_id?: string, known_revision?: string, signal?: AbortSignal): Promise<ArtifactPreview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/preview` + query({page_id,known_revision}), 'ArtifactPreview', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceInspector = (base: string, proof: SessionProof, conversation: string, binding: string, refresh = false, signal?: AbortSignal): Promise<WorkspaceInspector> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/inspector` + query({refresh:refresh?'true':'false'}), 'WorkspaceInspector', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceChanges = (base: string, proof: SessionProof, conversation: string, binding: string, revision?: string, cursor?: string, signal?: AbortSignal): Promise<WorkspaceChanges> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/changes` + query({revision,cursor}), 'WorkspaceChanges', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceDirectory = (base: string, proof: SessionProof, conversation: string, binding: string, directory = '', cursor?: string, revision?: string, signal?: AbortSignal): Promise<WorkspaceDirectory> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/directory` + query({directory,cursor,revision}), 'WorkspaceDirectory', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceFile = (base: string, proof: SessionProof, conversation: string, binding: string, path: string, offset = 0, revision?: string, signal?: AbortSignal): Promise<WorkspaceFile> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/file` + query({path,offset,revision}), 'WorkspaceFile', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceDiff = (base: string, proof: SessionProof, conversation: string, binding: string, path: string, snapshot_revision: string, offset = 0, revision?: string, signal?: AbortSignal): Promise<WorkspaceDiff> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/diff` + query({path,snapshot_revision,offset,revision}), 'WorkspaceDiff', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceChangeSets = (base: string, proof: SessionProof, conversation: string, binding: string, revision: string, cursor?: string, signal?: AbortSignal): Promise<WorkspaceChangeSetPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/change-sets` + query({revision,cursor}), 'WorkspaceChangeSetPage', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceChangeSetFiles = (base: string, proof: SessionProof, conversation: string, binding: string, change: string, revision: string, cursor?: string, signal?: AbortSignal): Promise<WorkspaceChangeSetFiles> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/change-sets/${id(change)}` + query({revision,cursor}), 'WorkspaceChangeSetFiles', proof, 'GET', undefined, undefined, signal);
export const getLazyContent = (base: string, proof: SessionProof, conversation: string, message: string,
  limit_bytes = 65536, cursor?: string, signal?: AbortSignal): Promise<LazyContent> =>
  jsonRequest(base, `/conversations/${id(conversation)}/content/${id(message)}` + query({limit_bytes,cursor}), 'LazyContent', proof, 'GET', undefined, undefined, signal);
export const getReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<CommandReceipt> =>
  jsonRequest(base, `/commands/${id(command)}`, 'CommandReceipt', proof, 'GET', undefined, undefined, signal);
export const getApproval = (base: string, proof: SessionProof, approval: string, signal?: AbortSignal): Promise<ApprovalView> =>
  jsonRequest(base, `/approvals/${id(approval)}?include_summary=true`, 'ApprovalView', proof, 'GET', undefined, undefined, signal);
export const getResource = (base: string, proof: SessionProof, reference: string, signal?: AbortSignal): Promise<ResourceView> =>
  jsonRequest(base, `/resources/${id(reference)}`, 'ResourceView', proof, 'GET', undefined, undefined, signal);
export const subscribe = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<SubscriptionView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/subscriptions`, 'SubscriptionView', proof, 'POST', undefined, undefined, signal);
export const poll = (base: string, proof: SessionProof, subscription_id: string, cursor: string, signal?: AbortSignal): Promise<EventPage> =>
  jsonRequest(base, '/events/poll' + query({subscription_id,cursor}), 'EventPage', proof, 'GET', undefined, undefined, signal);
export const acknowledge = (base: string, proof: SessionProof, subscription: string, cursor: string, signal?: AbortSignal): Promise<Acknowledged> =>
  jsonRequest(base, `/subscriptions/${id(subscription)}/ack`, 'Acknowledged', proof, 'PUT', {cursor}, undefined, signal);
export const unsubscribe = (base: string, proof: SessionProof, subscription: string, signal?: AbortSignal, keepalive = false): Promise<Unsubscribed> =>
  jsonRequest(base, `/subscriptions/${id(subscription)}`, 'Unsubscribed', proof, 'DELETE', undefined, undefined, signal, keepalive);
export const beginUpload = (base: string, proof: SessionProof, body: UploadRequest, signal?: AbortSignal): Promise<UploadView> => {
  validateWire<UploadRequest>('UploadRequest', body);
  return jsonRequest(base, '/uploads/sessions', 'UploadView', proof, 'POST', body, undefined, signal);
};
export const uploadStatus = (base: string, proof: SessionProof, upload: string, signal?: AbortSignal): Promise<UploadView> =>
  jsonRequest(base, `/uploads/${id(upload)}`, 'UploadView', proof, 'GET', undefined, undefined, signal);
export const cancelUpload = (base: string, proof: SessionProof, upload: string, signal?: AbortSignal): Promise<UploadCancelled> =>
  jsonRequest(base, `/uploads/${id(upload)}`, 'UploadCancelled', proof, 'DELETE', undefined, undefined, signal);
export const completeUpload = (base: string, proof: SessionProof, upload: string, body: UploadCompletion, key: string, signal?: AbortSignal): Promise<AttachmentView> => {
  validateWire<UploadCompletion>('UploadCompletion', body);
  return jsonRequest(base, `/uploads/${id(upload)}/complete`, 'AttachmentView', proof, 'POST', body, key, signal);
};
export async function uploadChunk(base: string, proof: SessionProof, upload: string, offset: number, data: Blob, signal?: AbortSignal): Promise<UploadView> {
  if (data.size > 1048576) throw new Error('payload_too_large');
  const response = await fetch(`${base}/api/v1/uploads/${id(upload)}/chunks` + query({offset}), {
    method: 'PUT', credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), body: data, signal,
  });
  const value = await response.json();
  if (!response.ok) throw validateWire<Problem>('Problem', value);
  return validateWire<UploadView>('UploadView', value);
}
export async function readAttachment(base: string, proof: SessionProof, reference: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${base}/api/v1/attachments/${id(reference)}`, {
    credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), signal,
  });
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  const data = await response.blob();
  if (data.size > 26214400) throw new Error('protocol_incompatible');
  return data;
}
export async function* observeEvents(base: string, proof: SessionProof, subscription_id: string,
  cursor: string, signal?: AbortSignal): AsyncGenerator<EventRecord | StreamReset> {
  const response = await fetch(`${base}/api/v1/events` + query({subscription_id,cursor}), {
    credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), signal,
  });
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  if (!response.body) throw new Error('protocol_incompatible');
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', {fatal: true});
  let buffer = '';
  try {
    while (true) {
      const part = await reader.read();
      buffer += decoder.decode(part.value, {stream: !part.done});
      let cut: number;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0,cut); buffer = buffer.slice(cut+2);
        if (new TextEncoder().encode(frame).length > 69632) throw new Error('protocol_incompatible');
        const fields = Object.fromEntries(frame.split('\n').filter(l => l && !l.startsWith(':')).map(l => {
          const at = l.indexOf(':'); return [l.slice(0,at),l.slice(at+1).trimStart()];
        }));
        if (!fields.event) continue;
        if (fields.event === 'snapshot_required') {
          yield validateWire<StreamReset>('StreamReset', JSON.parse(fields.data)); return;
        }
        if (fields.event !== 'domain') throw new Error('protocol_incompatible');
        const event = JSON.parse(fields.data);
        if (!isEvent(event) || !fields.id) throw new Error('protocol_incompatible');
        yield {event, cursor: fields.id};
      }
      if (new TextEncoder().encode(buffer).length > 69632) throw new Error('protocol_incompatible');
      if (part.done) return;
    }
  } finally { await reader.cancel(); reader.releaseLock(); }
}
export async function sendConversationCommand(baseUrl: string, conversationId: string | null,
  command: Command, proof: SessionProof, idempotencyKey: string, signal?: AbortSignal): Promise<CommandReceipt> {
  if (!isCommand(command)) throw new Error('invalid_command');
  const suffix = command.type === 'approval.resolve' ? `/approvals/${id(conversationId || '')}/commands`
    : conversationId === null && (command.type === 'resource.setup' || command.type === 'resource.continue') ? '/resources/commands'
    : conversationId === null ? '/conversations/commands'
    : `/conversations/${encodeURIComponent(conversationId)}/commands`;
  return jsonRequest(baseUrl, suffix, 'CommandReceipt', proof, 'POST', command, idempotencyKey, signal);
}
export async function sendApprovalCommand(base: string, approval: string, command: Command,
  proof: SessionProof, key: string, signal?: AbortSignal): Promise<CommandReceipt> {
  if (!isCommand(command) || command.type !== 'approval.resolve') throw new Error('invalid_command');
  return jsonRequest(base, `/approvals/${id(approval)}/commands`, 'CommandReceipt', proof, 'POST', command, key, signal);
}
'''.strip())
    return "\n".join(lines) + "\n"


def outputs() -> dict[Path, str]:
    """Return all generated artifacts for check mode and deterministic tests."""
    bundle = schema_bundle()
    destination = ROOT / "contracts/client-platform/v1"
    result = {destination / "schema" / f"{name}.schema.json": json.dumps(value, indent=2, sort_keys=True) + "\n"
              for name, value in bundle.items()}
    result[destination / "typescript/client.ts"] = typescript(bundle)
    paths = {}
    for method, suffix, request, response in OPERATIONS:
        parameters = [{"name": name, "in": "path", "required": True,
                       "schema": {"type": "string", "minLength": 1, "maxLength": 256}}
                      for name in re.findall(r"\{([^}]+)\}", suffix)]
        if suffix != "/handshake":
            parameters += [{"name": name, "in": "header", "required": True,
                            "schema": {"type": "string", "maxLength": 256}}
                           for name in ("X-Client-Session", "X-CSRF-Token")]
        query_parameters = []
        if suffix == "/conversations" or suffix.endswith("/transcript"):
            query_parameters = [("limit", False, {"type": "integer", "minimum": 1,
                                 "maximum": 100 if suffix.endswith("/transcript") else 200}),
                                ("cursor", False, {"type": "string", "maxLength": 2048})]
        elif "/content/" in suffix:
            query_parameters = [("limit_bytes", False, {"type": "integer", "minimum": 1, "maximum": 65536}),
                                ("cursor", False, {"type": "string", "maxLength": 2048})]
        elif suffix in {"/events", "/events/poll"}:
            query_parameters = [(name, True, {"type": "string", "maxLength": 2048})
                                for name in ("subscription_id", "cursor")]
        elif suffix.endswith("/chunks"):
            query_parameters = [("offset", True, {"type": "integer", "minimum": 0, "maximum": 26214400})]
        elif suffix == "/uploads":
            query_parameters = [(name, True, {"type": "string", "maxLength": 240})
                                for name in ("conversation_id", "name")]
            parameters.append({"name": "X-Command-Id", "in": "header", "required": True,
                               "schema": {"type": "string", "format": "uuid"}})
        parameters += [{"name": name, "in": "query", "required": required, "schema": schema}
                       for name, required, schema in query_parameters]
        if request in {"Command", "UploadCompletion"} or suffix == "/uploads":
            parameters.append({"name": "Idempotency-Key", "in": "header", "required": True,
                               "schema": {"type": "string", "format": "uuid"}})
        response_schema = ({"type": "string", "format": "binary", "maxLength": 26214400} if response == "bytes"
                           else {"$ref": f"./{response}.schema.json"})
        mime = "text/event-stream" if suffix == "/events" else "application/octet-stream" if response == "bytes" else "application/json"
        operation = {"parameters": parameters, "responses": {
            "200": {"description": "Authenticated result", "content": {mime: {"schema": response_schema}}},
            "default": {"description": "Safe problem", "content": {"application/problem+json": {
                "schema": {"$ref": "./Problem.schema.json"}}}}}}
        if response == "CommandReceipt":
            operation["responses"]["202"] = {"description": "Durable acceptance", "content": {
                "application/json": {"schema": response_schema}}}
        if request:
            request_schema = ({"type": "string", "format": "binary",
                               "maxLength": 1048576}
                              if request == "bytes" else {"$ref": f"./{request}.schema.json"})
            operation["requestBody"] = {"required": True, "content": {
                "application/octet-stream" if request == "bytes" else "application/json": {"schema": request_schema}}}
        if suffix == "/events":
            operation["description"] = "SSE domain data validates Event; snapshot_required data validates StreamReset. IDs are signed cursors."
        paths.setdefault("/api/v1" + suffix, {})[method] = operation
    openapi = {"openapi": "3.1.0", "info": {"title": "Row-Bot client protocol", "version": "1.0"},
               "paths": paths}
    result[destination / "schema/openapi.json"] = json.dumps(openapi, indent=2, sort_keys=True) + "\n"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    for path, content in outputs().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                changed.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Preserve clean checkout line endings and timestamps on unchanged files.
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                path.write_text(content, encoding="utf-8", newline="\n")
    if changed:
        print("Generated contract mismatch: " + ", ".join(changed))
        return 1
    print("Client platform contracts " + ("verified" if args.check else "generated"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
