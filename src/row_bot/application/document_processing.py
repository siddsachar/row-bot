"""Reviewed document worker policy over canonical configuration and admissions.

Only keyed policy identities are durable. Runtime credentials remain private,
and an absent authenticated resolver never grants legacy processing authority.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from row_bot.document_jobs import DocumentJobService

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import sys
from typing import Any

from row_bot.application import knowledge_commands as common
from row_bot.runtime import admissions


@dataclass(frozen=True, repr=False)
class _Capture:
    context: dict
    chat: Any
    embedding: Any
    digest: str


@dataclass(frozen=True)
class DocumentWorkerPolicy:
    embedding_config: dict
    embedding: Any
    invoke: Callable[[str], str]
    validate: Callable[[], None]
    add_source_guard: Callable[[Callable[[], None]], None]


class DocumentProcessingPolicy:
    """Root-injected current session/profile/review authority; no second registry."""

    def __init__(self, *, read_context: Callable[[], dict], validate: Callable[[], None],
                 validate_action: Callable[[str], None], owner_id: str = "", authority_id: str = "",
                 conversation_id: str = "") -> None:
        self.read_context = read_context
        self.validate = validate
        self.validate_action = validate_action
        self.owner_id, self.authority_id = owner_id, authority_id
        self.conversation_id = conversation_id

    def authority_digest(self) -> str:
        if not self.owner_id or not self.authority_id or not self.conversation_id:
            raise common._error("document_processing_authority_unavailable")
        common._id(self.conversation_id)
        return admissions.keyed_digest({"document_processing_owner":self.owner_id,
            "authority":self.authority_id,"conversation_id":self.conversation_id},read_only=True)

    def validate_admission(self, proof: dict) -> None:
        self.validate()
        if (proof.get("processing_owner_id") != self.owner_id or proof.get("conversation_id") != self.conversation_id
                or proof.get("authority_digest") != self.authority_digest()):
            raise common._error("document_processing_authority_changed")
        command_id = common._uuid(proof.get("processing_command_id"))
        metadata = admissions.read_command_metadata(self.owner_id,command_id)
        receipt = admissions.read_command_receipt(self.owner_id,command_id)
        expected = {key:proof.get(key) for key in ("batch_id","owner_id","processing_owner_id","conversation_id",
            "processing_command_id","source_digest","policy_digest","authority_digest")}
        if (proof.keys() != expected.keys()
                or not metadata or metadata.get("type") != "document.batch.process" or metadata.get("target") != "document-processing"
                or not receipt or receipt.get("_document_processing") != expected):
            raise common._error("document_processing_proof_unavailable")
        if self.capture().digest != proof.get("policy_digest"):
            raise common._error("document_processing_policy_changed")

    def capture(self) -> _Capture:
        self.validate()
        context = deepcopy(self.read_context())
        if not isinstance(context, dict) or len(json.dumps(context, allow_nan=False).encode()) > 65536:
            raise common._error("document_processing_policy_unavailable")
        from row_bot.approval_policy import decision_for_action
        if decision_for_action(context.get("approval_mode")) == "block":
            raise common._error("document_processing_denied")
        self.validate_action("document.batch.process")
        # Passive review must not initialize the embedding data owner on a cold
        # process. The application supplies the already initialized canonical one.
        embedding_owner = sys.modules.get("row_bot.embedding_providers")
        if embedding_owner is None:
            raise common._error("document_processing_policy_unavailable")
        from row_bot.providers.runtime import capture_chat_runtime
        from row_bot.providers.reasoning import ReasoningCapabilities, ReasoningRequestPlan, ReasoningSelection
        selection = context.get("model_override")
        if not isinstance(selection, str) or not selection.startswith("model:") or len(selection) > 512:
            raise common._error("document_processing_model_unavailable")
        snapshot = context.get("reasoning_snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("model_ref") != selection:
            raise common._error("document_processing_reasoning_unavailable")
        plan = ReasoningRequestPlan(selection, ReasoningSelection.from_json(snapshot.get("selection")),
                                    ReasoningCapabilities.from_json(snapshot.get("capabilities")))
        chat = capture_chat_runtime(selection, reasoning_plan=plan)
        embedding = embedding_owner.capture_embedding_runtime(embedding_owner.get_embedding_config())
        common._id(self.conversation_id)
        digest = admissions.keyed_digest({"context":context,"chat":asdict(chat),"embedding":asdict(embedding),
            "processing_owner_id":self.owner_id,"conversation_id":self.conversation_id}, read_only=True)
        self.validate()
        return _Capture(context, chat, embedding, digest)

    def review(self, batch_id: str, revision: str) -> dict:
        from row_bot.application.document_job_commands import _snapshot
        common._id(batch_id)
        snapshot = _snapshot([batch_id])
        if common._digest(snapshot) != revision or not batch_id.startswith("client_"):
            raise common._error("document_queue_changed")
        captured = self.capture()
        resolved = captured.chat.resolved
        return {"schema_version":1,"action":"document.batch.process","batch_id":batch_id,
            "revision":revision,"policy_digest":captured.digest,"provider_work":True,"conversation_id":self.conversation_id,
            "knowledge_projection_scope":"saved_knowledge",
            "chat":{"provider_id":resolved.provider_id,"model_ref":resolved.selection_ref,
                    "execution_location":resolved.execution_location},
            "embedding":{"provider":captured.embedding.provider,
                         "execution_location":"local" if captured.embedding.provider == "local" else "remote"}}

    @contextmanager
    def worker_scope(self, policy_digest: str, *, validate_work: Callable[[], None]) -> Iterator[DocumentWorkerPolicy]:
        captured = self.capture()
        if captured.digest != policy_digest:
            raise common._error("document_processing_policy_changed")
        source_guards = []
        def validate() -> None:
            validate_work()
            current = self.capture()
            if current.digest != captured.digest:
                raise common._error("document_processing_policy_changed")
            for guard in source_guards:
                guard()
        from row_bot.embedding_providers import captured_embedding_provider, use_captured_embeddings
        from row_bot.providers.runtime import captured_chat_model
        # Chat construction is lazy: indexing never initializes an LLM. Both
        # resources live only for this invocation and use one captured selection.
        from contextlib import ExitStack
        with ExitStack() as stack:
            embedding = stack.enter_context(captured_embedding_provider(captured.embedding, validate=validate))
            stack.enter_context(use_captured_embeddings(captured.embedding.config, embedding, validate))
            model = None
            def invoke(prompt: str) -> str:
                nonlocal model
                validate()
                if model is None:
                    model = stack.enter_context(captured_chat_model(captured.chat, validate=validate))
                from langchain_core.messages import HumanMessage
                response = model.invoke([HumanMessage(content=prompt)])
                validate()
                return response.content
            yield DocumentWorkerPolicy(deepcopy(captured.embedding.config), embedding, invoke, validate, source_guards.append)


def read_document_processing_command(*, command_id: str, policy: DocumentProcessingPolicy) -> dict:
    policy.validate()
    command_id = common._uuid(command_id)
    metadata = admissions.read_command_metadata(policy.owner_id,command_id)
    receipt = admissions.read_command_receipt(policy.owner_id,command_id)
    proof = receipt.get("_document_processing") if receipt else None
    if (not metadata or metadata.get("type") != "document.batch.process" or metadata.get("target") != "document-processing"
            or not isinstance(proof,dict) or proof.get("authority_digest") != policy.authority_digest()
            or proof.get("processing_owner_id") != policy.owner_id or proof.get("conversation_id") != policy.conversation_id
            or proof.get("processing_command_id") != command_id):
        raise common._error("document_processing_unavailable")
    policy.validate()
    if receipt.get("status") == "completed" and receipt.get("batch_id") == proof.get("batch_id"):
        return {"command_id":command_id,"status":"completed","batch_id":common._id(proof["batch_id"]),"processing":"admitted"}
    return {"command_id":command_id,"status":"partial","code":"document_processing_uncertain"}


def execute_document_processing(command: dict, *, service: DocumentJobService | None, key: str, policy: DocumentProcessingPolicy,
                                validate_review: Callable[[dict,dict],None]) -> dict:
    from row_bot.application.document_job_commands import _snapshot
    from row_bot.data_paths import get_row_bot_data_dir
    import contextlib
    policy.validate()
    command = deepcopy(command)
    command_id = common._uuid(command.get("command_id"))
    payload = command.get("payload")
    if (command.get("type") != "document.batch.process" or not isinstance(payload,dict)
            or payload.keys() != {"batch_id","revision","review_id","conversation_id"}
            or payload["conversation_id"] != policy.conversation_id):
        raise common._error("invalid_document_processing")
    def receipt() -> dict:
        return read_document_processing_command(command_id=command_id,policy=policy)
    if admissions.read_command_metadata(policy.owner_id,command_id) is not None:
        try:
            admissions.claim_command(policy.owner_id,key,command,"document-processing")
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise common._error(str(error)) from error
        return receipt()
    review = policy.review(payload["batch_id"],payload["revision"])
    snapshot = _snapshot([payload["batch_id"]])
    if common._digest(snapshot) != review["revision"]:
        raise common._error("document_queue_changed")
    validate_review(command,review)
    expected = get_row_bot_data_dir(create=False) / "document_ingestion/jobs.db"
    if service.db_path.absolute() != expected.absolute():
        raise common._error("document_processing_unavailable")
    with contextlib.closing(service._connect()) as conn:
        source_digest = service._processing_sources(conn,payload["batch_id"])
        upload = conn.execute("SELECT owner_id FROM document_batch_admissions WHERE batch_id=?",(payload["batch_id"],)).fetchone()
        if upload is None:
            raise common._error("document_processing_unavailable")
        upload_owner = upload["owner_id"]
    proof = {"batch_id":payload["batch_id"],"owner_id":upload_owner,"processing_owner_id":policy.owner_id,
        "conversation_id":policy.conversation_id,"processing_command_id":command_id,
        "source_digest":source_digest,"policy_digest":review["policy_digest"],"authority_digest":policy.authority_digest()}
    authority_error = None
    def validate() -> None:
        nonlocal authority_error
        try:
            policy.validate()
            policy.validate_action("document.batch.process")
            validate_review(command,review)
            if policy.capture().digest != review["policy_digest"]:
                raise common._error("document_processing_policy_changed")
        except Exception as error:
            authority_error = error
            raise
    validate()
    try:
        prior = admissions.claim_command(policy.owner_id,key,command,"document-processing",
            initial_result={"command_id":command_id,"status":"effect_started","_document_processing":proof})
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise common._error(str(error)) from error
        return receipt()
    if prior is not None:
        return receipt()
    try:
        service.authorize_processing(payload["batch_id"],expected_snapshot=snapshot,proof=proof,validate=validate)
    except Exception:
        if authority_error is not None:
            raise authority_error
        return receipt()
    admissions.complete_command(policy.owner_id,key,{"command_id":command_id,"status":"completed",
        "batch_id":payload["batch_id"],"_document_processing":proof})
    return receipt()
