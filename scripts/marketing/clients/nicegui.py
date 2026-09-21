"""NiceGUI semantic adapter for the canonical landing story."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from scripts.marketing.capture_contract import SceneSpec
from scripts.marketing.capture_run import require_contained
from scripts.marketing.clients.base import ClientAdapter, ClientAdapterError, SceneCapture


class NiceGuiAdapter(ClientAdapter):
    """Own all current-client routes, selectors, and semantic waits."""

    name = "nicegui"
    APP_READY = "body"
    CHAT_INPUT = '[data-docs-id="chat-input"] textarea'
    MODEL_PICKER = '[data-docs-id="chat-model-picker"]'
    ACTIVITY_RAIL = '[data-workflow-console-rail="1"]'
    APPROVAL_DIALOG = '[data-docs-id="approval-dialog"]'
    SENSITIVE_MASKS = (
        "[data-sensitive]",
        ".q-notification",
        "[data-row-bot-secret]",
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
            raise ClientAdapterError(f"unsupported NiceGUI home surface: {name}") from exc
        self._goto(home_tab=tab)
        self.page.wait_for_selector(
            f'[data-docs-id="home-panel-{tab.casefold()}"]',
            timeout=30_000,
        )

    def open_model_picker(self) -> None:
        picker = self.page.locator(self.MODEL_PICKER).first
        picker.click(timeout=15_000)
        self.page.wait_for_selector(".q-menu .q-item", state="visible", timeout=15_000)

    def select_model(self, model_ref: str) -> None:
        self.open_model_picker()
        model_id = model_ref.split(":", 2)[-1]
        option = self.page.locator(".q-menu .q-item").filter(has_text=model_id).first
        option.click(timeout=15_000)
        self.page.wait_for_function(
            "([selector, value]) => { const el=document.querySelector(selector); return el && (el.getAttribute('aria-label')||'').includes(value); }",
            arg=[self.MODEL_PICKER, model_id],
            timeout=15_000,
        )

    def open_activity(self) -> None:
        rail = self.page.locator(self.ACTIVITY_RAIL).first
        if rail.count():
            rail.click(timeout=15_000)
        self.page.wait_for_function(
            "() => !!document.querySelector('[data-workflow-console-drawer=\"1\"]')",
            timeout=15_000,
        )

    def open_workflow(self, record_key: str) -> None:
        self._goto(
            home_tab="Workflows",
            dialog="workflow-editor",
            workflow_id=self.record_id(record_key),
        )
        self.page.wait_for_selector('[data-docs-id="workflow-editor"]', state="visible", timeout=30_000)

    def open_designer_project(self, record_key: str) -> None:
        self._goto(
            docs_surface="designer-editor",
            project_id=self.record_id(record_key),
        )
        self.page.wait_for_selector('[data-docs-id="designer-editor"]', timeout=30_000)

    def open_approval(self, record_key: str | None = None) -> None:
        if record_key:
            self.open_conversation(record_key)
        self.open_activity()
        self.page.get_by_text("Approval", exact=False).first.wait_for(
            state="visible", timeout=30_000
        )

    def await_settled_state(self, scene: SceneSpec) -> None:
        if scene.state == "pending":
            if self.page.locator(self.APPROVAL_DIALOG).count():
                self.page.wait_for_selector(self.APPROVAL_DIALOG, state="visible", timeout=30_000)
            else:
                self.page.get_by_text("Approval", exact=False).first.wait_for(
                    state="visible", timeout=30_000
                )
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
        elif scene.surface == "designer":
            assert scene.record is not None
            self.open_designer_project(scene.record)
        elif scene.surface == "workflow":
            assert scene.record is not None
            self.open_workflow(scene.record)
        elif scene.surface == "approval":
            self.open_approval("approval-boundary" if "approval-boundary" in self.records else None)
        elif scene.surface == "model-picker":
            self.open_conversation("campaign-narrative")
            self.open_model_picker()
        elif scene.surface == "home":
            self.open_home_surface("knowledge" if scene.state == "knowledge-open" else "home")
        else:
            raise ClientAdapterError(f"unsupported NiceGUI scene surface: {scene.surface}")
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
        self._goto()
        self.page.get_by_role("button", name="＋ New").click(timeout=20_000)
        self.page.wait_for_selector(self.CHAT_INPUT, timeout=30_000)
        self.select_model(model_ref)
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
        self.rename_active_conversation(title)

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
        self.open_home_surface("designer")
        self.page.get_by_role("button", name="New Design").click(timeout=20_000)
        self.page.get_by_text("New Design", exact=True).wait_for(state="visible", timeout=20_000)
        self.page.get_by_text("Landing page", exact=True).click(timeout=15_000)
        self.page.get_by_label("Project name").fill(title)
        self.page.get_by_role("button", name="Create", exact=True).click(timeout=20_000)
        self.page.wait_for_selector('[data-docs-id="designer-editor"]', timeout=30_000)
        self.select_model(model_ref)
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

    def create_workflow(
        self,
        *,
        title: str,
        model_ref: str,
        prompt: str,
    ) -> None:
        self.open_home_surface("workflow")
        self.page.get_by_role("button", name="New Workflow").click(timeout=20_000)
        self.page.get_by_text("New Workflow", exact=True).wait_for(state="visible", timeout=20_000)
        self.page.get_by_label("Name *").fill(title)
        self.page.get_by_label("Description (optional)").fill(
            "Public-source weekly local-first AI intelligence. Delivery disabled."
        )
        self.page.get_by_label("Step 1").fill(prompt)
        model_id = model_ref.split(":", 2)[-1]
        model_picker = self.page.get_by_label("Model override")
        model_picker.click(timeout=15_000)
        self.page.locator(".q-menu .q-item").filter(has_text=model_id).first.click(timeout=15_000)
        enabled = self.page.get_by_text("Enabled", exact=True).locator("..").locator("input")
        if enabled.count() and enabled.is_checked():
            enabled.uncheck()
        self.page.get_by_role("button", name="Save", exact=True).click(timeout=20_000)
        self.page.get_by_text("Task created", exact=False).wait_for(state="visible", timeout=20_000)

    def run_workflow(self, title: str) -> None:
        self.open_home_surface("workflow")
        card = self.page.get_by_text(title, exact=True).locator("..").locator("..")
        switch = card.locator('[role="switch"]')
        if switch.count() and switch.get_attribute("aria-checked") != "true":
            switch.click(timeout=10_000)
        card.get_by_title("Run now").click(timeout=20_000)
        self.page.get_by_text(f"{title} started", exact=False).wait_for(state="visible", timeout=20_000)


    def rename_active_conversation(self, title: str) -> None:
        active = self.page.locator('.row-bot-thread-row[active], .row-bot-thread-row.q-item--active').first
        if not active.count():
            raise ClientAdapterError("active conversation row is unavailable for rename")
        active.get_by_role("button").last.click(timeout=15_000)
        self.page.get_by_text("Rename", exact=True).click(timeout=15_000)
        dialog = self.page.get_by_text("Rename conversation", exact=True).locator("..").locator("..")
        name_input = dialog.locator("input").first
        name_input.fill(title)
        dialog.get_by_role("button", name="Save").click(timeout=15_000)
        self.page.get_by_text(title, exact=True).first.wait_for(state="visible", timeout=15_000)
