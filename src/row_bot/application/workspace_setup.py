"""Shared conversation/resource composition, with durable confirmed stages.

Domain creation, origin association and binding remain separate owners. This
coordinator never launches generation; an explicit later submit owns that act.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


def _empty_workspace(command: dict, target: str, *, owner_id: str, key: str,
                     authorized_folder: Any, previous: dict | None = None,
                     validate: Any = None) -> tuple[dict, Any | None]:
    """Keep directory recovery evidence inside the existing server admission receipt."""
    from row_bot.developer.client_workspace import (
        EmptyWorkspaceCreationError, EmptyWorkspaceRecovery, create_empty_workspace,
    )
    if authorized_folder is None:
        raise ClientPlatformError("capability_revoked")
    saved = (previous or {}).get("_empty_workspace")
    recovery = EmptyWorkspaceRecovery(**saved) if saved else None
    original = str((previous or {}).get("setup_command_id") or command["command_id"])
    name = recovery.folder_name if recovery else command["payload"]["empty_workspace"]["folder_name"]
    result = {**(previous or {}), "command_id": command["command_id"], "setup_command_id": original,
              "status": "admitting", "resource_kind": "workspace", "setup_intent": "create",
              "association_required": True, "confirmed_stages": (previous or {}).get("confirmed_stages", []),
              **({"conversation_id": target} if target != "resources" else {})}
    result.pop("code", None)
    admissions.command_progress(owner_id, key, result)

    def confirmed(value: EmptyWorkspaceRecovery) -> None:
        result.update(resource_id=value.resource_id, _empty_workspace=asdict(value),
                      folder_reselection_required=True)
        admissions.command_progress(owner_id, key, result)

    try:
        registration = create_empty_workspace(authorized_folder, name, command_id=original,
                                               persist_created=confirmed, recovery=recovery, validate=validate)
    except EmptyWorkspaceCreationError as exc:
        if exc.recovery is None and exc.code != "workspace_creation_unconfirmed":
            raise ClientPlatformError(exc.code) from exc
        result.update(status="partial", code=exc.code,
                      folder_reselection_required=bool(result.get("_empty_workspace")))
        admissions.command_progress(owner_id, key, result)
        return result, None
    result.update(resource_id=registration.workspace.resource_id,
                  resource_revision=registration.workspace.revision)
    if "created" not in result["confirmed_stages"]:
        result["confirmed_stages"].append("created")
    return result, registration


def _clone_workspace(command: dict, target: str, *, owner_id: str, key: str,
                     authorized_folder: Any, previous: dict | None = None,
                     validate: Any = None) -> tuple[dict, Any | None]:
    """Persist each clone stage; continuation never repeats uncertain network work."""
    from row_bot.developer.client_clone import (
        CloneCreationError, CloneRecovery, clone_selected_repository, source_name,
    )
    if authorized_folder is None:
        raise ClientPlatformError('capability_revoked')
    saved = (previous or {}).get('_clone_workspace')
    recovery = CloneRecovery(**saved) if saved else None
    original = str((previous or {}).get('setup_command_id') or command['command_id'])
    source = recovery.source if recovery else command['payload']['clone_workspace']['repo_url']
    try:
        source_name(source)
    except CloneCreationError as exc:
        raise ClientPlatformError(exc.code) from exc
    result = {**(previous or {}), 'command_id': command['command_id'],
              'setup_command_id': original, 'status': 'admitting',
              'resource_kind': 'workspace', 'setup_intent': 'create',
              'association_required': True,
              'confirmed_stages': list((previous or {}).get('confirmed_stages', [])),
              **({'conversation_id': target} if target != 'resources' else {})}
    result.pop('code', None)
    admissions.command_progress(owner_id, key, result)

    def persist(value: CloneRecovery) -> None:
        result.update(resource_id=value.empty['resource_id'], _clone_workspace=asdict(value),
                      folder_reselection_required=True)
        if 'created' not in result['confirmed_stages']:
            result['confirmed_stages'].append('created')
        if value.stage == 'cloned' and 'cloned' not in result['confirmed_stages']:
            result['confirmed_stages'].append('cloned')
        admissions.command_progress(owner_id, key, result)

    try:
        registration = clone_selected_repository(
            authorized_folder, source, command_id=original, persist=persist,
            recovery=recovery, validate=validate or (lambda: None))
    except CloneCreationError as exc:
        result.update(status='partial', code=exc.code,
                      folder_reselection_required=bool(result.get('_clone_workspace')))
        admissions.command_progress(owner_id, key, result)
        return result, None
    result.update(resource_id=registration.workspace.resource_id,
                  resource_revision=registration.workspace.revision)
    return result, registration


def resource_choice(kind: str, identity: str, revision: str | None = None) -> dict:
    from row_bot import threads
    if kind == "artifact":
        from row_bot.designer.client_service import read_artifact
        value = read_artifact(identity)
        if value.mode not in {"deck", "document", "landing", "app_mockup", "storyboard"}:
            raise ClientPlatformError("capability_unavailable")
        origin = value.thread_id or value.missing_origin_thread_id
        current_revision = value.updated_at
        available = True
        name = value.name
    else:
        from row_bot.developer.client_workspace import resolve_workspace_open
        from row_bot.developer.storage import get_workspace
        raw = get_workspace(identity)
        if raw is None:
            raise ClientPlatformError("resource_unavailable")
        value = resolve_workspace_open(identity, revision or raw.updated_at)
        origin = value.association.conversation_id
        current_revision, available, name = value.revision, value.available, value.name
    if revision is not None and current_revision != revision:
        raise ClientPlatformError("resource_revision_conflict")
    status = "unassociated" if not origin else "available" if (
        threads._thread_exists(origin) and not threads._thread_write_blocked(origin)) else "repair_required"
    return {"resource_id": identity, "kind": kind, "name": name,
            "revision": current_revision, "available": available,
            "origin_conversation_id": origin or None, "origin_status": status}


def _associate(kind: str, identity: str, conversation: str, revision: str,
               expected_origin: str | None, repair: bool) -> dict:
    from row_bot import threads
    if not threads._thread_exists(conversation) or threads._thread_write_blocked(conversation):
        raise ClientPlatformError("conversation_deleting")
    if kind == "artifact":
        from row_bot.designer.client_service import associate_origin
        associate_origin(identity, conversation, expected_revision=revision,
                         expected_origin=expected_origin, repair=repair)
    else:
        from row_bot.developer.client_workspace import associate_workspace
        associate_workspace(identity, conversation, revision, expected_origin, repair=repair)
    return resource_choice(kind, identity)


def setup(service: Any, command: dict, target: str, *, owner_id: str, key: str,
          authorized_folder: Any = None, validate: Any = None) -> dict:
    from row_bot import threads
    from row_bot.conversation_resources import bind, list_bindings
    payload = command["payload"]
    continuing = command["type"] == "resource.continue"
    empty_result = None
    if continuing:
        previous = service.receipt(owner_id, str(payload["setup_command_id"]))
        if previous.get("status") not in {"partial", "admitting"} or not previous.get("resource_id"):
            raise ClientPlatformError("invalid_command")
        if (previous.get("conversation_id") or "resources") != target:
            raise ClientPlatformError("action_denied")
        if target != "resources":
            current = service._metadata(target)
            if str(current["client_revision"]) != command["expected_revision"]:
                raise ClientPlatformError("revision_conflict", str(current["client_revision"]))
        if validate:
            validate()
        raw_previous = admissions.receipt(owner_id, str(payload["setup_command_id"])) or {}
        if raw_previous.get('_clone_workspace'):
            previous['_clone_workspace'] = raw_previous['_clone_workspace']
            previous, registration = _clone_workspace(command, target, owner_id=owner_id, key=key,
                authorized_folder=authorized_folder, previous=previous, validate=validate)
            if registration is None:
                return previous
        elif raw_previous.get("_empty_workspace"):
            previous["_empty_workspace"] = raw_previous["_empty_workspace"]
            previous, registration = _empty_workspace(command, target, owner_id=owner_id, key=key,
                authorized_folder=authorized_folder, previous=previous, validate=validate)
            if registration is None:
                return previous
        elif payload.get("folder_grant") or not payload.get("expected_resource_revision"):
            raise ClientPlatformError("invalid_command")
        kind, identity = previous["resource_kind"], previous["resource_id"]
        result = {k: v for k, v in previous.items() if k not in {"code", "current_revision"}}
        result.update(command_id=command["command_id"], status="partial")
        intent = previous.get("setup_intent", "create")
        choice = resource_choice(kind, identity, payload.get("expected_resource_revision"))
        conversation = None if target == "resources" else target
        needs_association = bool(previous.get("association_required")) and "associated" not in result.get("confirmed_stages", [])
    else:
        kind, intent = payload["kind"], payload["intent"]
        if kind not in {"artifact", "workspace"}:
            raise ClientPlatformError("invalid_resource")
        if intent == "new_conversation" and (
            kind != "workspace" or target != "resources"
            or not payload.get("resource_id") or not payload.get("expected_resource_revision")
            or any(payload.get(field) is not None for field in ("deck", "artifact", "empty_workspace", "clone_workspace", "folder_grant", "expected_origin_id"))
        ):
            raise ClientPlatformError("invalid_command")
        if intent == "add" and target == "resources":
            raise ClientPlatformError("invalid_command")
        if target != "resources":
            current = service._metadata(target)
            if str(current["client_revision"]) != command["expected_revision"]:
                raise ClientPlatformError("revision_conflict", str(current["client_revision"]))
        identity = payload.get("resource_id")
        created = False
        if intent == "create":
            if identity:
                raise ClientPlatformError("invalid_command")
            if kind == "artifact":
                from row_bot.designer.client_service import ArtifactSetup, DeckSetup, create_artifact, create_deck
                identity = str(uuid.uuid5(uuid.UUID(str(command["command_id"])), "deck"))
                admissions.command_progress(owner_id, key, {
                    "command_id": command["command_id"], "setup_command_id": command["command_id"],
                    "status": "admitting", "resource_id": identity, "resource_kind": kind,
                    "setup_intent": intent, "association_required": True, "confirmed_stages": [],
                    **({"conversation_id": target} if target != "resources" else {}),
                })
                if payload.get("artifact") is not None:
                    if payload.get("deck") is not None or payload.get("folder_grant") is not None:
                        raise ClientPlatformError("invalid_command")
                    create_artifact(identity, ArtifactSetup(**payload["artifact"]))
                else:
                    create_deck(identity, DeckSetup(**(payload.get("deck") or {})))
                created = True
            elif payload.get('clone_workspace') is not None:
                if validate:
                    validate()
                empty_result, registration = _clone_workspace(command, target, owner_id=owner_id, key=key,
                                                               authorized_folder=authorized_folder, validate=validate)
                if registration is None:
                    return empty_result
                identity, created = registration.workspace.resource_id, registration.created
            elif payload.get("empty_workspace") is not None:
                if validate:
                    validate()
                empty_result, registration = _empty_workspace(command, target, owner_id=owner_id, key=key,
                                                               authorized_folder=authorized_folder, validate=validate)
                if registration is None:
                    return empty_result
                identity, created = registration.workspace.resource_id, registration.created
            else:
                from row_bot.developer.client_workspace import register_existing_folder
                if authorized_folder is None:
                    raise ClientPlatformError("capability_revoked")
                from row_bot.developer.storage import _workspace_id_for_path, get_workspace
                identity = _workspace_id_for_path(authorized_folder.path)
                is_new = get_workspace(identity) is None
                admissions.command_progress(owner_id, key, {
                    "command_id": command["command_id"], "setup_command_id": command["command_id"],
                    "status": "admitting", "resource_id": identity, "resource_kind": kind,
                    "setup_intent": intent, "association_required": is_new, "confirmed_stages": [],
                    **({"conversation_id": target} if target != "resources" else {}),
                })
                registration = register_existing_folder(authorized_folder)
                identity, created = registration.workspace.resource_id, registration.created
        if not identity:
            raise ClientPlatformError("invalid_command")
        choice = resource_choice(kind, identity, None if intent == "create" else payload.get("expected_resource_revision"))
        result = {"command_id": command["command_id"], "setup_command_id": command["command_id"],
                  "status": "partial", "resource_id": identity, "resource_kind": kind,
                  "resource_revision": choice["revision"], "confirmed_stages": ["created"] if created else []}
        if empty_result:
            if '_clone_workspace' in empty_result:
                result.update(_clone_workspace=empty_result['_clone_workspace'],
                              folder_reselection_required=True)
                result['confirmed_stages'] = empty_result['confirmed_stages']
            else:
                result.update(_empty_workspace=empty_result["_empty_workspace"], folder_reselection_required=True)
        conversation = target if target != "resources" else choice["origin_conversation_id"]
        if intent == "new_conversation":
            # An explicit separate history never changes a saved origin, even
            # when that origin is missing or has not yet been associated.
            conversation = None
        needs_association = created or (intent in {"open", "repair", "create"} and choice["origin_status"] == "unassociated") or intent == "repair"
        # Bind-to-current never changes a saved resource's canonical history.
        if intent in {"add", "new_conversation"}:
            needs_association = False
        if choice["origin_status"] == "repair_required" and target == "resources" and intent not in {"repair", "new_conversation"}:
            raise ClientPlatformError("origin_repair_required")
        if intent == "repair":
            if payload.get("expected_origin_id") != choice["origin_conversation_id"]:
                raise ClientPlatformError("resource_revision_conflict")
            if target == "resources":
                conversation = None
        if not choice["available"]:
            raise ClientPlatformError("resource_unavailable")
        result["setup_intent"] = intent
        result["association_required"] = needs_association
    def persist() -> None:
        admissions.command_progress(owner_id, key, result)
    persist()
    try:
        if validate:
            validate()
        if not conversation:
            conversation = str(uuid.uuid5(uuid.UUID(str(result["setup_command_id"])), "conversation"))
            if threads._thread_write_blocked(conversation):
                raise ClientPlatformError("conversation_deleting")
            if not threads._thread_exists(conversation):
                threads.create_thread(choice["name"], thread_id=conversation)
        service._metadata(conversation)
        result["conversation_id"] = conversation
        if "conversation" not in result["confirmed_stages"]:
            result["confirmed_stages"].append("conversation")
        persist()
        if needs_association:
            if validate:
                validate()
            choice = _associate(kind, identity, conversation, choice["revision"],
                                payload.get("expected_origin_id"), intent == "repair")
            result["resource_revision"] = choice["revision"]
            if "associated" not in result["confirmed_stages"]:
                result["confirmed_stages"].append("associated")
            persist()
        if validate:
            validate()
        current = list_bindings(conversation)
        if conversation == target and current.revision != command["expected_revision"]:
            raise ClientPlatformError("revision_conflict", current.revision)
        binding_identity, binding_revision = identity, choice["revision"]
        if kind == "workspace" and intent in {"open", "create"} and choice["origin_conversation_id"] == conversation:
            # A legacy project's conversation can execute in its own worktree.
            # Opening its saved history must preserve that accepted relationship.
            metadata = service._metadata(conversation)
            execution_id = metadata.get("developer_workspace_id")
            if metadata.get("project_workspace_id") == identity and execution_id and execution_id != identity:
                existing = next((item for item in current.bindings
                                 if item.kind == "workspace" and item.resource_id == execution_id), None)
                if existing is None:
                    raise ClientPlatformError("resource_binding_revoked")
                from row_bot.conversation_resources import describe
                descriptor = describe(existing)
                if not descriptor.available:
                    raise ClientPlatformError("resource_unavailable")
                binding_identity, binding_revision = execution_id, descriptor.resource_revision
        resources = bind(conversation, kind, binding_identity, expected_revision=current.revision,
                         expected_resource_revision=binding_revision)
        binding = next(item for item in resources.bindings if item.kind == kind and item.resource_id == binding_identity)
        result.update(binding_id=binding.binding_id, revision=resources.revision, status="completed")
        result["folder_reselection_required"] = False
        if "bound" not in result["confirmed_stages"]:
            result["confirmed_stages"].append("bound")
        persist()
        service.projection.publish(conversation, "resource.changed", {"revision": resources.revision})
        return result
    except Exception as exc:
        # A confirmed resource is retained and named in the receipt. No guessed
        # rollback or duplicate creation follows an uncertain later stage.
        result["status"] = "partial"
        code = getattr(exc, "code", str(exc))
        result["code"] = code if code in {"revision_conflict", "resource_revision_conflict", "origin_repair_required",
            "conversation_deleting", "resource_binding_revoked", "capability_revoked", "resource_unavailable",
            "resource_limit", "resource_ambiguous", "action_denied"} else "setup_stage_failed"
        persist()
        return result


def reconcile_setup_receipt(result: dict) -> dict:
    """Recover confirmed resource identity after a process/response boundary.

    Reading a receipt never creates, binds, associates or invokes a provider.
    Missing reserved IDs stay uncertain; only a saved domain record confirms a
    resource survived. Explicit continuation performs remaining stages.
    """
    if result.get("status") not in {"admitting", "partial"} or not result.get("setup_command_id") or not result.get("resource_id"):
        return result
    try:
        choice = resource_choice(result["resource_kind"], result["resource_id"])
    except (ValueError, OSError):
        return result
    stages = list(result.get("confirmed_stages") or [])
    if result.get("status") == "admitting" and result.get("association_required") and "created" not in stages:
        stages.append("created")
    if (result.get("association_required") and result.get("conversation_id")
            and choice["origin_status"] == "available"
            and choice["origin_conversation_id"] == result["conversation_id"] and "associated" not in stages):
        # Domain association may have committed just before progress was saved.
        # The exact durable origin confirms this stage without repeating a write.
        stages.append("associated")
    return {**result, "status": "partial", "resource_revision": choice["revision"],
            "confirmed_stages": stages, "code": result.get("code") or "setup_stage_failed"}


def conversation_workspace(service: Any, identity: str) -> dict:
    from row_bot import threads
    from row_bot.approval_policy import normalize_approval_mode
    from row_bot.agent_profiles import list_agent_profiles
    from row_bot.conversation_resources import list_bindings, describe
    from row_bot.providers.selection import parse_model_ref, model_choice_value
    from row_bot.models import get_current_model
    from row_bot.application.conversation_composer import read_conversation_composer
    row = service._metadata(identity)
    model = model_choice_value(row.get("model_override") or get_current_model())
    parsed = parse_model_ref(model)
    controls = {"model_selection": {"provider_id": parsed[0], "model_ref": model} if parsed else None,
                "runtime_mode": row.get("client_runtime_mode") or "agent", "profile_id": row.get("agent_profile_id") or "",
                "approval_mode": normalize_approval_mode(row.get("approval_mode"))}
    resources = []
    for binding in list_bindings(identity).bindings:
        if binding.kind not in {"artifact", "workspace"}:
            continue
        descriptor = describe(binding)
        resources.append({"resource_ref": identity + ":" + binding.binding_id, "conversation_revision": str(row["client_revision"]),
                          "binding": asdict(binding), "title": descriptor.title, "resource_revision": descriptor.resource_revision,
                          "available": descriptor.available})
    ready = generation_readiness(service, controls)
    from row_bot.application.context_status import read_usage
    from row_bot.application.reasoning_controls import reasoning_view
    return {"conversation_id": identity, "revision": str(row["client_revision"]), "controls": controls,
            "context_usage": read_usage(service, identity, controls),
            "reasoning": reasoning_view(identity, model),
            "composer": read_conversation_composer(
                identity,
                context={
                    "skills_override": row.get("skills_override"),
                    "agent_profile_id": str(row.get("agent_profile_id") or ""),
                    "agent_profile_slug": str(row.get("agent_profile_slug") or ""),
                    "client_revision": int(row["client_revision"]),
                },
            ),
            "profiles": [{"id": p["id"], "label": p["display_name"]} for p in list_agent_profiles(enabled_only=True)][:256],
            "resources": resources, "actions": [
                {"action": action, "ready": ready, "code": None if ready else "model_configuration_required"}
                for action in ("send", "generate")] + [
                {"action": action, "ready": not threads._thread_write_blocked(identity), "code": None}
                for action in ("create_deck", "bind", "preview") ]}


def generation_readiness(service: Any, controls: dict) -> bool:
    if service.readiness_factory is not None:
        return bool(service.readiness_factory(controls))
    if not controls.get("model_selection"):
        return False
    from row_bot.providers.readiness import evaluate_runtime_readiness
    try:
        result = evaluate_runtime_readiness(controls["model_selection"]["model_ref"],
                                           refresh_provider_status=False, probe_ollama_tools=False)
        return bool(result.chat.ready if controls["runtime_mode"] == "chat_only" else result.agent.ready)
    except (ValueError, RuntimeError):
        return False
