"""Curated Designer block registry and rendering helpers."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DesignerComponent:
    """Metadata for a curated reusable block."""

    name: str
    label: str
    category: str
    description: str
    template_html: str
    default_replacements: dict[str, str] = field(default_factory=dict)
    tags: tuple[str, ...] = ()


def _render_template(template_html: str, replacements: dict[str, str]) -> str:
    rendered = template_html
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", html.escape(str(value)))
    return rendered


def _normalize_replacements(replacements: str | dict[str, Any] | None) -> dict[str, str]:
    if replacements is None:
        return {}
    if isinstance(replacements, dict):
        return {str(key): str(value) for key, value in replacements.items()}

    raw = replacements.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid replacements_json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("replacements_json must be a JSON object.")
    return {str(key): str(value) for key, value in parsed.items()}


_COMPONENTS: dict[str, DesignerComponent] = {
    "hero_callout": DesignerComponent(
        name="hero_callout",
        label="Hero Callout",
        category="Story",
        description="Two-column opener with a strong claim, support copy, and one proof tile.",
        tags=("hero", "intro", "launch"),
        default_replacements={
            "eyebrow": "Product launch",
            "headline": "A high-clarity opener that frames the story in one screen.",
            "body": "Use this block when the page needs one strong promise, a short supporting paragraph, and a single proof signal.",
            "primary_cta": "Primary signal",
            "secondary_text": "Secondary note",
            "stat_label": "Active pilots",
            "stat_value": "42",
            "stat_caption": "Teams already using the workflow.",
        },
        template_html="""
<section style="display:grid; grid-template-columns: minmax(0, 1.3fr) minmax(260px, 0.7fr); gap: 28px; padding: 40px; border-radius: 28px; background: linear-gradient(135deg, color-mix(in srgb, var(--primary) 18%, transparent), color-mix(in srgb, var(--secondary) 14%, transparent)); border: 1px solid color-mix(in srgb, var(--primary) 22%, transparent); color: var(--text);">
  <div style="display:flex; flex-direction:column; gap: 16px; justify-content:center;">
    <div style="font: 600 12px/1.2 var(--body-font); letter-spacing: 0.18em; text-transform: uppercase; color: color-mix(in srgb, var(--text) 68%, transparent);">{{eyebrow}}</div>
    <h2 style="margin:0; font: 700 40px/1.05 var(--heading-font); letter-spacing:-0.03em;">{{headline}}</h2>
    <p style="margin:0; font: 400 18px/1.55 var(--body-font); max-width: 42ch; color: color-mix(in srgb, var(--text) 82%, transparent);">{{body}}</p>
    <div style="display:flex; gap: 14px; align-items:center; flex-wrap:wrap; margin-top: 6px;">
      <div style="padding: 12px 18px; border-radius: 999px; background: var(--accent); color: #111827; font: 700 13px/1 var(--body-font);">{{primary_cta}}</div>
      <div style="font: 500 14px/1.3 var(--body-font); color: color-mix(in srgb, var(--text) 72%, transparent);">{{secondary_text}}</div>
    </div>
  </div>
  <div style="display:flex; align-items:stretch;">
    <div style="width:100%; border-radius: 24px; padding: 24px; background: rgba(15, 23, 42, 0.42); border: 1px solid rgba(255,255,255,0.08); display:flex; flex-direction:column; justify-content:space-between; gap: 18px; min-height: 220px;">
      <div style="display:flex; justify-content:space-between; align-items:center; gap: 12px;">
        <div style="font: 600 12px/1.2 var(--body-font); letter-spacing: 0.16em; text-transform: uppercase; color: color-mix(in srgb, var(--text) 62%, transparent);">{{stat_label}}</div>
        <div style="width: 10px; height: 10px; border-radius: 999px; background: var(--accent);"></div>
      </div>
      <div style="font: 700 64px/0.95 var(--heading-font); letter-spacing: -0.04em;">{{stat_value}}</div>
      <div style="font: 500 14px/1.45 var(--body-font); color: color-mix(in srgb, var(--text) 74%, transparent);">{{stat_caption}}</div>
    </div>
  </div>
