"""NiceGUI semantic adapter for the canonical landing story."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from scripts.marketing.capture_contract import SceneSpec
from scripts.marketing.capture_run import require_contained
from scripts.marketing.clients.base import (
    ClientAdapter,
    ClientAdapterError,
    SceneCapture,
)


class NiceGuiAdapter(ClientAdapter):
    """Own all current-client routes, selectors, and semantic waits."""

    name = "nicegui"
    APP_READY = "body"
    CHAT_INPUT = (
        '[data-docs-id="chat-input"] textarea, '
        'textarea[data-docs-id="chat-input"], '
        ".row-bot-desktop-composer textarea"
    )
    MODEL_PICKER = '[data-docs-id="chat-model-picker"]'
    ACTIVITY_RAIL = '[data-workflow-console-rail="1"]'
    APPROVAL_DIALOG = '[data-docs-id="approval-dialog"]'
    SENSITIVE_MASKS = (
        "[data-sensitive]",
        ".q-notification",
        "[data-row-bot-secret]",
    )

    @classmethod
    def install_capture_privacy_filter(
        cls,
        context: Any,
        records: dict[str, str],
    ) -> None:
        """Prevent hard-sensitive UI from flashing into recorded video frames."""

        del records
        css = (
            ".q-notification,[data-sensitive],[data-row-bot-secret]"
            "{display:none!important}"
        )
        context.add_init_script(
            script=(
                "(() => {"
                f"const css={json.dumps(css)};"
                "const install=()=>{"
                " if(document.getElementById('row-bot-capture-privacy'))return;"
                " const style=document.createElement('style');"
                " style.id='row-bot-capture-privacy'; style.textContent=css;"
                " (document.head||document.documentElement).appendChild(style);"
                "}; install();"
                "if(!document.head)new MutationObserver(install).observe(document.documentElement,{childList:true});"
                "})();"
            )
        )

    def _goto(self, **query: str) -> None:
        selected = {key: value for key, value in query.items() if value}
        url = self.base_url + "/"
        if selected:
            url += "?" + urlencode(selected)
        self.page.goto(url, wait_until="networkidle", timeout=45_000)
        self.page.wait_for_selector(self.APP_READY, timeout=30_000)

    def open_conversation(self, record_key: str) -> None:
        self._goto(docs_surface="chat-main", thread_id=self.record_id(record_key))
        self.page.wait_for_selector('[data-docs-id="chat-composer"]', timeout=30_000)

    def open_home_surface(self, name: str) -> None:
        labels = {
            "home": "Workflows",
            "knowledge": "Knowledge",
            "workflow": "Workflows",
            "designer": "Designer",
            "monitor": "Monitor",
        }
        try:
            tab = labels[name]
        except KeyError as exc:
            raise ClientAdapterError(
                f"unsupported NiceGUI home surface: {name}"
            ) from exc
        self._goto(home_tab=tab)
        self.page.wait_for_selector(
            f'[data-docs-id="home-panel-{tab.casefold()}"]',
            timeout=30_000,
        )

    def open_model_picker(self) -> None:
        picker = self.page.locator(self.MODEL_PICKER).first
        picker.click(timeout=15_000)
        self.page.wait_for_selector(
            ".q-menu:visible .q-item", state="visible", timeout=15_000
        )

    @staticmethod
    def model_option_label(model_ref: str, *, surface: str = "chat") -> str:
        """Resolve one provider-qualified picker label from the real catalog."""

        from row_bot.providers.selection import list_model_choice_options

        matches = [
            option
            for option in list_model_choice_options(surface, include_inactive=True)
            if str(option.get("value") or "") == model_ref
        ]
        if len(matches) != 1:
            raise ClientAdapterError(
                f"expected one configured picker option for {model_ref}; found {len(matches)}"
            )
        if matches[0].get("active") is False:
            raise ClientAdapterError(
                f"configured picker option is inactive: {model_ref}"
            )
        return str(matches[0].get("label") or "")

    @classmethod
    def chat_picker_label(cls, model_ref: str) -> str:
        """Return the visible chat label, including the exact current default."""

        from row_bot.models import get_current_model
        from row_bot.providers.selection import (
            model_choice_value,
            model_id_from_choice_value,
        )

        if model_choice_value(get_current_model()) == model_ref:
            return f"Default - {model_id_from_choice_value(model_ref)}"
        return cls.model_option_label(model_ref)

    def _reveal_virtual_option(self, label: str) -> Any:
        """Scroll one open Quasar virtual menu until *label* is rendered."""

        option = self.page.locator(".q-menu:visible .q-item").filter(has_text=label)
        if option.count():
            return option
        menu = self.page.locator(".q-menu:visible").last
        seen_labels: set[str] = set()
        metrics = menu.evaluate(
            "el => ({height: el.clientHeight, scrollHeight: el.scrollHeight})"
        )
        height = max(int(metrics.get("height") or 0), 120)
        scroll_height = max(int(metrics.get("scrollHeight") or 0), height)
        step = max(height // 2, 120)
        for position in range(0, scroll_height + step, step):
            menu.evaluate(
                "(el, top) => { el.scrollTop = top; "
                "el.dispatchEvent(new Event('scroll', {bubbles: true})); }",
                position,
            )
            self.page.evaluate(
                "() => new Promise(resolve => requestAnimationFrame("
                "() => requestAnimationFrame(resolve)))"
            )
            seen_labels.update(
                self.page.locator(".q-menu:visible .q-item").all_inner_texts()
            )
            if option.count():
                return option
        self._last_virtual_labels = tuple(sorted(seen_labels))
        return option

    def reveal_chat_model_option(self, model_ref: str) -> tuple[Any, str]:
        """Move Quasar's virtual menu to an exact provider-qualified option."""

        label = self.chat_picker_label(model_ref)
        option = self._reveal_virtual_option(label)
        if not option.count():
            menu = self.page.locator(".q-menu:visible").last
            scrollables = menu.evaluate(
                "el => [el, ...el.querySelectorAll('*')].map(node => ({"
                "tag: node.tagName, cls: node.className || '', "
                "height: node.clientHeight, scrollHeight: node.scrollHeight"
                "})).filter(item => item.scrollHeight > item.height)"
            )
            raise ClientAdapterError(
                f"provider-qualified option was not rendered for {model_ref}; "
                f"matching labels={tuple(label for label in self._last_virtual_labels if 'gpt-5.6' in label.casefold())!r}; "
                f"scrollables={scrollables!r}"
            )
        option.first.wait_for(state="visible", timeout=30_000)
        if option.count() != 1:
            raise ClientAdapterError(
                f"provider-qualified model option is not unique in NiceGUI: {model_ref}"
            )
        return option, label

    def select_model(self, model_ref: str) -> None:
        self.open_model_picker()
        option, label = self.reveal_chat_model_option(model_ref)
        option.click(timeout=15_000)
        self.page.wait_for_function(
            "([selector, value]) => { const el=document.querySelector(selector); return el && (el.textContent||'').includes(value); }",
            arg=[self.MODEL_PICKER, label],
            timeout=15_000,
        )

    def open_activity(self) -> None:
        content = self.page.locator(".workflow-console-content:visible")
        if content.count():
            return
        rail = self.page.locator(f"{self.ACTIVITY_RAIL}:visible").last
        if rail.count():
            rail.click(timeout=15_000)
        else:
            toggle = self.page.locator(
                'button[aria-label="Toggle Activity Center"]:visible'
            ).last
            if not toggle.count():
                raise ClientAdapterError("no visible Activity Center control")
            toggle.click(timeout=15_000)
        self.page.locator(".workflow-console-content:visible").wait_for(
            state="visible", timeout=15_000
        )

    def open_workflow(self, record_key: str) -> None:
        self._goto(
            home_tab="Workflows",
            dialog="workflow-editor",
            workflow_id=self.record_id(record_key),
        )
        self.page.wait_for_selector(
            '[data-docs-id="workflow-editor"]', state="visible", timeout=30_000
        )

    def open_designer_project(self, record_key: str) -> None:
        self._goto(
            docs_surface="designer-editor",
            project_id=self.record_id(record_key),
        )
        self.page.wait_for_selector('[data-docs-id="designer-editor"]', timeout=30_000)

    def show_capture_knowledge_subset(self) -> None:
        entry_ids = sorted(
            str(value)
            for key, value in self.records.items()
            if key.startswith("knowledge-entry-") and value
        )
        if not entry_ids:
            raise ClientAdapterError("no capture-owned knowledge entries were recorded")
        self.page.wait_for_function(
            "ids => { const g=window._rowBotGraph; return !!g && "
            "ids.every(id => g.allNodes.some(node => String(node.id) === id)) && "
            "g.allEdges.some(edge => ids.includes(String(edge.from)) && "
            "ids.includes(String(edge.to))); }",
            arg=entry_ids,
            timeout=30_000,
        )
        shown = self.page.evaluate(
            "ids => {"
            " const g=window._rowBotGraph; const wanted=new Set(ids);"
            " const selected=g.allNodes.filter(node => wanted.has(String(node.id)));"
            " const mobile=window.innerWidth <= 600;"
            " const wrap=label => {"
            "   const words=String(label || '').split(/\\s+/); let lines=[''];"
            "   words.forEach(word => {"
            "     const last=lines.length-1; const next=(lines[last]+' '+word).trim();"
            "     if (next.length > (mobile ? 18 : 24) && lines[last]) lines.push(word);"
            "     else lines[last]=next;"
            "   }); return lines.join('\\n');"
            " };"
            " const center=selected.find(node => /decision framework/i.test(node.label)) || selected[0];"
            " const outer=selected.filter(node => node !== center);"
            " const radiusX=mobile ? 115 : 330; const radiusY=mobile ? 210 : 230;"
            " const nodes=selected.map(node => {"
            "   if (node === center) return Object.assign({},node,{label:wrap(node.label),"
            "     shape:'dot',size:34,x:0,y:0,fixed:{x:true,y:true},"
            "     font:{color:'#ECEFF1',size:mobile?11:15,face:'system-ui'}});"
            "   const index=outer.indexOf(node); const angle=(-Math.PI/2)+(index*2*Math.PI/outer.length);"
            "   return Object.assign({},node,{label:wrap(node.label),shape:'dot',size:20,"
            "     x:Math.cos(angle)*radiusX,y:Math.sin(angle)*radiusY,fixed:{x:true,y:true},"
            "     font:{color:'#ECEFF1',size:mobile?9:13,face:'system-ui'}});"
            " });"
            " const visible=new Set(nodes.map(node => node.id));"
            " const edges=g.allEdges.filter(edge => visible.has(edge.from) && visible.has(edge.to))"
            "   .map(edge => Object.assign({},edge,{width:2,"
            "     label:String(edge.label || '').replaceAll('_',' '),"
            "     font:{color:'#B0BEC5',strokeColor:'#121212',size:mobile?8:10}}));"
            " if (!edges.length) return -1;"
            " g.currentNodes=nodes; g.currentEdges=edges; g.isFullGraph=false;"
            " g.updateStatsLabel(nodes.length, edges.length);"
            " g.createNetwork(nodes, edges, null);"
            " if (g.network) {"
            "   g.network.setOptions({physics:false}); g.network.fit({animation:false});"
            " }"
            " const search=document.getElementById('graph-search');"
            " if (search) { search.value='Public-safe capture knowledge'; search.readOnly=true; }"
            " const userToggle=document.getElementById('graph-user-toggle');"
            " if (userToggle) { userToggle.checked=false; userToggle.disabled=true; }"
            " const sourceFilters=document.getElementById('graph-source-filters');"
            " if (sourceFilters) sourceFilters.hidden=true;"
            " const showAll=document.getElementById('graph-full-toggle');"
            " if (showAll) showAll.hidden=true;"
            " return nodes.length;"
            "}",
            entry_ids,
        )
        if shown != len(entry_ids):
            raise ClientAdapterError(
                "capture-owned connected knowledge subgraph was incomplete"
            )

    def open_approval(self, record_key: str | None = None) -> None:
        if record_key:
            self.open_conversation(record_key)
        self.open_activity()
        self.page.locator(
            ".row-bot-approvals-card:visible .q-card:visible"
        ).first.wait_for(state="visible", timeout=30_000)

    def await_settled_state(self, scene: SceneSpec) -> None:
        if scene.state == "pending":
            if self.page.locator(self.APPROVAL_DIALOG).count():
                self.page.wait_for_selector(
                    self.APPROVAL_DIALOG, state="visible", timeout=30_000
                )
            else:
                self.page.locator(
                    ".row-bot-approvals-card:visible .q-card:visible"
                ).first.wait_for(state="visible", timeout=30_000)
            return
        self.page.wait_for_function(
            "() => !document.querySelector('.row-bot-composer-stop-button:not([disabled])') && !document.querySelector('.q-spinner')",
            timeout=45_000,
        )
        self.page.wait_for_load_state("networkidle", timeout=30_000)

    def prepare_scene(self, scene: SceneSpec) -> None:
        if scene.surface == "conversation":
            assert scene.record is not None
            self.open_conversation(scene.record)
            if scene.state == "sources-open":
                trace = self.page.locator('[data-docs-id="tool-trace"]').first
                if trace.count():
                    trace.click(timeout=10_000)
        elif scene.surface == "knowledge":
            self.open_home_surface("knowledge")
            self.show_capture_knowledge_subset()
        elif scene.surface == "designer":
            assert scene.record is not None
            self.open_designer_project(scene.record)
        elif scene.surface == "workflow":
            assert scene.record is not None
            self.open_workflow(scene.record)
        elif scene.surface == "approval":
            self.open_approval(
                "approval-boundary" if "approval-boundary" in self.records else None
            )
        elif scene.surface == "model-picker":
            self.open_conversation("campaign-narrative")
            self.open_model_picker()
        elif scene.surface == "home":
            self.open_home_surface(
                "knowledge" if scene.state == "knowledge-open" else "home"
            )
            if scene.state == "knowledge-open":
                self.show_capture_knowledge_subset()
        else:
            raise ClientAdapterError(
                f"unsupported NiceGUI scene surface: {scene.surface}"
            )
        self.await_settled_state(scene)

    def capture_scene(self, scene: SceneSpec, output_dir: Path) -> SceneCapture:
        output_dir.mkdir(parents=True, exist_ok=True)
        target = require_contained(output_dir / f"{scene.id}.png", output_dir)
        masks = []
        used_masks: list[str] = []
        for selector in self.SENSITIVE_MASKS:
            locator = self.page.locator(selector)
            if locator.count():
                masks.append(locator)
                used_masks.append(selector)
        self.page.evaluate(
            "() => { window.scrollTo(0, 0); document.documentElement.scrollTop=0; document.body.scrollTop=0; }"
        )
        self.page.screenshot(
            path=str(target),
            animations="disabled",
            full_page=False,
            mask=masks,
        )
        viewport = self.page.viewport_size or {"width": 0, "height": 0}
        return SceneCapture(
            scene_id=scene.id,
            image_path=target,
            video_path=None,
            width=int(viewport["width"]),
            height=int(viewport["height"]),
            captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            masked_selectors=tuple(used_masks),
        )

    def create_conversation(self, *, title: str, model_ref: str, prompt: str) -> None:
        self.prepare_conversation(title=title, model_ref=model_ref)
        self.send_prepared_prompt(prompt)

    def prepare_conversation(self, *, title: str, model_ref: str) -> None:
        self._goto()
        self.page.get_by_role("button", name="＋ New").click(timeout=20_000)
        self.page.wait_for_selector(self.CHAT_INPUT, timeout=30_000)
        self.rename_active_conversation(title)
        self.select_model(model_ref)

    def send_prepared_prompt(self, prompt: str) -> None:
        textarea = self.page.locator(self.CHAT_INPUT).first
        textarea.fill(prompt)
        textarea.press("Enter")
        self.page.wait_for_selector(
            ".row-bot-composer-stop-button:not([disabled])",
            state="visible",
            timeout=30_000,
        )
        self.page.wait_for_selector(
            ".row-bot-composer-stop-button:not([disabled])",
            state="hidden",
            timeout=600_000,
        )
        blocked = self.page.get_by_text("is not chat-ready", exact=False)
        if blocked.count() and blocked.last.is_visible():
            raise ClientAdapterError(
                "generation ended at the provider readiness boundary"
            )

    def create_empty_conversation(self, *, title: str) -> None:
        self._goto()
        self.page.get_by_role("button", name="＋ New").click(timeout=20_000)
        self.page.wait_for_selector(self.CHAT_INPUT, timeout=30_000)
        self.rename_active_conversation(title)

    def create_designer_project(
        self,
        *,
        title: str,
        model_ref: str,
        prompt: str,
    ) -> None:
        self.prepare_designer_project(title=title, model_ref=model_ref)
        self.send_prepared_prompt(prompt)

    def prepare_designer_project(self, *, title: str, model_ref: str) -> None:
        self.open_home_surface("designer")
        self.page.get_by_role("button", name="New Design").click(timeout=20_000)
        self.page.get_by_text("New Design", exact=True).wait_for(
            state="visible", timeout=20_000
        )
        self.page.get_by_text("Landing page", exact=True).click(timeout=15_000)
        self.page.get_by_label("Project name").fill(title)
        self.page.get_by_role("button", name="Create", exact=True).click(timeout=20_000)
        self.page.wait_for_selector('[data-docs-id="designer-editor"]', timeout=30_000)
        self.select_model(model_ref)

    def create_workflow(
        self,
        *,
        title: str,
        model_ref: str,
        prompt: str,
    ) -> None:
        self.open_home_surface("workflow")
        self.page.get_by_role("button", name="New Workflow").click(timeout=20_000)
        self.page.get_by_text("New Workflow", exact=True).wait_for(
            state="visible", timeout=20_000
        )
        self.page.get_by_label("Name *").fill(title)
        self.page.get_by_label("Description (optional)").fill(
            "Public-source weekly local-first AI intelligence. Delivery disabled."
        )
        self.page.get_by_label("Step 1").fill(prompt)
        label = self.model_option_label(model_ref)
        model_picker = self.page.get_by_label("Model", exact=True)
        model_picker.click(timeout=15_000)
        option = self._reveal_virtual_option(label)
        option.first.wait_for(state="visible", timeout=30_000)
        if option.count() != 1:
            raise ClientAdapterError(
                f"provider-qualified workflow model is not unique: {model_ref}"
            )
        option.click(timeout=15_000)
        enabled = (
            self.page.get_by_text("Enabled", exact=True).locator("..").locator("input")
        )
        if enabled.count() and enabled.is_checked():
            enabled.uncheck()
        self.page.get_by_role("button", name="Save", exact=True).click(timeout=20_000)
        self.page.get_by_text("Task created", exact=False).wait_for(
            state="visible", timeout=20_000
        )

    def run_workflow(self, title: str) -> None:
        self.open_home_surface("workflow")
        card = self.page.locator(".q-card").filter(
            has=self.page.get_by_text(title, exact=True)
        )
        if card.count() != 1:
            raise ClientAdapterError(
                f"expected one workflow card for {title}; found {card.count()}"
            )
        switch = card.get_by_role("switch")
        if switch.count() and switch.get_attribute("aria-checked") != "true":
            switch.click(timeout=10_000)
            self.page.get_by_text(title, exact=True).wait_for(
                state="visible", timeout=20_000
            )
        card.locator('[data-docs-id="workflow-run"]').click(timeout=20_000)
        self.page.get_by_text(f"{title} started", exact=False).wait_for(
            state="visible", timeout=20_000
        )

    def rename_active_conversation(self, title: str) -> None:
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                active = self.page.locator(
                    "[data-thread-id][active], [data-thread-id].q-item--active"
                ).first
                if not active.count():
                    raise ClientAdapterError(
                        "active conversation row is unavailable for rename"
                    )
                active.get_by_role("button").last.click(timeout=10_000)
                self.page.locator(".q-menu:visible").get_by_text(
                    "Rename", exact=True
                ).click(timeout=10_000)
                dialog = (
                    self.page.get_by_role("dialog")
                    .filter(has_text="Rename conversation")
                    .first
                )
                dialog.wait_for(state="visible", timeout=10_000)
                dialog.locator("input").first.fill(title, timeout=10_000)
                dialog.get_by_role("button", name="Save").click(timeout=10_000)
                self.page.get_by_text(title, exact=True).first.wait_for(
                    state="visible", timeout=15_000
                )
                return
            except Exception as exc:
                last_error = exc
                self.page.keyboard.press("Escape")
        raise ClientAdapterError(
            f"active conversation could not be renamed after UI rebuilds: {last_error}"
        )
