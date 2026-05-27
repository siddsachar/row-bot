"""Shared in-app editor for knowledge graph entities."""

from __future__ import annotations

from collections.abc import Callable
import logging

from nicegui import ui

logger = logging.getLogger(__name__)


def open_entity_editor(
    entity_id: str,
    *,
    on_saved: Callable[[], None] | None = None,
) -> None:
    """Open a modal dialog to edit and audit a knowledge graph entity."""
    import knowledge_graph as kg
    import memory_evolution
    from ui import knowledge_audit as audit

    entity = kg.get_entity(entity_id)
    if not entity:
        ui.notify(f"Entity {entity_id} not found.", type="warning")
        return

    entity_ref = {"value": entity}
    entity_types = sorted(kg.VALID_ENTITY_TYPES)
    relation_types = sorted(kg.VALID_RELATION_TYPES)

    def _notify_saved() -> None:
        if on_saved:
            on_saved()

    with ui.dialog().props("persistent") as dlg, ui.card().classes("w-full").style(
        "min-width: 620px; max-width: 880px; max-height: 90vh; overflow-y: auto;"
    ):
        ui.label("Edit Entity").classes("text-h6")

        subject_input = ui.input(
            "Subject",
            value=entity.get("subject", ""),
            validation={"Required": lambda v: bool(v.strip())},
        ).classes("w-full")

        type_select = ui.select(
            label="Entity Type",
            options=entity_types,
            value=entity.get("entity_type", "fact"),
        ).classes("w-full")

        desc_input = ui.textarea(
            "Description",
            value=entity.get("description", ""),
        ).classes("w-full").props('rows="5"')

        aliases_input = ui.input(
            "Aliases (comma-separated)",
            value=entity.get("aliases", ""),
        ).classes("w-full")

        tags_input = ui.input(
            "Tags (comma-separated)",
            value=entity.get("tags", ""),
        ).classes("w-full")

        ui.separator()
        audit_container = ui.column().classes("w-full gap-2")

        def _reload_entity() -> dict | None:
            fresh = kg.get_entity(entity_id)
            if fresh:
                entity_ref["value"] = fresh
            return fresh

        def _refresh_audit() -> None:
            audit_container.clear()
            current = entity_ref["value"]
            summary = audit.audit_summary(current)
            with audit_container:
                with ui.expansion("Audit and Provenance", icon="manage_search", value=True).classes("w-full"):
                    with ui.row().classes("gap-2 q-mb-xs"):
                        ui.badge(summary["status_label"]).props(
                            f"color={summary.get('status_color', 'blue-grey')} outline"
                        )
                        ui.badge(summary["tier_label"]).props("color=blue-grey outline")
                        ui.badge(summary["source_bucket"]).props("color=blue-grey outline")
                        if summary.get("confidence_label"):
                            ui.badge(summary["confidence_label"]).props("color=blue-grey outline")

                    lines = [
                        f"Source: {summary['source_label']}",
                        f"Created: {current.get('created_at', '')[:16]}",
                        f"Updated: {current.get('updated_at', '')[:16]}",
                    ]
                    if summary.get("last_user_modified_at"):
                        lines.append(f"User modified: {summary['last_user_modified_at'][:16]}")
                    if summary.get("last_evolved_at"):
                        lines.append(f"Evolved: {summary['last_evolved_at'][:16]}")
                    if summary.get("recalled_at"):
                        lines.append(f"Recalled: {summary['recalled_at'][:16]}")
                    if summary.get("recall_count") not in ("", None):
                        lines.append(f"Recall count: {summary['recall_count']}")
                    if summary.get("review_reason"):
                        lines.append(f"Review: {summary['review_reason']}")
                    if summary.get("superseded_by"):
                        lines.append(f"Superseded by: {summary['superseded_by']}")
                    if summary.get("supersedes"):
                        lines.append(f"Supersedes: {', '.join(summary['supersedes'][:4])}")

                    for line in lines:
                        ui.label(line).classes("text-xs text-grey-6")
                    for line in summary.get("source_context_lines", []):
                        ui.label(line).classes("text-xs text-grey-6")
                    if summary.get("evidence"):
                        ui.label(f"Evidence: {summary.get('evidence_count', len(summary['evidence']))} item(s)").classes("text-xs text-grey-6 q-mt-xs")
                        for item in summary["evidence"]:
                            ui.label(item).classes("text-xs text-grey-6")

                    with ui.row().classes("gap-2 q-mt-sm"):
                        if summary["status"] == "archived":
                            ui.button(
                                "Restore",
                                icon="unarchive",
                                on_click=lambda: _set_manual_active("Memory restored."),
                            ).props("flat dense color=positive no-caps")
                        else:
                            ui.button(
                                "Archive",
                                icon="archive",
                                on_click=_archive_current,
                            ).props("flat dense color=grey no-caps")
                        if summary["status"] == "needs_review":
                            ui.button(
                                "Resolve Review",
                                icon="check",
                                on_click=lambda: _set_manual_active("Review resolved."),
                            ).props("flat dense color=positive no-caps")

                    with ui.row().classes("w-full gap-2 q-mt-sm"):
                        supersede_input = ui.input(
                            "Supersede with entity ID",
                            placeholder="Replacement memory ID",
                        ).classes("col")

                        def _supersede() -> None:
                            new_id = (supersede_input.value or "").strip()
                            if not new_id:
                                ui.notify("Enter the replacement entity ID.", type="warning")
                                return
                            if new_id == entity_id:
                                ui.notify("An entity cannot supersede itself.", type="warning")
                                return
                            if not kg.get_entity(new_id):
                                ui.notify("Replacement entity not found.", type="warning")
                                return
                            old, _new = memory_evolution.mark_superseded(
                                entity_id,
                                new_id,
                                reason="Superseded from entity editor",
                                actor="manual",
                            )
                            if old:
                                _reload_entity()
                                _refresh_audit()
                                _notify_saved()
                                ui.notify("Memory superseded.", type="positive")
                            else:
                                ui.notify("Supersede failed.", type="negative")

                        ui.button("Supersede", icon="change_circle", on_click=_supersede).props(
                            "flat dense color=warning no-caps"
                        )

        def _archive_current() -> None:
            updated = memory_evolution.set_status(
                entity_id,
                "archived",
                actor="manual",
                reason="Archived from entity editor",
            )
            if updated:
                entity_ref["value"] = updated
                _refresh_audit()
                _notify_saved()
                ui.notify("Memory archived.", type="info")
            else:
                ui.notify("Archive failed.", type="negative")

        def _set_manual_active(message: str) -> None:
            updated = memory_evolution.mark_user_modified(
                entity_id,
                actor="manual",
                source_context={"actor": "manual", "surface": "entity_editor"},
                status="active",
            )
            if updated:
                entity_ref["value"] = updated
                _refresh_audit()
                _notify_saved()
                ui.notify(message, type="positive")
            else:
                ui.notify("Update failed.", type="negative")

        _refresh_audit()

        ui.separator()
        ui.label("Relations").classes("text-subtitle2 font-bold")
        rels_container = ui.column().classes("w-full gap-1")

        def _refresh_relations() -> None:
            rels_container.clear()
            rels = kg.get_relations(entity_id)
            with rels_container:
                if not rels:
                    ui.label("No relations.").classes("text-grey-6 text-sm")
                else:
                    for rel in rels:
                        arrow = "->" if rel["direction"] == "outgoing" else "<-"
                        label_text = f"{arrow} {rel['relation_type']}  {rel['peer_subject']}"
                        with ui.row().classes("w-full items-center gap-2"):
                            ui.label(label_text).classes("text-sm flex-grow").style("color: #bbb;")
                            conf = rel.get("confidence", 1.0)
                            if conf < 1.0:
                                ui.label(f"{conf:.0%}").classes("text-xs text-grey-6")

                            def _del_rel(rid=rel["id"]) -> None:
                                kg.delete_relation(rid)
                                ui.notify("Relation removed.", type="info")
                                _refresh_relations()
                                _notify_saved()

                            ui.button(
                                icon="close", on_click=_del_rel
                            ).props("flat dense round size=xs color=negative")

        _refresh_relations()

        with ui.expansion("Add Relation", icon="add").classes("w-full"):
            all_entities = kg.list_entities(limit=500)
            peer_options = {
                e["id"]: e["subject"]
                for e in all_entities
                if e["id"] != entity_id
            }

            peer_select = ui.select(
                label="Target entity",
                options=peer_options,
                with_input=True,
            ).classes("w-full")

            rel_type_select = ui.select(
                label="Relation type",
                options=relation_types,
                with_input=True,
                value="knows",
            ).classes("w-full")

            dir_select = ui.select(
                label="Direction",
                options=["outgoing (this -> target)", "incoming (target -> this)"],
                value="outgoing (this -> target)",
            ).classes("w-full")

            def _add_relation() -> None:
                peer_id = peer_select.value
                rtype = rel_type_select.value
                if not peer_id or not rtype:
                    ui.notify("Select a target entity and relation type.", type="warning")
                    return
                if "outgoing" in dir_select.value:
                    src, tgt = entity_id, peer_id
                else:
                    src, tgt = peer_id, entity_id
                result = kg.add_relation(src, tgt, rtype)
                if result:
                    ui.notify(f"Relation added: {rtype}", type="positive")
                    peer_select.value = None
                    _refresh_relations()
                    _notify_saved()
                else:
                    ui.notify("Failed to add relation.", type="negative")

            ui.button("Add", icon="add", on_click=_add_relation).props(
                "flat dense color=primary"
            )

        with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
            ui.button("Cancel", on_click=dlg.close).props("flat")

            def _save() -> None:
                subj = (subject_input.value or "").strip()
                if not subj:
                    ui.notify("Subject is required.", type="warning")
                    return
                desc = (desc_input.value or "").strip()
                updated = kg.update_entity(
                    entity_id,
                    description=desc,
                    subject=subj,
                    entity_type=type_select.value,
                    aliases=(aliases_input.value or "").strip(),
                    tags=(tags_input.value or "").strip(),
                )
                if not updated:
                    ui.notify("Update failed.", type="negative")
                    return
                marked = memory_evolution.mark_user_modified(
                    entity_id,
                    actor="manual",
                    source_context={"actor": "manual", "surface": "entity_editor", "action": "save"},
                    status="active",
                )
                entity_ref["value"] = marked or updated
                ui.notify(f"'{subj}' updated.", type="positive")
                _notify_saved()
                dlg.close()

            ui.button("Save", icon="save", on_click=_save).props("color=primary")

    dlg.open()