</section>
""",
    ),
    "stats_band": DesignerComponent(
        name="stats_band",
        label="Stats Band",
        category="Evidence",
        description="A horizontal evidence strip for traction, proof, or KPI snapshots.",
        tags=("metrics", "traction", "kpis"),
        default_replacements={
            "headline": "Momentum in four signals",
            "metric_1_value": "$4.2M",
            "metric_1_label": "ARR",
            "metric_2_value": "127%",
            "metric_2_label": "Net retention",
            "metric_3_value": "9 days",
            "metric_3_label": "Time to launch",
            "metric_4_value": "31",
            "metric_4_label": "Enterprise accounts",
        },
        template_html="""
<section style="padding: 28px 30px; border-radius: 24px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); display:flex; flex-direction:column; gap: 18px; color: var(--text);">
  <div style="font: 700 22px/1.15 var(--heading-font);">{{headline}}</div>
  <div style="display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px;">
    <div style="padding: 16px; border-radius: 18px; background: color-mix(in srgb, var(--primary) 14%, transparent);"><div style="font:700 28px/1 var(--heading-font);">{{metric_1_value}}</div><div style="margin-top:8px; font:500 13px/1.35 var(--body-font); color: color-mix(in srgb, var(--text) 72%, transparent);">{{metric_1_label}}</div></div>
    <div style="padding: 16px; border-radius: 18px; background: color-mix(in srgb, var(--secondary) 14%, transparent);"><div style="font:700 28px/1 var(--heading-font);">{{metric_2_value}}</div><div style="margin-top:8px; font:500 13px/1.35 var(--body-font); color: color-mix(in srgb, var(--text) 72%, transparent);">{{metric_2_label}}</div></div>
    <div style="padding: 16px; border-radius: 18px; background: rgba(255,255,255,0.04);"><div style="font:700 28px/1 var(--heading-font);">{{metric_3_value}}</div><div style="margin-top:8px; font:500 13px/1.35 var(--body-font); color: color-mix(in srgb, var(--text) 72%, transparent);">{{metric_3_label}}</div></div>
    <div style="padding: 16px; border-radius: 18px; background: color-mix(in srgb, var(--accent) 18%, transparent); color: var(--text);"><div style="font:700 28px/1 var(--heading-font);">{{metric_4_value}}</div><div style="margin-top:8px; font:500 13px/1.35 var(--body-font); color: color-mix(in srgb, var(--text) 72%, transparent);">{{metric_4_label}}</div></div>
  </div>
</section>
""",
    ),
    "feature_cards": DesignerComponent(
        name="feature_cards",
        label="Feature Cards",
        category="Story",
        description="Three-card feature grid for benefits, capabilities, or offer framing.",
        tags=("features", "cards", "benefits"),
        default_replacements={
            "headline": "Why the product lands quickly",
            "card_1_title": "Structured workflows",
            "card_1_body": "Give the audience a concise explanation of the first differentiator.",
            "card_2_title": "Operational visibility",
            "card_2_body": "Use the middle card for proof, velocity, or measurable clarity.",
            "card_3_title": "Ready-to-ship polish",
            "card_3_body": "Close with the benefit that matters most to decision makers.",
        },
        template_html="""
<section style="display:flex; flex-direction:column; gap: 18px; color: var(--text);">
  <h2 style="margin:0; font:700 28px/1.1 var(--heading-font);">{{headline}}</h2>
  <div style="display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px;">
    <article style="padding: 22px; border-radius: 22px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);"><div style="font:600 12px/1.2 var(--body-font); letter-spacing:0.16em; text-transform:uppercase; color: var(--accent);">01</div><h3 style="margin:14px 0 10px; font:700 21px/1.1 var(--heading-font);">{{card_1_title}}</h3><p style="margin:0; font:400 15px/1.55 var(--body-font); color: color-mix(in srgb, var(--text) 78%, transparent);">{{card_1_body}}</p></article>
    <article style="padding: 22px; border-radius: 22px; background: color-mix(in srgb, var(--primary) 12%, transparent); border: 1px solid color-mix(in srgb, var(--primary) 18%, transparent);"><div style="font:600 12px/1.2 var(--body-font); letter-spacing:0.16em; text-transform:uppercase; color: var(--primary);">02</div><h3 style="margin:14px 0 10px; font:700 21px/1.1 var(--heading-font);">{{card_2_title}}</h3><p style="margin:0; font:400 15px/1.55 var(--body-font); color: color-mix(in srgb, var(--text) 78%, transparent);">{{card_2_body}}</p></article>
    <article style="padding: 22px; border-radius: 22px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);"><div style="font:600 12px/1.2 var(--body-font); letter-spacing:0.16em; text-transform:uppercase; color: var(--secondary);">03</div><h3 style="margin:14px 0 10px; font:700 21px/1.1 var(--heading-font);">{{card_3_title}}</h3><p style="margin:0; font:400 15px/1.55 var(--body-font); color: color-mix(in srgb, var(--text) 78%, transparent);">{{card_3_body}}</p></article>
  </div>
</section>
""",
    ),
    "testimonial_quote": DesignerComponent(
        name="testimonial_quote",
        label="Testimonial Quote",
        category="Evidence",
        description="A proof block for customer voice, analyst validation, or internal stakeholder endorsement.",
        tags=("quote", "testimonial", "proof"),
        default_replacements={
            "quote": "The team cut launch preparation from weeks to days without sacrificing control or confidence.",
            "person": "Morgan Lee",
            "title": "VP Operations, Northstar Health",
        },
        template_html="""
<section style="padding: 30px 34px; border-radius: 28px; background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02)); border: 1px solid rgba(255,255,255,0.08); color: var(--text); display:flex; flex-direction:column; gap: 18px;">
  <div style="font:700 52px/0.8 var(--heading-font); color: color-mix(in srgb, var(--accent) 72%, white 28%);">“</div>
  <blockquote style="margin:0; font:600 28px/1.3 var(--heading-font); letter-spacing:-0.02em; max-width: 30ch;">{{quote}}</blockquote>
  <div style="display:flex; flex-direction:column; gap:4px;">
    <div style="font:700 16px/1.2 var(--body-font);">{{person}}</div>
    <div style="font:500 13px/1.35 var(--body-font); color: color-mix(in srgb, var(--text) 70%, transparent);">{{title}}</div>
  </div>
</section>
""",
    ),
    "pricing_cards": DesignerComponent(
        name="pricing_cards",
        label="Pricing Cards",
        category="Conversion",
        description="Three-tier offer block with a highlighted middle plan for proposals or sales pages.",
        tags=("pricing", "plans", "offer"),
        default_replacements={
            "headline": "Choose the operating model that fits your team",
            "plan_1_name": "Starter",
            "plan_1_price": "$499",
            "plan_1_note": "For small teams testing the workflow.",
            "plan_2_name": "Growth",
            "plan_2_price": "$1,499",
            "plan_2_note": "For teams that need collaboration and governance.",
            "plan_3_name": "Enterprise",
            "plan_3_price": "Custom",
            "plan_3_note": "For orgs with bespoke controls and rollout needs.",
        },
        template_html="""
<section style="display:flex; flex-direction:column; gap: 20px; color: var(--text);">
  <h2 style="margin:0; font:700 28px/1.1 var(--heading-font);">{{headline}}</h2>
  <div style="display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px;">
    <article style="padding: 24px; border-radius: 24px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);"><div style="font:700 18px/1.1 var(--heading-font);">{{plan_1_name}}</div><div style="margin-top:14px; font:700 42px/1 var(--heading-font);">{{plan_1_price}}</div><p style="margin:12px 0 0; font:400 14px/1.55 var(--body-font); color: color-mix(in srgb, var(--text) 75%, transparent);">{{plan_1_note}}</p></article>
    <article style="padding: 24px; border-radius: 24px; background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 24%, transparent), rgba(255,255,255,0.05)); border: 1px solid color-mix(in srgb, var(--accent) 28%, transparent); box-shadow: 0 20px 40px rgba(0,0,0,0.12);"><div style="display:flex; justify-content:space-between; gap:12px; align-items:center;"><div style="font:700 18px/1.1 var(--heading-font);">{{plan_2_name}}</div><div style="padding:6px 10px; border-radius:999px; background: rgba(17,24,39,0.7); font:700 11px/1 var(--body-font); letter-spacing:0.12em; text-transform:uppercase;">Recommended</div></div><div style="margin-top:14px; font:700 42px/1 var(--heading-font);">{{plan_2_price}}</div><p style="margin:12px 0 0; font:400 14px/1.55 var(--body-font); color: color-mix(in srgb, var(--text) 78%, transparent);">{{plan_2_note}}</p></article>
    <article style="padding: 24px; border-radius: 24px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);"><div style="font:700 18px/1.1 var(--heading-font);">{{plan_3_name}}</div><div style="margin-top:14px; font:700 42px/1 var(--heading-font);">{{plan_3_price}}</div><p style="margin:12px 0 0; font:400 14px/1.55 var(--body-font); color: color-mix(in srgb, var(--text) 75%, transparent);">{{plan_3_note}}</p></article>
  </div>
</section>
""",
    ),
    "timeline_steps": DesignerComponent(
        name="timeline_steps",
        label="Timeline Steps",
        category="Story",
        description="Three-step roadmap section for rollout plans, onboarding, or narrative sequencing.",
        tags=("timeline", "roadmap", "steps"),
        default_replacements={
            "headline": "A rollout in three deliberate steps",
            "step_1_title": "Frame the workflow",
            "step_1_body": "Clarify the operating model, owner, and success condition.",
            "step_2_title": "Launch the pilot",
            "step_2_body": "Run the first live cycle with lightweight instrumentation.",
            "step_3_title": "Scale with guardrails",
            "step_3_body": "Move from proof to repeatable operating cadence across teams.",
        },
        template_html="""
<section style="display:flex; flex-direction:column; gap: 18px; color: var(--text);">
  <h2 style="margin:0; font:700 28px/1.1 var(--heading-font);">{{headline}}</h2>
  <div style="display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px;">
    <article style="padding: 22px; border-radius: 22px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);"><div style="width: 34px; height: 34px; border-radius: 999px; display:grid; place-items:center; background: color-mix(in srgb, var(--primary) 20%, transparent); font:700 14px/1 var(--body-font);">1</div><h3 style="margin:16px 0 10px; font:700 20px/1.1 var(--heading-font);">{{step_1_title}}</h3><p style="margin:0; font:400 14px/1.55 var(--body-font); color: color-mix(in srgb, var(--text) 76%, transparent);">{{step_1_body}}</p></article>
    <article style="padding: 22px; border-radius: 22px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);"><div style="width: 34px; height: 34px; border-radius: 999px; display:grid; place-items:center; background: color-mix(in srgb, var(--secondary) 20%, transparent); font:700 14px/1 var(--body-font);">2</div><h3 style="margin:16px 0 10px; font:700 20px/1.1 var(--heading-font);">{{step_2_title}}</h3><p style="margin:0; font:400 14px/1.55 var(--body-font); color: color-mix(in srgb, var(--text) 76%, transparent);">{{step_2_body}}</p></article>
    <article style="padding: 22px; border-radius: 22px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);"><div style="width: 34px; height: 34px; border-radius: 999px; display:grid; place-items:center; background: color-mix(in srgb, var(--accent) 24%, transparent); font:700 14px/1 var(--body-font);">3</div><h3 style="margin:16px 0 10px; font:700 20px/1.1 var(--heading-font);">{{step_3_title}}</h3><p style="margin:0; font:400 14px/1.55 var(--body-font); color: color-mix(in srgb, var(--text) 76%, transparent);">{{step_3_body}}</p></article>
  </div>
</section>
""",
    ),
}


def list_components() -> list[DesignerComponent]:
    """Return the curated block catalog in display order."""
    return list(_COMPONENTS.values())


def get_component(name: str) -> DesignerComponent:
    """Look up a component by registry name."""
    key = (name or "").strip().lower()
    component = _COMPONENTS.get(key)
    if component is None:
        available = ", ".join(sorted(_COMPONENTS))
        raise ValueError(f"Unknown component '{name}'. Available: {available}.")
    return component


def render_component_html(name: str, replacements: str | dict[str, Any] | None = None) -> str:
    """Render one curated block with optional JSON or dict replacements."""
    component = get_component(name)
    merged = dict(component.default_replacements)
    merged.update(_normalize_replacements(replacements))
    return _render_template(component.template_html.strip(), merged)