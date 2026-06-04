"""Designer — template definitions for 7 design categories.

Each template provides a set of starter pages with professional HTML/CSS
using CSS variables for brand theming.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from row_bot.brand import APP_BRAND_ACCENT


@dataclass
class Template:
    """A designer template definition."""
    id: str
    name: str
    category: str          # "Starters" | "Presentations" | "Documents" | "Marketing" | "UI" | "General"
    description: str
    aspect_ratio: str      # default aspect ratio for this template
    pages: list[dict]      # [{html, title, notes}...]
    icon: str = "🎨"
    # Phase 2.3.A — Designer mode this template targets. Drives gallery
    # filtering, default canvas resolution, and the agent's initial
    # prompt. Legacy templates default to "deck".
    mode: str = "deck"
    # Phase 2.3.H — When True, template still resolves via get_template(id)
    # (legacy aliases, deep-links) but is hidden from the gallery grid and
    # the selected-template summary. Avoids duplicate visible cards when
    # two templates map to the same starter (e.g. blank_canvas vs
    # blank_deck).
    hidden_from_gallery: bool = False


# ─── CSS base that all templates share ────────────────────────────────────
def _base_style(
    width: int = 1920, height: int = 1080,
    primary: str = APP_BRAND_ACCENT, secondary: str = "#2F4B68",
    accent: str = "#5D82A8", bg: str = "#0F172A", text: str = "#F8FAFC",
    heading_font: str = "Inter", body_font: str = "Inter",
) -> str:
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    # Resolve fonts locally (bundled → cached → CDN fallback)
    font_families = list(dict.fromkeys([heading_font, body_font, "Inter"]))
    font_css = get_all_fonts_css(font_families)
    fallback = get_fallback_stack(body_font)
    h_fallback = get_fallback_stack(heading_font)
    return (
        f"<style>\n{font_css}\n"
        "  * { margin: 0; padding: 0; box-sizing: border-box; }\n"
        "  :root {\n"
        f"    --primary: {primary};\n"
        f"    --secondary: {secondary};\n"
        f"    --accent: {accent};\n"
        f"    --bg: {bg};\n"
        f"    --text: {text};\n"
        f"    --heading-font: '{heading_font}', {fallback};\n"
        f"    --body-font: '{body_font}', {fallback};\n"
        "  }\n"
        "  body {\n"
        f"    width: {width}px; height: {height}px;\n"
        f"    font-family: var(--body-font);\n"
        "    background: var(--bg);\n"
        "    color: var(--text);\n"
        "    overflow: hidden;\n"
        "    -webkit-font-smoothing: antialiased;\n"
        "    text-rendering: optimizeLegibility;\n"
        "  }\n"
        "  h1, h2, h3, h4 {\n"
        f"    font-family: var(--heading-font);\n"
        "    letter-spacing: -0.02em;\n"
        "  }\n"
        "  .card {\n"
        "    background: rgba(255,255,255,0.05);\n"
        "    border-radius: 16px;\n"
        "    border: 1px solid rgba(255,255,255,0.06);\n"
        "    backdrop-filter: blur(8px);\n"
        "  }\n"
        "  .card-light {\n"
        "    background: #F9FAFB;\n"
        "    border-radius: 12px;\n"
        "    border: 1px solid #E5E7EB;\n"
        "  }\n"
        "  .gradient-primary {\n"
        "    background: linear-gradient(135deg, var(--primary), var(--secondary));\n"
        "  }\n"
        "</style>"
    )


# ═══════════════════════════════════════════════════════════════════════
# TEMPLATES
# ═══════════════════════════════════════════════════════════════════════

def _pitch_deck() -> Template:
    s = _base_style()
    return Template(
        id="pitch_deck",
        name="Pitch Deck",
        category="Presentations",
        description="5-slide investor pitch deck with title, problem, solution, traction, and CTA.",
        aspect_ratio="16:9",
        icon="📊",
        pages=[
            {"title": "Title Slide", "notes": "Introduce company and tagline.", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;justify-content:center;align-items:center;height:100%;padding:80px;">
  <div style="width:120px;height:120px;border-radius:24px;background:var(--primary);margin-bottom:40px;display:flex;align-items:center;justify-content:center;">
    <span style="font-size:3rem;color:#fff;">✦</span>
  </div>
  <h1 style="font-size:4.5rem;font-weight:800;text-align:center;margin-bottom:16px;">Your Company Name</h1>
  <p style="font-size:1.8rem;opacity:0.7;text-align:center;max-width:800px;">A one-line description of what you do and why it matters</p>
  <div style="margin-top:60px;padding:12px 32px;border:2px solid var(--accent);border-radius:8px;font-size:1.2rem;color:var(--accent);">investor@company.com</div>
</div></body></html>"""},
            {"title": "Problem", "notes": "Define the problem you solve.", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;padding:80px 100px;height:100%;">
  <h2 style="font-size:3rem;font-weight:700;color:var(--accent);margin-bottom:48px;">The Problem</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:40px;flex:1;align-items:start;">
    <div style="background:rgba(255,255,255,0.05);border-radius:16px;padding:40px;">
      <div style="font-size:2.5rem;margin-bottom:16px;">😤</div>
      <h3 style="font-size:1.5rem;margin-bottom:12px;">Pain Point 1</h3>
      <p style="font-size:1.1rem;opacity:0.7;line-height:1.6;">Description of the first major pain point your target audience faces.</p>
    </div>
    <div style="background:rgba(255,255,255,0.05);border-radius:16px;padding:40px;">
      <div style="font-size:2.5rem;margin-bottom:16px;">⏰</div>
      <h3 style="font-size:1.5rem;margin-bottom:12px;">Pain Point 2</h3>
      <p style="font-size:1.1rem;opacity:0.7;line-height:1.6;">Description of the second major pain point your target audience faces.</p>
    </div>
    <div style="background:rgba(255,255,255,0.05);border-radius:16px;padding:40px;">
      <div style="font-size:2.5rem;margin-bottom:16px;">💸</div>
      <h3 style="font-size:1.5rem;margin-bottom:12px;">Pain Point 3</h3>
      <p style="font-size:1.1rem;opacity:0.7;line-height:1.6;">Description of the third major pain point your target audience faces.</p>
    </div>
  </div>
</div></body></html>"""},
            {"title": "Solution", "notes": "Show your solution.", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;padding:80px 100px;height:100%;">
  <h2 style="font-size:3rem;font-weight:700;color:var(--primary);margin-bottom:48px;">Our Solution</h2>
  <div style="display:flex;gap:60px;flex:1;align-items:center;">
    <div style="flex:1;">
      <h3 style="font-size:2rem;margin-bottom:24px;">How It Works</h3>
      <div style="display:flex;flex-direction:column;gap:20px;">
        <div style="display:flex;align-items:center;gap:16px;"><div style="width:40px;height:40px;border-radius:50%;background:var(--primary);display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0;">1</div><p style="font-size:1.2rem;opacity:0.8;">Step one of your solution workflow</p></div>
        <div style="display:flex;align-items:center;gap:16px;"><div style="width:40px;height:40px;border-radius:50%;background:var(--primary);display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0;">2</div><p style="font-size:1.2rem;opacity:0.8;">Step two of your solution workflow</p></div>
        <div style="display:flex;align-items:center;gap:16px;"><div style="width:40px;height:40px;border-radius:50%;background:var(--primary);display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0;">3</div><p style="font-size:1.2rem;opacity:0.8;">Step three of your solution workflow</p></div>
      </div>
    </div>
    <div style="flex:1;height:400px;border-radius:20px;background:linear-gradient(135deg,var(--primary),var(--secondary));display:flex;align-items:center;justify-content:center;">
      <span style="font-size:4rem;opacity:0.3;">📱</span>
    </div>
  </div>
</div></body></html>"""},
            {"title": "Traction", "notes": "Show your progress and metrics.", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;padding:80px 100px;height:100%;">
  <h2 style="font-size:3rem;font-weight:700;color:var(--accent);margin-bottom:48px;">Traction</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:32px;margin-bottom:48px;">
    <div style="text-align:center;padding:32px;background:rgba(255,255,255,0.05);border-radius:16px;">
      <div style="font-size:3rem;font-weight:800;color:var(--primary);">10K+</div>
      <div style="font-size:1rem;opacity:0.6;margin-top:8px;">Active Users</div>
    </div>
    <div style="text-align:center;padding:32px;background:rgba(255,255,255,0.05);border-radius:16px;">
      <div style="font-size:3rem;font-weight:800;color:var(--primary);">150%</div>
      <div style="font-size:1rem;opacity:0.6;margin-top:8px;">MoM Growth</div>
    </div>
    <div style="text-align:center;padding:32px;background:rgba(255,255,255,0.05);border-radius:16px;">
      <div style="font-size:3rem;font-weight:800;color:var(--primary);">$2M</div>
      <div style="font-size:1rem;opacity:0.6;margin-top:8px;">ARR</div>
    </div>
    <div style="text-align:center;padding:32px;background:rgba(255,255,255,0.05);border-radius:16px;">
      <div style="font-size:3rem;font-weight:800;color:var(--primary);">95%</div>
      <div style="font-size:1rem;opacity:0.6;margin-top:8px;">Retention</div>
    </div>
  </div>
  <div style="flex:1;background:rgba(255,255,255,0.03);border-radius:16px;padding:40px;display:flex;align-items:center;justify-content:center;">
    <span style="font-size:1.5rem;opacity:0.4;">📈 Growth chart placeholder</span>
  </div>
</div></body></html>"""},
            {"title": "Call to Action", "notes": "Next steps and contact info.", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;justify-content:center;align-items:center;height:100%;padding:80px;">
  <h2 style="font-size:3.5rem;font-weight:800;text-align:center;margin-bottom:24px;">Let's Build the Future Together</h2>
  <p style="font-size:1.5rem;opacity:0.6;text-align:center;max-width:700px;margin-bottom:60px;">We're raising a $5M Series A to scale our platform globally.</p>
  <div style="display:flex;gap:24px;margin-bottom:60px;">
    <div style="padding:16px 40px;background:var(--primary);border-radius:12px;font-size:1.3rem;font-weight:600;">Schedule a Call</div>
    <div style="padding:16px 40px;border:2px solid var(--accent);border-radius:12px;font-size:1.3rem;color:var(--accent);">Download Deck</div>
  </div>
  <div style="opacity:0.5;font-size:1.1rem;">
    <p>founder@company.com · (555) 123-4567</p>
    <p style="margin-top:8px;">www.company.com</p>
  </div>
</div></body></html>"""},
        ],
    )


def _status_report() -> Template:
    s = _base_style()
    return Template(
        id="status_report",
        name="Status Report",
        category="Documents",
        description="3-page project status report with summary, metrics, and next steps.",
        aspect_ratio="A4",
        icon="📋",
        mode="document",
        pages=[
            {"title": "Executive Summary", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;padding:80px 100px;height:100%;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:48px;">
    <h1 style="font-size:3rem;font-weight:800;">Project Status Report</h1>
    <div style="padding:8px 24px;background:var(--accent);border-radius:8px;font-weight:600;color:#000;">On Track</div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:40px;flex:1;">
    <div style="background:rgba(255,255,255,0.05);border-radius:16px;padding:36px;">
      <h3 style="font-size:1.3rem;color:var(--primary);margin-bottom:16px;">Overview</h3>
      <p style="font-size:1.1rem;line-height:1.8;opacity:0.8;">Summary of the project's current state, key accomplishments this period, and overall health assessment.</p>
    </div>
    <div style="background:rgba(255,255,255,0.05);border-radius:16px;padding:36px;">
      <h3 style="font-size:1.3rem;color:var(--primary);margin-bottom:16px;">Key Highlights</h3>
      <ul style="font-size:1.1rem;line-height:2;opacity:0.8;list-style:none;padding:0;">
        <li>✅ Milestone 1 completed ahead of schedule</li>
        <li>✅ Team expanded by 2 engineers</li>
        <li>⚠️ Budget utilization at 78%</li>
        <li>🔄 Feature X moved to next sprint</li>
      </ul>
    </div>
  </div>
  <div style="margin-top:32px;opacity:0.4;font-size:0.9rem;">Report Date: April 2026 · Prepared by: Team Lead</div>
</div></body></html>"""},
            {"title": "Metrics", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;padding:80px 100px;height:100%;">
  <h2 style="font-size:2.5rem;font-weight:700;margin-bottom:48px;">Key Metrics</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:32px;margin-bottom:40px;">
    <div style="text-align:center;padding:32px;background:rgba(37,99,235,0.15);border-radius:16px;border:1px solid rgba(37,99,235,0.3);">
      <div style="font-size:2.5rem;font-weight:800;color:var(--primary);">87%</div>
      <div style="font-size:1rem;opacity:0.6;margin-top:8px;">Sprint Velocity</div>
    </div>
    <div style="text-align:center;padding:32px;background:rgba(245,158,11,0.15);border-radius:16px;border:1px solid rgba(245,158,11,0.3);">
      <div style="font-size:2.5rem;font-weight:800;color:var(--accent);">23</div>
      <div style="font-size:1rem;opacity:0.6;margin-top:8px;">Tasks Completed</div>
    </div>
    <div style="text-align:center;padding:32px;background:rgba(34,197,94,0.15);border-radius:16px;border:1px solid rgba(34,197,94,0.3);">
      <div style="font-size:2.5rem;font-weight:800;color:#22c55e;">4</div>
      <div style="font-size:1rem;opacity:0.6;margin-top:8px;">Blockers Resolved</div>
    </div>
  </div>
  <div style="flex:1;background:rgba(255,255,255,0.03);border-radius:16px;padding:40px;display:flex;align-items:center;justify-content:center;">
    <span style="opacity:0.3;font-size:1.2rem;">📊 Burn-down chart placeholder</span>
  </div>
</div></body></html>"""},
            {"title": "Next Steps", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;padding:80px 100px;height:100%;">
  <h2 style="font-size:2.5rem;font-weight:700;margin-bottom:48px;">Next Steps</h2>
  <div style="display:flex;flex-direction:column;gap:24px;flex:1;">
    <div style="display:flex;gap:20px;align-items:start;padding:24px;background:rgba(255,255,255,0.05);border-radius:12px;border-left:4px solid var(--primary);">
      <div style="font-size:1.2rem;font-weight:700;color:var(--primary);white-space:nowrap;">Week 1</div>
      <div><h4 style="font-size:1.2rem;margin-bottom:8px;">Complete Feature Integration</h4><p style="opacity:0.6;">Finish API integration and run end-to-end tests.</p></div>
    </div>
    <div style="display:flex;gap:20px;align-items:start;padding:24px;background:rgba(255,255,255,0.05);border-radius:12px;border-left:4px solid var(--accent);">
      <div style="font-size:1.2rem;font-weight:700;color:var(--accent);white-space:nowrap;">Week 2</div>
      <div><h4 style="font-size:1.2rem;margin-bottom:8px;">User Testing Round</h4><p style="opacity:0.6;">Run beta testing with 50 users and collect feedback.</p></div>
    </div>
    <div style="display:flex;gap:20px;align-items:start;padding:24px;background:rgba(255,255,255,0.05);border-radius:12px;border-left:4px solid #22c55e;">
      <div style="font-size:1.2rem;font-weight:700;color:#22c55e;white-space:nowrap;">Week 3</div>
      <div><h4 style="font-size:1.2rem;margin-bottom:8px;">Production Release</h4><p style="opacity:0.6;">Deploy to production and begin monitoring.</p></div>
    </div>
  </div>
  <div style="margin-top:32px;padding:24px;background:rgba(245,158,11,0.1);border-radius:12px;border:1px solid rgba(245,158,11,0.3);">
    <h4 style="color:var(--accent);margin-bottom:8px;">⚠️ Risks</h4>
    <p style="opacity:0.7;">Third-party API rate limits may require caching strategy. Team capacity reduced in Week 2 due to company offsite.</p>
  </div>
</div></body></html>"""},
        ],
    )


def _marketing_one_pager() -> Template:
    s = _base_style()
    return Template(
        id="marketing_one_pager",
        name="Marketing One-Pager",
        category="Marketing",
        description="Single-page product marketing sheet with benefits, social proof, and CTA.",
        aspect_ratio="A4",
        icon="📄",
        mode="document",
        pages=[
            {"title": "One-Pager", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s.replace('1920px', '794px').replace('1080px', '1123px')}</head><body>
<div style="display:flex;flex-direction:column;padding:60px 50px;height:100%;gap:32px;">
  <div style="text-align:center;">
    <div style="width:60px;height:60px;border-radius:12px;background:var(--primary);margin:0 auto 16px;display:flex;align-items:center;justify-content:center;"><span style="font-size:1.5rem;color:#fff;">✦</span></div>
    <h1 style="font-size:2.2rem;font-weight:800;margin-bottom:8px;">Product Name</h1>
    <p style="font-size:1.1rem;opacity:0.7;">The one-liner that explains your value proposition</p>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;">
    <div style="padding:24px;background:rgba(255,255,255,0.05);border-radius:12px;text-align:center;">
      <div style="font-size:1.8rem;margin-bottom:8px;">🚀</div>
      <h3 style="font-size:1rem;margin-bottom:8px;">Fast</h3>
      <p style="font-size:0.85rem;opacity:0.6;line-height:1.5;">10x faster than alternatives.</p>
    </div>
    <div style="padding:24px;background:rgba(255,255,255,0.05);border-radius:12px;text-align:center;">
      <div style="font-size:1.8rem;margin-bottom:8px;">🔒</div>
      <h3 style="font-size:1rem;margin-bottom:8px;">Secure</h3>
      <p style="font-size:0.85rem;opacity:0.6;line-height:1.5;">Enterprise-grade security built in.</p>
    </div>
    <div style="padding:24px;background:rgba(255,255,255,0.05);border-radius:12px;text-align:center;">
      <div style="font-size:1.8rem;margin-bottom:8px;">💡</div>
      <h3 style="font-size:1rem;margin-bottom:8px;">Simple</h3>
      <p style="font-size:0.85rem;opacity:0.6;line-height:1.5;">Get started in under 5 minutes.</p>
    </div>
  </div>
  <div style="background:rgba(255,255,255,0.03);border-radius:12px;padding:24px;text-align:center;">
    <p style="font-size:1rem;font-style:italic;opacity:0.7;">"This product transformed how our team works. We saved 20 hours per week."</p>
    <p style="font-size:0.85rem;margin-top:8px;color:var(--accent);">— Jane Doe, VP Engineering at Acme Corp</p>
  </div>
  <div style="text-align:center;padding:24px;background:linear-gradient(135deg,var(--primary),var(--secondary));border-radius:12px;">
    <h3 style="font-size:1.3rem;margin-bottom:8px;">Ready to Get Started?</h3>
    <p style="opacity:0.8;">Visit www.product.com or email sales@product.com</p>
  </div>
</div></body></html>"""},
        ],
    )


def _product_launch() -> Template:
    s = _base_style()
    return Template(
        id="product_launch",
        name="Product Launch",
        category="Marketing",
        description="4-slide product launch announcement with hero, features, pricing, and availability.",
        aspect_ratio="16:9",
        icon="🚀",
        pages=[
            {"title": "Hero", "notes": "Main announcement slide.", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;justify-content:center;align-items:center;height:100%;background:linear-gradient(135deg,var(--bg) 0%,rgba(37,99,235,0.2) 100%);">
  <div style="padding:8px 20px;border:1px solid var(--accent);border-radius:20px;font-size:0.9rem;color:var(--accent);margin-bottom:32px;">🎉 NOW AVAILABLE</div>
  <h1 style="font-size:5rem;font-weight:800;text-align:center;margin-bottom:16px;">Introducing<br><span style="color:var(--primary);">Product 2.0</span></h1>
  <p style="font-size:1.5rem;opacity:0.6;text-align:center;max-width:700px;">The next generation of your favourite tool — faster, smarter, and more powerful than ever.</p>
</div></body></html>"""},
            {"title": "Features", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;padding:80px 100px;height:100%;">
  <h2 style="font-size:2.5rem;font-weight:700;margin-bottom:48px;">What's New</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;flex:1;">
    <div style="padding:36px;background:rgba(255,255,255,0.05);border-radius:16px;"><div style="font-size:2rem;margin-bottom:12px;">⚡</div><h3 style="font-size:1.4rem;margin-bottom:12px;">Lightning Fast</h3><p style="opacity:0.6;line-height:1.6;">3x performance improvement with our new engine.</p></div>
    <div style="padding:36px;background:rgba(255,255,255,0.05);border-radius:16px;"><div style="font-size:2rem;margin-bottom:12px;">🤖</div><h3 style="font-size:1.4rem;margin-bottom:12px;">AI-Powered</h3><p style="opacity:0.6;line-height:1.6;">Built-in AI assistant for smarter workflows.</p></div>
    <div style="padding:36px;background:rgba(255,255,255,0.05);border-radius:16px;"><div style="font-size:2rem;margin-bottom:12px;">🔗</div><h3 style="font-size:1.4rem;margin-bottom:12px;">Integrations</h3><p style="opacity:0.6;line-height:1.6;">Connect with 100+ tools out of the box.</p></div>
    <div style="padding:36px;background:rgba(255,255,255,0.05);border-radius:16px;"><div style="font-size:2rem;margin-bottom:12px;">🛡️</div><h3 style="font-size:1.4rem;margin-bottom:12px;">Security</h3><p style="opacity:0.6;line-height:1.6;">SOC 2 Type II certified with E2E encryption.</p></div>
  </div>
</div></body></html>"""},
            {"title": "Pricing", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;align-items:center;padding:80px 100px;height:100%;">
  <h2 style="font-size:2.5rem;font-weight:700;margin-bottom:48px;">Simple Pricing</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:32px;width:100%;max-width:1200px;">
    <div style="padding:40px;background:rgba(255,255,255,0.05);border-radius:16px;text-align:center;">
      <h3 style="font-size:1.2rem;opacity:0.6;margin-bottom:16px;">STARTER</h3>
      <div style="font-size:3rem;font-weight:800;margin-bottom:8px;">$9<span style="font-size:1rem;opacity:0.5;">/mo</span></div>
      <div style="height:1px;background:rgba(255,255,255,0.1);margin:20px 0;"></div>
      <div style="text-align:left;font-size:0.95rem;line-height:2.2;opacity:0.7;">✓ 5 projects<br>✓ Basic analytics<br>✓ Email support</div>
    </div>
    <div style="padding:40px;background:linear-gradient(135deg,rgba(37,99,235,0.2),rgba(37,99,235,0.1));border-radius:16px;text-align:center;border:2px solid var(--primary);">
      <h3 style="font-size:1.2rem;color:var(--primary);margin-bottom:16px;">PRO ⭐</h3>
      <div style="font-size:3rem;font-weight:800;margin-bottom:8px;">$29<span style="font-size:1rem;opacity:0.5;">/mo</span></div>
      <div style="height:1px;background:rgba(255,255,255,0.1);margin:20px 0;"></div>
      <div style="text-align:left;font-size:0.95rem;line-height:2.2;opacity:0.7;">✓ Unlimited projects<br>✓ Advanced analytics<br>✓ Priority support<br>✓ AI features</div>
    </div>
    <div style="padding:40px;background:rgba(255,255,255,0.05);border-radius:16px;text-align:center;">
      <h3 style="font-size:1.2rem;opacity:0.6;margin-bottom:16px;">ENTERPRISE</h3>
      <div style="font-size:3rem;font-weight:800;margin-bottom:8px;">Custom</div>
      <div style="height:1px;background:rgba(255,255,255,0.1);margin:20px 0;"></div>
      <div style="text-align:left;font-size:0.95rem;line-height:2.2;opacity:0.7;">✓ Everything in Pro<br>✓ SSO & SAML<br>✓ Dedicated support<br>✓ Custom SLA</div>
    </div>
  </div>
</div></body></html>"""},
            {"title": "Get Started", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;justify-content:center;align-items:center;height:100%;background:linear-gradient(135deg,var(--bg) 0%,rgba(37,99,235,0.15) 100%);">
  <h2 style="font-size:3.5rem;font-weight:800;text-align:center;margin-bottom:16px;">Start Building Today</h2>
  <p style="font-size:1.3rem;opacity:0.6;margin-bottom:48px;">Free 14-day trial • No credit card required</p>
  <div style="padding:16px 48px;background:var(--primary);border-radius:12px;font-size:1.4rem;font-weight:600;">Get Started Free →</div>
</div></body></html>"""},
        ],
    )


def _social_media() -> Template:
    s = _base_style(width=1080, height=1080, bg="#0F172A")
    return Template(
        id="social_media",
        name="Social Media Set",
        category="Marketing",
        description="3-post social media graphics set (1:1 square format).",
        aspect_ratio="1:1",
        icon="📱",
        pages=[
            {"title": "Quote Post", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;justify-content:center;align-items:center;height:100%;padding:80px;background:linear-gradient(135deg,var(--bg),rgba(37,99,235,0.2));">
  <div style="font-size:4rem;margin-bottom:32px;opacity:0.3;">❝</div>
  <p style="font-size:2rem;font-weight:600;text-align:center;line-height:1.5;max-width:800px;">"The best way to predict the future is to create it."</p>
  <div style="margin-top:32px;opacity:0.5;">— Peter Drucker</div>
  <div style="margin-top:48px;padding:8px 24px;border:1px solid var(--primary);border-radius:20px;font-size:0.85rem;color:var(--primary);">@yourbrand</div>
</div></body></html>"""},
            {"title": "Stats Post", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;justify-content:center;align-items:center;height:100%;padding:80px;">
  <h2 style="font-size:1.2rem;text-transform:uppercase;letter-spacing:3px;color:var(--accent);margin-bottom:32px;">Did You Know?</h2>
  <div style="font-size:6rem;font-weight:800;color:var(--primary);margin-bottom:16px;">73%</div>
  <p style="font-size:1.5rem;text-align:center;opacity:0.7;max-width:600px;">of teams report increased productivity after adopting AI tools</p>
  <div style="margin-top:48px;display:flex;gap:16px;">
    <div style="width:60px;height:4px;background:var(--primary);border-radius:2px;"></div>
    <div style="width:60px;height:4px;background:rgba(255,255,255,0.1);border-radius:2px;"></div>
    <div style="width:60px;height:4px;background:rgba(255,255,255,0.1);border-radius:2px;"></div>
  </div>
</div></body></html>"""},
            {"title": "CTA Post", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;flex-direction:column;justify-content:center;align-items:center;height:100%;padding:80px;background:linear-gradient(180deg,var(--bg) 0%,rgba(37,99,235,0.3) 100%);">
  <div style="width:80px;height:80px;border-radius:20px;background:var(--primary);margin-bottom:32px;display:flex;align-items:center;justify-content:center;font-size:2rem;">✦</div>
  <h2 style="font-size:2.5rem;font-weight:800;text-align:center;margin-bottom:16px;">Join 10,000+<br>Happy Users</h2>
  <p style="font-size:1.2rem;opacity:0.6;margin-bottom:40px;">Start your free trial today</p>
  <div style="padding:14px 40px;background:var(--accent);border-radius:30px;font-weight:700;color:#000;font-size:1.1rem;">Try It Free</div>
</div></body></html>"""},
        ],
    )


def _wireframe_kit() -> Template:
    """Low-fidelity desktop app wireframe, two routes: Dashboard + Detail."""
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    font_css = get_all_fonts_css(["Inter"])
    fallback = get_fallback_stack("Inter")
    s = (
        f"<style>\n{font_css}\n"
        "  * { margin:0; padding:0; box-sizing:border-box; }\n"
        "  :root {\n"
        "    --primary:#6B7280; --accent:#4F78A4;\n"
        "    --bg:#FFFFFF; --text:#1F2937;\n"
        f"    --body-font:'Inter', {fallback};\n"
        "  }\n"
        "  html, body { margin:0; width:1440px; height:900px; overflow:hidden; }\n"
        "  body { font-family:var(--body-font); background:var(--bg); color:var(--text); }\n"
        "  .shell { display:flex; height:100%; }\n"
        "  .side { width:260px; background:#F3F4F6; border-right:2px solid #E5E7EB; padding:24px; display:flex; flex-direction:column; gap:12px; }\n"
        "  .side .logo { height:40px; background:#D1D5DB; border-radius:8px; }\n"
        "  .side .sep  { height:1px; background:#E5E7EB; margin:8px 0; }\n"
        "  .side a { display:block; padding:8px 12px; border-radius:6px; background:#E5E7EB; color:inherit; text-decoration:none; font-size:0.9rem; }\n"
        "  .side a.active { background:#DBEAFE; border:2px solid var(--accent); }\n"
        "  .main { flex:1; padding:32px; display:flex; flex-direction:column; gap:24px; overflow:auto; }\n"
        "  .ph { background:#F9FAFB; border-radius:12px; border:2px dashed #D1D5DB; display:flex; align-items:center; justify-content:center; color:#9CA3AF; }\n"
        "  .btn { padding:10px 20px; background:#DBEAFE; border:2px solid var(--accent); border-radius:8px; color:var(--accent); font-weight:600; text-decoration:none; }\n"
        "</style>"
    )
    dashboard_body = (
        '<div style="display:flex;justify-content:space-between;align-items:center;">'
        '  <div style="height:32px;width:240px;background:#E5E7EB;border-radius:6px;"></div>'
        '  <div style="display:flex;gap:12px;align-items:center;">'
        '    <a class="btn" href="#" data-row-bot-action="navigate:detail">Open detail</a>'
        '    <div style="height:36px;width:36px;background:#E5E7EB;border-radius:50%;"></div>'
        '  </div>'
        '</div>'
        '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;">'
        '  <div class="ph" style="height:110px;"></div>'
        '  <div class="ph" style="height:110px;"></div>'
        '  <div class="ph" style="height:110px;"></div>'
        '  <div class="ph" style="height:110px;"></div>'
        '</div>'
        '<div class="ph" style="flex:1;min-height:260px;">\ud83d\udcca Main chart area</div>'
        '<div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;">'
        '  <div class="ph" style="height:200px;">\ud83d\udccb Table</div>'
        '  <div class="ph" style="height:200px;">\ud83d\udcdd Activity</div>'
        '</div>'
    )
    detail_body = (
        '<div style="display:flex;align-items:center;gap:12px;">'
        '  <a href="#" data-row-bot-action="navigate:dashboard" data-row-bot-transition="slide_right" style="text-decoration:none;color:#6B7280;font-size:0.95rem;">\u2039 Back</a>'
        '  <div style="height:28px;width:280px;background:#E5E7EB;border-radius:6px;"></div>'
        '  <div style="flex:1;"></div>'
        '  <a class="btn" href="#" data-row-bot-action="navigate:dashboard">Save</a>'
        '</div>'
        '<div style="display:grid;grid-template-columns:2fr 1fr;gap:24px;flex:1;">'
        '  <div style="display:flex;flex-direction:column;gap:16px;">'
        '    <div class="ph" style="flex-direction:column;align-items:stretch;padding:24px;">'
        '      <div style="height:20px;width:120px;background:#D1D5DB;border-radius:4px;margin-bottom:12px;"></div>'
        '      <div style="height:40px;background:#E5E7EB;border-radius:8px;margin-bottom:12px;"></div>'
        '      <div style="height:40px;background:#E5E7EB;border-radius:8px;margin-bottom:12px;"></div>'
        '      <div style="height:140px;background:#E5E7EB;border-radius:8px;"></div>'
        '    </div>'
        '    <div class="ph" style="flex:1;min-height:160px;">\ud83d\udcce Attachments</div>'
        '  </div>'
        '  <div style="display:flex;flex-direction:column;gap:16px;">'
        '    <div class="ph" style="flex-direction:column;align-items:stretch;padding:24px;">'
        '      <div style="height:20px;width:100px;background:#D1D5DB;border-radius:4px;margin-bottom:16px;"></div>'
        '      <div style="height:24px;background:#E5E7EB;border-radius:4px;margin-bottom:8px;"></div>'
        '      <div style="height:24px;background:#E5E7EB;border-radius:4px;margin-bottom:8px;"></div>'
        '      <div style="height:24px;background:#E5E7EB;border-radius:4px;"></div>'
        '    </div>'
        '    <div class="ph" style="flex:1;min-height:140px;">\ud83d\udcac Activity</div>'
        '  </div>'
        '</div>'
    )
    def _route(route_id: str, active: str, body: str) -> str:
        dash_cls = ' class="active"' if active == "dashboard" else ''
        det_cls = ' class="active"' if active == "detail" else ''
        return f"""<!DOCTYPE html><html><head>{s}</head><body>
<div class="shell" data-row-bot-route-host="1" data-row-bot-route="{route_id}">
  <aside class="side">
    <div class="logo"></div>
    <div class="sep"></div>
    <a href="#" data-row-bot-action="navigate:dashboard"{dash_cls}>Dashboard</a>
    <a href="#" data-row-bot-action="navigate:detail"{det_cls}>Detail</a>
    <a href="#">Settings</a>
    <div style="flex:1;"></div>
    <a href="#">Sign out</a>
  </aside>
  <main class="main">{body}</main>
</div></body></html>"""
    return Template(
        id="wireframe_kit",
        name="Wireframe Kit",
        category="UI",
        description="Low-fidelity desktop wireframe: dashboard + detail, with sidebar navigation wired.",
        aspect_ratio="desktop",
        icon="\ud83d\udd32",
        mode="app_mockup",
        pages=[
            {"title": "Dashboard", "notes": "Dashboard shell with KPI row, chart, table, activity.", "html": _route("dashboard", "dashboard", dashboard_body)},
            {"title": "Detail", "notes": "Detail / form page with two-column layout.", "html": _route("detail", "detail", detail_body)},
        ],
    )


def _blank_canvas() -> Template:
    s = _base_style()
    return Template(
        id="blank_canvas",
        name="Blank Canvas",
        category="Starters",
        description="Start from scratch — a single blank slide (alias for Blank Deck).",
        aspect_ratio="16:9",
        icon="🧊",
        mode="deck",
        hidden_from_gallery=True,
        pages=[
            {"title": "Page 1", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;justify-content:center;align-items:center;height:100%;">
  <p style="font-size:1.5rem;opacity:0.3;">Start designing — tell the AI what to create</p>
</div></body></html>"""},
        ],
    )


# ─── Phase 2.3.B — Blank starters per mode ────────────────────────────────

def _blank_deck() -> Template:
    s = _base_style()  # 1920x1080, overflow:hidden — standard slide body.
    return Template(
        id="blank_deck",
        name="Blank Deck",
        category="Starters",
        description="One blank 16:9 slide, ready for a deck.",
        aspect_ratio="16:9",
        icon="🟦",
        mode="deck",
        pages=[
            {"title": "Slide 1", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:flex;justify-content:center;align-items:center;height:100%;">
  <p style="font-size:1.5rem;opacity:0.3;">Blank slide — describe what to build</p>
</div></body></html>"""},
        ],
    )


def _blank_document() -> Template:
    # A4 portrait document body (794x1123 at 96dpi).
    s = _base_style().replace("1920px", "794px").replace("1080px", "1123px")
    return Template(
        id="blank_document",
        name="Blank Document",
        category="Starters",
        description="One blank A4 page, ready for a report or memo.",
        aspect_ratio="A4",
        icon="📄",
        mode="document",
        pages=[
            {"title": "Page 1", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="padding:80px 72px;">
  <p style="font-size:1.1rem;opacity:0.35;">Blank document — describe the report, memo, or one-pager you want.</p>
</div></body></html>"""},
        ],
    )


def _blank_landing() -> Template:
    # Landing mode: tall scrollable document, NOT a fixed slide body.
    # Override the _base_style body rules so the seed HTML isn't locked
    # to a 1920x1080 clipped frame.
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    font_css = get_all_fonts_css(["Inter"])
    fallback = get_fallback_stack("Inter")
    s = (
        f"<style>\n{font_css}\n"
        "  * { margin:0; padding:0; box-sizing:border-box; }\n"
        "  :root {\n"
        "    --primary:#2563EB; --secondary:#1E40AF; --accent:#F59E0B;\n"
        "    --bg:#0F172A; --text:#F8FAFC;\n"
        f"    --heading-font:'Inter', {fallback};\n"
        f"    --body-font:'Inter', {fallback};\n"
        "  }\n"
        "  html, body { margin:0; width:100%; min-height:100vh; overflow-x:hidden; }\n"
        "  body { font-family:var(--body-font); background:var(--bg); color:var(--text); }\n"
        "  .page { max-width:1440px; margin:0 auto; padding:0 clamp(16px,4vw,48px); }\n"
        "  section { padding:clamp(48px,8vw,120px) 0; }\n"
        "  h1,h2,h3 { font-family:var(--heading-font); letter-spacing:-0.02em; }\n"
        "</style>"
    )
    return Template(
        id="blank_landing",
        name="Blank Landing",
        category="Starters",
        description="A tall scrollable landing page scaffold with hero, features, and CTA sections.",
        aspect_ratio="landing",
        icon="🌐",
        mode="landing",
        pages=[
            {"title": "Landing", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div class="page">
  <section style="min-height:60vh;display:flex;flex-direction:column;justify-content:center;gap:24px;">
    <h1 style="font-size:clamp(2.5rem,6vw,4.5rem);font-weight:800;">Hero headline</h1>
    <p style="font-size:clamp(1.1rem,2vw,1.4rem);opacity:0.7;max-width:640px;">One-line value proposition describing what you do and why it matters.</p>
    <div><a href="#cta" style="display:inline-block;padding:16px 32px;background:var(--primary);color:#fff;border-radius:10px;text-decoration:none;font-weight:600;">Get started</a></div>
  </section>
  <section>
    <h2 style="font-size:clamp(1.8rem,3vw,2.4rem);margin-bottom:32px;">Features</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:24px;">
      <div style="padding:28px;background:rgba(255,255,255,0.04);border-radius:16px;border:1px solid rgba(255,255,255,0.06);">
        <h3 style="font-size:1.3rem;margin-bottom:12px;">Feature one</h3>
        <p style="opacity:0.7;">Short description of the first feature.</p>
      </div>
      <div style="padding:28px;background:rgba(255,255,255,0.04);border-radius:16px;border:1px solid rgba(255,255,255,0.06);">
        <h3 style="font-size:1.3rem;margin-bottom:12px;">Feature two</h3>
        <p style="opacity:0.7;">Short description of the second feature.</p>
      </div>
      <div style="padding:28px;background:rgba(255,255,255,0.04);border-radius:16px;border:1px solid rgba(255,255,255,0.06);">
        <h3 style="font-size:1.3rem;margin-bottom:12px;">Feature three</h3>
        <p style="opacity:0.7;">Short description of the third feature.</p>
      </div>
    </div>
  </section>
  <section id="cta" style="text-align:center;">
    <h2 style="font-size:clamp(1.8rem,3vw,2.4rem);margin-bottom:16px;">Call to action</h2>
    <p style="opacity:0.7;max-width:520px;margin:0 auto 24px;">Describe the next step you want the visitor to take.</p>
    <a href="#" style="display:inline-block;padding:16px 32px;background:var(--accent);color:#000;border-radius:10px;text-decoration:none;font-weight:700;">Contact us</a>
  </section>
</div></body></html>"""},
        ],
    )


def _blank_app_mockup() -> Template:
    # Phone viewport 390x844. Body IS the device viewport; in-content
    # scrolling is done via inner .screen containers.
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    font_css = get_all_fonts_css(["Inter"])
    fallback = get_fallback_stack("Inter")
    s = (
        f"<style>\n{font_css}\n"
        "  * { margin:0; padding:0; box-sizing:border-box; }\n"
        "  :root {\n"
        "    --primary:#2563EB; --accent:#F59E0B;\n"
        "    --bg:#0F172A; --text:#F8FAFC;\n"
        f"    --body-font:'Inter', {fallback};\n"
        "  }\n"
        "  html, body { margin:0; width:390px; height:844px; overflow:hidden; }\n"
        "  body { font-family:var(--body-font); background:var(--bg); color:var(--text); }\n"
        "  .screen { width:100%; height:100%; display:flex; flex-direction:column; }\n"
        "  .screen-body { flex:1; overflow-y:auto; padding:20px; }\n"
        "  .topbar { padding:54px 20px 12px; display:flex; align-items:center; justify-content:space-between; }\n"
        "  .btn { display:block; padding:14px 20px; background:var(--primary); color:#fff; border-radius:12px; text-align:center; text-decoration:none; font-weight:600; }\n"
        "</style>"
    )
    def _screen(title: str, body: str) -> str:
        return f"""<!DOCTYPE html><html><head>{s}</head><body>
<div class="screen">
  <div class="topbar"><h2 style="font-size:1.2rem;">{title}</h2><span style="opacity:0.5;">⋯</span></div>
  <div class="screen-body">{body}</div>
</div></body></html>"""
    home_body = (
        '<p style="opacity:0.7;margin-bottom:16px;">Welcome screen. Use the button to navigate to the detail view.</p>'
        '<a class="btn" href="#" data-row-bot-action="navigate:detail" data-row-bot-transition="slide_left">Open detail</a>'
        '<a class="btn" href="#" data-row-bot-action="navigate:settings" style="background:transparent;border:1px solid rgba(255,255,255,0.2);margin-top:12px;">Settings</a>'
    )
    detail_body = (
        '<p style="opacity:0.7;margin-bottom:16px;">Detail view. Replace this with the actual record content.</p>'
        '<a class="btn" href="#" data-row-bot-action="navigate:home" data-row-bot-transition="slide_right" style="background:transparent;border:1px solid rgba(255,255,255,0.2);">Back</a>'
    )
    settings_body = (
        '<p style="opacity:0.7;margin-bottom:16px;">Settings list — add toggles, rows, and links here.</p>'
        '<a class="btn" href="#" data-row-bot-action="navigate:home" data-row-bot-transition="slide_right" style="background:transparent;border:1px solid rgba(255,255,255,0.2);">Back</a>'
    )
    return Template(
        id="blank_app_mockup",
        name="Blank App Mockup",
        category="Starters",
        description="Phone-sized three-route scaffold: home → detail + settings, with navigate actions wired.",
        aspect_ratio="phone",
        icon="📱",
        mode="app_mockup",
        pages=[
            {"title": "Home", "notes": "", "html": _screen("Home", home_body)},
            {"title": "Detail", "notes": "", "html": _screen("Detail", detail_body)},
            {"title": "Settings", "notes": "", "html": _screen("Settings", settings_body)},
        ],
    )


def _blank_storyboard() -> Template:
    s = _base_style()  # 1920x1080, overflow:hidden.
    def _shot(num: int, beat: str) -> str:
        return f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:grid;grid-template-columns:1.15fr 1fr;height:100%;">
  <div style="display:flex;flex-direction:column;justify-content:center;align-items:center;padding:60px;gap:20px;border-right:1px solid rgba(255,255,255,0.08);background:rgba(255,255,255,0.02);">
    <div style="font-size:0.85rem;letter-spacing:0.25em;text-transform:uppercase;opacity:0.5;">Shot {num} — {beat}</div>
    <div data-row-bot-shot-visual="1" style="width:100%;max-width:640px;aspect-ratio:16/9;border:2px dashed rgba(255,255,255,0.28);border-radius:14px;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,0.03);">
      <div style="text-align:center;opacity:0.55;padding:16px;">
        <div style="font-size:2.2rem;line-height:1;margin-bottom:8px;">🎬</div>
        <div style="font-size:0.85rem;letter-spacing:0.14em;text-transform:uppercase;opacity:0.8;">Shot visual</div>
        <div style="font-size:0.75rem;opacity:0.7;margin-top:6px;">Ask the agent to generate or animate a still for this shot.</div>
      </div>
    </div>
  </div>
  <div style="padding:80px 60px;display:flex;flex-direction:column;gap:24px;justify-content:center;">
    <div>
      <div style="font-size:0.8rem;letter-spacing:0.2em;text-transform:uppercase;opacity:0.5;margin-bottom:10px;">Beat</div>
      <h1 style="font-size:2.4rem;font-weight:800;line-height:1.1;margin:0;">{beat}</h1>
    </div>
    <div>
      <div style="font-size:0.8rem;letter-spacing:0.2em;text-transform:uppercase;opacity:0.5;margin-bottom:8px;">Direction</div>
      <p style="font-size:1rem;opacity:0.6;line-height:1.5;margin:0;">Describe the camera, subject, and motion for this beat.</p>
    </div>
    <div style="margin-top:auto;opacity:0.4;font-size:0.8rem;">Shot {num} of 4</div>
  </div>
</div></body></html>"""
    return Template(
        id="blank_storyboard",
        name="Blank Storyboard",
        category="Starters",
        description="Four-shot storyboard: establishing, rising, climax, resolution.",
        aspect_ratio="16:9",
        icon="🎬",
        mode="storyboard",
        pages=[
            {"title": "Shot 1 — Establishing", "notes": "", "html": _shot(1, "Establishing")},
            {"title": "Shot 2 — Rising", "notes": "", "html": _shot(2, "Rising")},
            {"title": "Shot 3 — Climax", "notes": "", "html": _shot(3, "Climax")},
            {"title": "Shot 4 — Resolution", "notes": "", "html": _shot(4, "Resolution")},
        ],
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 2.3.G — Interactive seed templates (richer starting points)
# ═══════════════════════════════════════════════════════════════════════

def _landing_hero() -> Template:
    """A single-page landing scaffold: hero + 3 features + pricing + footer."""
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    font_css = get_all_fonts_css(["Inter"])
    fallback = get_fallback_stack("Inter")
    s = (
        f"<style>\n{font_css}\n"
        "  * { margin:0; padding:0; box-sizing:border-box; }\n"
        "  :root {\n"
        "    --primary:#2563EB; --secondary:#1E40AF; --accent:#F59E0B;\n"
        "    --bg:#0F172A; --text:#F8FAFC;\n"
        f"    --heading-font:'Inter', {fallback};\n"
        f"    --body-font:'Inter', {fallback};\n"
        "  }\n"
        "  html, body { margin:0; width:100%; min-height:100vh; overflow-x:hidden; }\n"
        "  body { font-family:var(--body-font); background:var(--bg); color:var(--text); line-height:1.5; }\n"
        "  .page { max-width:1440px; margin:0 auto; padding:0 clamp(16px,4vw,48px); }\n"
        "  section { padding:clamp(48px,8vw,120px) 0; }\n"
        "  h1,h2,h3 { font-family:var(--heading-font); letter-spacing:-0.02em; line-height:1.15; }\n"
        "  .btn { display:inline-block; padding:16px 32px; border-radius:10px; text-decoration:none; font-weight:600; }\n"
        "  .btn-primary { background:var(--primary); color:#fff; }\n"
        "  .btn-accent  { background:var(--accent);  color:#000; }\n"
        "  .card { padding:28px; background:rgba(255,255,255,0.04); border-radius:16px; border:1px solid rgba(255,255,255,0.06); }\n"
        "  nav { display:flex; justify-content:space-between; align-items:center; padding:24px 0; }\n"
        "  nav a { color:var(--text); text-decoration:none; opacity:0.8; margin-left:24px; }\n"
        "  footer { padding:48px 0; border-top:1px solid rgba(255,255,255,0.08); opacity:0.6; text-align:center; font-size:0.9rem; }\n"
        "  .price-card { padding:32px; border-radius:16px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); text-align:center; }\n"
        "  .price-card.featured { border-color:var(--primary); background:rgba(37,99,235,0.08); }\n"
        "  .price { font-size:3rem; font-weight:800; margin:16px 0; }\n"
        "</style>"
    )
    return Template(
        id="landing_hero",
        name="Landing — Hero + Pricing",
        category="Marketing",
        description="Full landing page: nav, hero, three feature cards, three-tier pricing, and footer.",
        aspect_ratio="landing",
        icon="🌐",
        mode="landing",
        pages=[
            {"title": "Landing", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div class="page">
  <nav>
    <strong style="font-size:1.2rem;">Brand</strong>
    <div><a href="#features">Features</a><a href="#pricing">Pricing</a><a href="#cta" class="btn btn-primary" style="padding:10px 20px;">Sign up</a></div>
  </nav>
  <section style="min-height:70vh;display:flex;flex-direction:column;justify-content:center;gap:24px;">
    <h1 style="font-size:clamp(2.5rem,6vw,4.5rem);font-weight:800;">A hero headline that sells the outcome.</h1>
    <p style="font-size:clamp(1.1rem,2vw,1.4rem);opacity:0.7;max-width:680px;">One or two sentences describing the product, the audience, and the benefit.</p>
    <div style="display:flex;gap:16px;flex-wrap:wrap;">
      <a href="#cta" class="btn btn-primary">Get started free</a>
      <a href="#features" class="btn" style="background:rgba(255,255,255,0.06);color:var(--text);">See how it works</a>
    </div>
  </section>
  <section id="features">
    <h2 style="font-size:clamp(1.8rem,3vw,2.6rem);margin-bottom:40px;">Everything you need to ship faster.</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px;">
      <div class="card"><div style="font-size:2rem;margin-bottom:16px;">⚡</div><h3 style="font-size:1.3rem;margin-bottom:12px;">Fast by default</h3><p style="opacity:0.7;">Lightweight runtime and zero-config setup.</p></div>
      <div class="card"><div style="font-size:2rem;margin-bottom:16px;">🔒</div><h3 style="font-size:1.3rem;margin-bottom:12px;">Secure</h3><p style="opacity:0.7;">Enterprise-grade encryption and access controls.</p></div>
      <div class="card"><div style="font-size:2rem;margin-bottom:16px;">🔌</div><h3 style="font-size:1.3rem;margin-bottom:12px;">Integrations</h3><p style="opacity:0.7;">Connects to the tools your team already uses.</p></div>
    </div>
  </section>
  <section id="pricing">
    <h2 style="font-size:clamp(1.8rem,3vw,2.6rem);margin-bottom:8px;text-align:center;">Simple, honest pricing</h2>
    <p style="opacity:0.6;text-align:center;margin-bottom:48px;">Cancel any time. No hidden fees.</p>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:24px;max-width:1040px;margin:0 auto;">
      <div class="price-card"><h3 style="font-size:1.2rem;">Starter</h3><div class="price">$0</div><p style="opacity:0.7;margin-bottom:24px;">For individuals trying things out.</p><a href="#cta" class="btn" style="background:rgba(255,255,255,0.08);color:var(--text);">Start free</a></div>
      <div class="price-card featured"><div style="font-size:0.75rem;letter-spacing:0.15em;color:var(--primary);margin-bottom:8px;">MOST POPULAR</div><h3 style="font-size:1.2rem;">Pro</h3><div class="price">$29<span style="font-size:1rem;opacity:0.6;">/mo</span></div><p style="opacity:0.7;margin-bottom:24px;">For small teams shipping regularly.</p><a href="#cta" class="btn btn-primary">Choose Pro</a></div>
      <div class="price-card"><h3 style="font-size:1.2rem;">Business</h3><div class="price">Custom</div><p style="opacity:0.7;margin-bottom:24px;">For orgs with advanced needs.</p><a href="#cta" class="btn" style="background:rgba(255,255,255,0.08);color:var(--text);">Contact sales</a></div>
    </div>
  </section>
  <section id="cta" style="text-align:center;">
    <h2 style="font-size:clamp(1.8rem,3vw,2.6rem);margin-bottom:16px;">Ready when you are.</h2>
    <a href="#" class="btn btn-accent">Create your account</a>
  </section>
  <footer>© Brand · <a href="#" style="color:inherit;">Privacy</a> · <a href="#" style="color:inherit;">Terms</a></footer>
</div></body></html>"""},
        ],
    )


def _app_mockup_starter() -> Template:
    """Interactive three-route mobile starter: Home / Detail / Settings with wired navigation."""
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    font_css = get_all_fonts_css(["Inter"])
    fallback = get_fallback_stack("Inter")
    s = (
        f"<style>\n{font_css}\n"
        "  * { margin:0; padding:0; box-sizing:border-box; }\n"
        "  :root {\n"
        "    --primary:#2563EB; --accent:#F59E0B;\n"
        "    --bg:#0F172A; --surface:#1E293B; --text:#F8FAFC; --muted:#94A3B8;\n"
        f"    --body-font:'Inter', {fallback};\n"
        "  }\n"
        "  html, body { margin:0; width:390px; height:844px; overflow:hidden; }\n"
        "  body { font-family:var(--body-font); background:var(--bg); color:var(--text); }\n"
        "  .screen { width:100%; height:100%; display:flex; flex-direction:column; }\n"
        "  .topbar { padding:54px 20px 12px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.06); }\n"
        "  .screen-body { flex:1; overflow-y:auto; padding:20px; }\n"
        "  .tabbar { display:flex; border-top:1px solid rgba(255,255,255,0.08); padding:8px 0 28px; background:rgba(15,23,42,0.9); }\n"
        "  .tab { flex:1; text-align:center; padding:10px; color:var(--muted); text-decoration:none; font-size:0.75rem; }\n"
        "  .tab.active { color:var(--primary); }\n"
        "  .row { display:flex; align-items:center; gap:12px; padding:14px; background:var(--surface); border-radius:12px; margin-bottom:10px; text-decoration:none; color:var(--text); }\n"
        "  .row .icon { width:40px; height:40px; border-radius:10px; background:rgba(37,99,235,0.15); display:flex; align-items:center; justify-content:center; }\n"
        "  .row .title { font-weight:600; font-size:0.95rem; }\n"
        "  .row .sub { font-size:0.8rem; color:var(--muted); }\n"
        "  .btn { display:block; padding:14px 20px; background:var(--primary); color:#fff; border-radius:12px; text-align:center; text-decoration:none; font-weight:600; }\n"
        "  .btn-ghost { background:transparent; border:1px solid rgba(255,255,255,0.15); color:var(--text); }\n"
        "  .toggle-row { display:flex; justify-content:space-between; align-items:center; padding:14px; background:var(--surface); border-radius:12px; margin-bottom:10px; }\n"
        "  .toggle { width:44px; height:26px; background:rgba(255,255,255,0.15); border-radius:13px; position:relative; border:none; cursor:pointer; }\n"
        "  .toggle[aria-pressed=\"true\"] { background:var(--primary); }\n"
        "  .toggle::after { content:''; position:absolute; top:3px; left:3px; width:20px; height:20px; background:#fff; border-radius:50%; transition:transform 0.2s; }\n"
        "  .toggle[aria-pressed=\"true\"]::after { transform:translateX(18px); }\n"
        "</style>"
    )
    def _screen(title: str, body: str, active: str) -> str:
        home_cls = " active" if active == "home" else ""
        set_cls = " active" if active == "settings" else ""
        back_btn = ("" if active == "home" else
                    '<a href="#" data-row-bot-action="navigate:home" data-row-bot-transition="slide_right" style="opacity:0.7;text-decoration:none;color:var(--text);">‹ Back</a>')
        return f"""<!DOCTYPE html><html><head>{s}</head><body>
<div class="screen">
  <div class="topbar">{back_btn or '<span></span>'}<h2 style="font-size:1.1rem;">{title}</h2><span style="opacity:0.5;">⋯</span></div>
  <div class="screen-body">{body}</div>
  <div class="tabbar">
    <a href="#" class="tab{home_cls}" data-row-bot-action="navigate:home">🏠<br>Home</a>
    <a href="#" class="tab{set_cls}" data-row-bot-action="navigate:settings">⚙<br>Settings</a>
  </div>
</div></body></html>"""
    home_body = (
        '<p style="color:var(--muted);margin-bottom:16px;">Recent items</p>'
        '<a class="row" href="#" data-row-bot-action="navigate:detail" data-row-bot-transition="slide_left"><div class="icon">📄</div><div><div class="title">First item</div><div class="sub">Tap to view details</div></div></a>'
        '<a class="row" href="#" data-row-bot-action="navigate:detail" data-row-bot-transition="slide_left"><div class="icon">📄</div><div><div class="title">Second item</div><div class="sub">Tap to view details</div></div></a>'
        '<a class="row" href="#" data-row-bot-action="navigate:detail" data-row-bot-transition="slide_left"><div class="icon">📄</div><div><div class="title">Third item</div><div class="sub">Tap to view details</div></div></a>'
    )
    detail_body = (
        '<h1 style="font-size:1.6rem;font-weight:700;margin-bottom:8px;">Item title</h1>'
        '<p style="color:var(--muted);font-size:0.9rem;margin-bottom:20px;">Subheading or metadata line</p>'
        '<p style="line-height:1.6;margin-bottom:24px;">Detail body copy. Replace this with the actual record contents, media, or description.</p>'
        '<a class="btn" href="#" data-row-bot-action="navigate:home" data-row-bot-transition="slide_right">Primary action</a>'
        '<div style="height:10px;"></div>'
        '<a class="btn btn-ghost" href="#" data-row-bot-action="navigate:home" data-row-bot-transition="slide_right">Back to list</a>'
    )
    settings_body = (
        '<p style="color:var(--muted);margin-bottom:16px;">Preferences</p>'
        '<div class="toggle-row"><span>Notifications</span><button class="toggle" aria-pressed="true" data-row-bot-action="toggle_state:notifications"></button></div>'
        '<div class="toggle-row"><span>Dark mode</span><button class="toggle" aria-pressed="true" data-row-bot-action="toggle_state:dark"></button></div>'
        '<div class="toggle-row"><span>Sync over cellular</span><button class="toggle" aria-pressed="false" data-row-bot-action="toggle_state:cellular"></button></div>'
        '<div style="height:16px;"></div>'
        '<a class="btn btn-ghost" href="#" data-row-bot-action="navigate:home" data-row-bot-transition="slide_right">Done</a>'
    )
    return Template(
        id="app_mockup_starter",
        name="App Mockup — 3 Routes",
        category="Product",
        description="Phone scaffold: Home list → Detail view, plus a Settings screen with toggles and a tab bar.",
        aspect_ratio="phone",
        icon="📱",
        mode="app_mockup",
        pages=[
            {"title": "Home", "notes": "List screen; rows navigate to Detail.", "html": _screen("Home", home_body, "home")},
            {"title": "Detail", "notes": "Detail view for a single item.", "html": _screen("Detail", detail_body, "home")},
            {"title": "Settings", "notes": "Preferences with toggle rows.", "html": _screen("Settings", settings_body, "settings")},
        ],
    )


def _storyboard_4shot() -> Template:
    """Four-shot cinematic storyboard with beats, camera direction, and captions."""
    s = _base_style()
    def _shot(num: int, beat: str, caption: str, camera: str, subject: str) -> str:
        return f"""<!DOCTYPE html><html><head>{s}</head><body>
<div style="display:grid;grid-template-columns:1.2fr 1fr;height:100%;">
  <div style="background:linear-gradient(135deg,rgba(37,99,235,0.15),rgba(15,23,42,0.9));display:flex;flex-direction:column;justify-content:center;align-items:center;padding:64px;gap:20px;border-right:1px solid rgba(255,255,255,0.08);">
    <div style="font-size:0.85rem;letter-spacing:0.25em;text-transform:uppercase;opacity:0.5;">Shot {num:02d} — {beat}</div>
    <div data-row-bot-shot-visual="1" style="width:100%;max-width:520px;aspect-ratio:16/9;border:2px dashed rgba(255,255,255,0.28);border-radius:14px;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,0.03);">
      <div style="text-align:center;opacity:0.55;padding:16px;">
        <div style="font-size:2.2rem;line-height:1;margin-bottom:8px;">🎬</div>
        <div style="font-size:0.85rem;letter-spacing:0.14em;text-transform:uppercase;opacity:0.8;">Shot visual</div>
        <div style="font-size:0.75rem;opacity:0.7;margin-top:6px;">Ask the agent to generate or animate a still for this shot.</div>
      </div>
    </div>
    <p style="font-size:1.05rem;opacity:0.75;text-align:center;max-width:520px;line-height:1.45;">{subject}</p>
  </div>
  <div style="padding:80px 60px;display:flex;flex-direction:column;gap:28px;justify-content:center;">
    <div>
      <div style="font-size:0.8rem;letter-spacing:0.2em;text-transform:uppercase;opacity:0.5;margin-bottom:8px;">Caption</div>
      <p style="font-size:1.4rem;font-weight:600;line-height:1.3;">{caption}</p>
    </div>
    <div>
      <div style="font-size:0.8rem;letter-spacing:0.2em;text-transform:uppercase;opacity:0.5;margin-bottom:8px;">Camera</div>
      <p style="font-size:1rem;opacity:0.75;line-height:1.5;">{camera}</p>
    </div>
    <div style="margin-top:auto;display:flex;justify-content:space-between;opacity:0.4;font-size:0.8rem;">
      <span>Board {num} of 4</span><span>00:{num * 6:02d}s</span>
    </div>
  </div>
</div></body></html>"""
    return Template(
        id="storyboard_4shot",
        name="Storyboard — 4-Shot Sequence",
        category="Video",
        description="Classic four-beat story arc: establishing, rising, climax, resolution — with camera direction and captions.",
        aspect_ratio="16:9",
        icon="🎬",
        mode="storyboard",
        pages=[
            {"title": "Shot 01 — Establishing", "notes": "Set the scene and introduce the world.", "html": _shot(1, "Establishing", "Open on the world of the story.", "Wide establishing shot, slow push-in. Natural light.", "Introduce the setting, mood, and protagonist in frame.")},
            {"title": "Shot 02 — Rising", "notes": "Introduce tension or the inciting moment.", "html": _shot(2, "Rising", "Something changes — the stakes appear.", "Medium shot, handheld tilt. Tighter framing on the subject.", "The conflict or problem surfaces; energy builds.")},
            {"title": "Shot 03 — Climax", "notes": "The peak moment.", "html": _shot(3, "Climax", "The turning point — maximum tension.", "Close-up, locked-off. Hard key light, dramatic contrast.", "The decisive action or reveal lands here.")},
            {"title": "Shot 04 — Resolution", "notes": "Aftermath and close.", "html": _shot(4, "Resolution", "The new equilibrium. Aftermath and exhale.", "Wide pull-back, slow dolly out. Softer, warmer light.", "Land the emotional payoff and close the loop.")},
        ],
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 2.3.H — Audit additions
# ═══════════════════════════════════════════════════════════════════════

def _dashboard_desktop() -> Template:
    """SaaS dashboard desktop mockup with sidebar, KPI row, chart, table, and a Settings route."""
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    font_css = get_all_fonts_css(["Inter"])
    fallback = get_fallback_stack("Inter")
    s = (
        f"<style>\n{font_css}\n"
        "  * { margin:0; padding:0; box-sizing:border-box; }\n"
        "  :root {\n"
        "    --primary:#2563EB; --accent:#F59E0B;\n"
        "    --bg:#0B1220; --surface:#111A2E; --surface-2:#1A2440;\n"
        "    --text:#F8FAFC; --muted:#94A3B8; --border:rgba(255,255,255,0.06);\n"
        f"    --body-font:'Inter', {fallback};\n"
        "  }\n"
        "  html, body { margin:0; width:1440px; height:900px; overflow:hidden; }\n"
        "  body { font-family:var(--body-font); background:var(--bg); color:var(--text); font-size:14px; }\n"
        "  .shell { display:flex; height:100%; }\n"
        "  aside { width:240px; background:var(--surface); border-right:1px solid var(--border); padding:24px 16px; display:flex; flex-direction:column; gap:4px; }\n"
        "  aside .brand { font-weight:700; font-size:1.1rem; padding:8px 12px 20px; }\n"
        "  aside a { display:flex; align-items:center; gap:10px; padding:10px 12px; color:var(--muted); text-decoration:none; border-radius:8px; font-size:0.9rem; }\n"
        "  aside a:hover { background:var(--surface-2); color:var(--text); }\n"
        "  aside a.active { background:rgba(37,99,235,0.15); color:var(--primary); }\n"
        "  main { flex:1; display:flex; flex-direction:column; overflow:auto; }\n"
        "  .top { display:flex; justify-content:space-between; align-items:center; padding:20px 32px; border-bottom:1px solid var(--border); }\n"
        "  .top h1 { font-size:1.4rem; font-weight:700; }\n"
        "  .content { padding:28px 32px; display:flex; flex-direction:column; gap:24px; }\n"
        "  .kpi-row { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; }\n"
        "  .kpi { padding:20px; background:var(--surface); border:1px solid var(--border); border-radius:12px; }\n"
        "  .kpi .label { color:var(--muted); font-size:0.8rem; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px; }\n"
        "  .kpi .value { font-size:1.9rem; font-weight:700; }\n"
        "  .kpi .delta { color:#22c55e; font-size:0.8rem; margin-top:4px; }\n"
        "  .card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:24px; }\n"
        "  .row2 { display:grid; grid-template-columns:2fr 1fr; gap:16px; }\n"
        "  .chart { height:240px; display:flex; align-items:end; gap:12px; padding:8px 0; }\n"
        "  .bar { flex:1; background:linear-gradient(180deg,var(--primary),rgba(37,99,235,0.3)); border-radius:6px 6px 0 0; }\n"
        "  table { width:100%; border-collapse:collapse; }\n"
        "  th,td { padding:10px 8px; text-align:left; border-bottom:1px solid var(--border); font-size:0.88rem; }\n"
        "  th { color:var(--muted); font-weight:500; text-transform:uppercase; letter-spacing:0.04em; font-size:0.7rem; }\n"
        "  .pill { display:inline-block; padding:3px 10px; border-radius:999px; font-size:0.72rem; font-weight:600; }\n"
        "  .pill.ok { background:rgba(34,197,94,0.15); color:#22c55e; }\n"
        "  .pill.warn { background:rgba(245,158,11,0.15); color:var(--accent); }\n"
        "  .btn { padding:8px 14px; background:var(--primary); color:#fff; border-radius:8px; text-decoration:none; font-weight:600; font-size:0.85rem; }\n"
        "  .field { display:flex; flex-direction:column; gap:6px; margin-bottom:16px; }\n"
        "  .field label { color:var(--muted); font-size:0.8rem; }\n"
        "  .field input { padding:10px 12px; background:var(--surface-2); border:1px solid var(--border); border-radius:8px; color:var(--text); font-family:inherit; font-size:0.9rem; }\n"
        "</style>"
    )
    dashboard_body = """
  <section class="content">
    <div class="kpi-row">
      <div class="kpi"><div class="label">Revenue</div><div class="value">$128.4k</div><div class="delta">+12.4%</div></div>
      <div class="kpi"><div class="label">Active users</div><div class="value">8,214</div><div class="delta">+4.1%</div></div>
      <div class="kpi"><div class="label">Conversion</div><div class="value">3.8%</div><div class="delta">+0.3pt</div></div>
      <div class="kpi"><div class="label">NPS</div><div class="value">64</div><div class="delta">+6</div></div>
    </div>
    <div class="row2">
      <div class="card">
        <div style="display:flex;justify-content:space-between;margin-bottom:16px;"><strong>Weekly signups</strong><span style="color:var(--muted);font-size:0.8rem;">Last 12 weeks</span></div>
        <div class="chart">
          <div class="bar" style="height:30%;"></div><div class="bar" style="height:45%;"></div><div class="bar" style="height:38%;"></div>
          <div class="bar" style="height:62%;"></div><div class="bar" style="height:51%;"></div><div class="bar" style="height:70%;"></div>
          <div class="bar" style="height:58%;"></div><div class="bar" style="height:74%;"></div><div class="bar" style="height:82%;"></div>
          <div class="bar" style="height:68%;"></div><div class="bar" style="height:89%;"></div><div class="bar" style="height:95%;"></div>
        </div>
      </div>
      <div class="card">
        <strong>Top sources</strong>
        <div style="display:flex;flex-direction:column;gap:12px;margin-top:16px;">
          <div><div style="display:flex;justify-content:space-between;font-size:0.85rem;margin-bottom:4px;"><span>Organic</span><span>42%</span></div><div style="height:6px;background:var(--surface-2);border-radius:3px;"><div style="width:42%;height:100%;background:var(--primary);border-radius:3px;"></div></div></div>
          <div><div style="display:flex;justify-content:space-between;font-size:0.85rem;margin-bottom:4px;"><span>Paid</span><span>28%</span></div><div style="height:6px;background:var(--surface-2);border-radius:3px;"><div style="width:28%;height:100%;background:var(--primary);border-radius:3px;"></div></div></div>
          <div><div style="display:flex;justify-content:space-between;font-size:0.85rem;margin-bottom:4px;"><span>Referral</span><span>18%</span></div><div style="height:6px;background:var(--surface-2);border-radius:3px;"><div style="width:18%;height:100%;background:var(--primary);border-radius:3px;"></div></div></div>
          <div><div style="display:flex;justify-content:space-between;font-size:0.85rem;margin-bottom:4px;"><span>Direct</span><span>12%</span></div><div style="height:6px;background:var(--surface-2);border-radius:3px;"><div style="width:12%;height:100%;background:var(--primary);border-radius:3px;"></div></div></div>
        </div>
      </div>
    </div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;"><strong>Recent accounts</strong><a class="btn" href="#">New account</a></div>
      <table>
        <thead><tr><th>Account</th><th>Plan</th><th>MRR</th><th>Status</th><th>Last seen</th></tr></thead>
        <tbody>
          <tr><td>Acme Corp</td><td>Enterprise</td><td>$4,200</td><td><span class="pill ok">Active</span></td><td>2h ago</td></tr>
          <tr><td>Globex</td><td>Pro</td><td>$890</td><td><span class="pill ok">Active</span></td><td>5h ago</td></tr>
          <tr><td>Initech</td><td>Pro</td><td>$640</td><td><span class="pill warn">Trial</span></td><td>yesterday</td></tr>
          <tr><td>Umbrella</td><td>Starter</td><td>$120</td><td><span class="pill ok">Active</span></td><td>3d ago</td></tr>
        </tbody>
      </table>
    </div>
  </section>
"""
    settings_body = """
  <section class="content">
    <div class="card" style="max-width:640px;">
      <h3 style="font-size:1.1rem;margin-bottom:20px;">Workspace settings</h3>
      <div class="field"><label>Workspace name</label><input value="Acme Corp"></div>
      <div class="field"><label>Billing email</label><input value="billing@acme.example"></div>
      <div class="field"><label>Time zone</label><input value="America/Los_Angeles"></div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:8px;"><a href="#" style="color:var(--muted);text-decoration:none;align-self:center;font-size:0.9rem;">Cancel</a><a class="btn" href="#">Save changes</a></div>
    </div>
  </section>
"""
    def _route(route_id: str, title: str, active: str, body: str) -> str:
        dash_cls = ' class="active"' if active == "dashboard" else ""
        set_cls = ' class="active"' if active == "settings" else ""
        return f"""<!DOCTYPE html><html><head>{s}</head><body>
<div class="shell" data-row-bot-route-host="1" data-row-bot-route="{route_id}">
  <aside>
    <div class="brand">◆ Workspace</div>
    <a href="#" data-row-bot-action="navigate:dashboard"{dash_cls}>📊 Dashboard</a>
    <a href="#">👥 Customers</a>
    <a href="#">💳 Billing</a>
    <a href="#" data-row-bot-action="navigate:settings"{set_cls}>⚙ Settings</a>
    <div style="flex:1;"></div>
    <a href="#">❓ Help</a>
  </aside>
  <main>
    <div class="top"><h1>{title}</h1><div style="display:flex;gap:12px;align-items:center;"><span style="color:var(--muted);font-size:0.85rem;">Apr 2026</span><div style="width:32px;height:32px;border-radius:50%;background:var(--surface-2);"></div></div></div>
    {body}
  </main>
</div></body></html>"""
    return Template(
        id="dashboard_desktop",
        name="Dashboard — Desktop SaaS",
        category="Product",
        description="Desktop SaaS shell with sidebar, KPI row, charts, table, and a linked Settings route.",
        aspect_ratio="desktop",
        icon="🖥",
        mode="app_mockup",
        pages=[
            {"title": "Dashboard", "notes": "KPI + chart + recent accounts table.", "html": _route("dashboard", "Dashboard", "dashboard", dashboard_body)},
            {"title": "Settings", "notes": "Workspace settings form.", "html": _route("settings", "Settings", "settings", settings_body)},
        ],
    )


def _resume_onepage() -> Template:
    """Classic one-page CV / resume, A4 portrait."""
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    font_css = get_all_fonts_css(["Inter"])
    fallback = get_fallback_stack("Inter")
    s = (
        f"<style>\n{font_css}\n"
        "  * { margin:0; padding:0; box-sizing:border-box; }\n"
        "  :root {\n"
        "    --primary:#1E40AF; --accent:#F59E0B;\n"
        "    --bg:#FFFFFF; --text:#1F2937; --muted:#6B7280; --border:#E5E7EB;\n"
        f"    --body-font:'Inter', {fallback};\n"
        "  }\n"
        "  html, body { margin:0; width:794px; height:1123px; overflow:hidden; }\n"
        "  body { font-family:var(--body-font); background:var(--bg); color:var(--text); font-size:11pt; line-height:1.5; }\n"
        "  .page { padding:56px 64px; display:flex; flex-direction:column; gap:24px; }\n"
        "  .header { display:flex; justify-content:space-between; align-items:flex-end; border-bottom:2px solid var(--primary); padding-bottom:16px; }\n"
        "  .header h1 { font-size:2rem; font-weight:800; color:var(--primary); letter-spacing:-0.02em; }\n"
        "  .header .role { color:var(--muted); font-size:1.05rem; margin-top:4px; }\n"
        "  .header .contact { text-align:right; color:var(--muted); font-size:0.85rem; line-height:1.6; }\n"
        "  section { display:flex; flex-direction:column; gap:10px; }\n"
        "  h2 { font-size:0.8rem; color:var(--primary); text-transform:uppercase; letter-spacing:0.15em; }\n"
        "  .entry { display:flex; flex-direction:column; gap:4px; }\n"
        "  .entry .row { display:flex; justify-content:space-between; align-items:baseline; }\n"
        "  .entry .role-name { font-weight:600; }\n"
        "  .entry .meta { color:var(--muted); font-size:0.85rem; }\n"
        "  .entry ul { padding-left:18px; margin-top:4px; color:var(--text); }\n"
        "  .entry li { margin-bottom:2px; }\n"
        "  .two-col { display:grid; grid-template-columns:1fr 1fr; gap:20px; }\n"
        "  .skills { display:flex; flex-wrap:wrap; gap:6px; }\n"
        "  .skill { padding:4px 10px; background:#F3F4F6; border-radius:6px; font-size:0.82rem; }\n"
        "</style>"
    )
    return Template(
        id="resume_onepage",
        name="Resume — One Page",
        category="Documents",
        description="Single-page CV with header, summary, experience, skills, and education.",
        aspect_ratio="A4",
        icon="📃",
        mode="document",
        pages=[
            {"title": "Resume", "notes": "Classic one-page CV.", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div class="page">
  <div class="header">
    <div>
      <h1>Your Name</h1>
      <div class="role">Senior Software Engineer</div>
    </div>
    <div class="contact">
      you@example.com<br>
      +1 (555) 010-0000<br>
      linkedin.com/in/you · github.com/you
    </div>
  </div>
  <section>
    <h2>Summary</h2>
    <p>Two-line summary describing your specialty, years of experience, and the kind of impact you want to have in your next role.</p>
  </section>
  <section>
    <h2>Experience</h2>
    <div class="entry">
      <div class="row"><span class="role-name">Senior Engineer — Company A</span><span class="meta">2023 — Present</span></div>
      <ul>
        <li>Led a 4-person team to ship Feature X, driving a 22% lift in weekly active use.</li>
        <li>Reduced p95 latency from 1.2s to 380ms by redesigning the caching layer.</li>
        <li>Mentored three engineers from mid to senior level.</li>
      </ul>
    </div>
    <div class="entry">
      <div class="row"><span class="role-name">Engineer — Company B</span><span class="meta">2020 — 2023</span></div>
      <ul>
        <li>Built the realtime collaboration engine used by 60k daily users.</li>
        <li>Owned the migration from a monolith to 8 services with zero downtime.</li>
      </ul>
    </div>
    <div class="entry">
      <div class="row"><span class="role-name">Engineer — Company C</span><span class="meta">2018 — 2020</span></div>
      <ul>
        <li>Shipped the mobile payments SDK adopted by 40+ partner apps.</li>
      </ul>
    </div>
  </section>
  <div class="two-col">
    <section>
      <h2>Skills</h2>
      <div class="skills">
        <span class="skill">Python</span><span class="skill">TypeScript</span><span class="skill">Go</span>
        <span class="skill">Postgres</span><span class="skill">Redis</span><span class="skill">AWS</span>
        <span class="skill">Kubernetes</span><span class="skill">Distributed systems</span><span class="skill">Leadership</span>
      </div>
    </section>
    <section>
      <h2>Education</h2>
      <div class="entry">
        <div class="row"><span class="role-name">B.Sc. Computer Science</span><span class="meta">2014 — 2018</span></div>
        <div class="meta">University Name · GPA 3.8</div>
      </div>
    </section>
  </div>
</div></body></html>"""},
        ],
    )


def _landing_product() -> Template:
    """Product-focused landing page: hero + features + testimonials + integrations + CTA. Distinct from landing_hero (pricing-focused)."""
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    font_css = get_all_fonts_css(["Inter"])
    fallback = get_fallback_stack("Inter")
    s = (
        f"<style>\n{font_css}\n"
        "  * { margin:0; padding:0; box-sizing:border-box; }\n"
        "  :root {\n"
        "    --primary:#7C3AED; --secondary:#4338CA; --accent:#F59E0B;\n"
        "    --bg:#0B0F1E; --surface:rgba(255,255,255,0.04); --text:#F8FAFC; --muted:#94A3B8;\n"
        f"    --heading-font:'Inter', {fallback};\n"
        f"    --body-font:'Inter', {fallback};\n"
        "  }\n"
        "  html, body { margin:0; width:100%; min-height:100vh; overflow-x:hidden; }\n"
        "  body { font-family:var(--body-font); background:var(--bg); color:var(--text); line-height:1.5; }\n"
        "  .page { max-width:1440px; margin:0 auto; padding:0 clamp(16px,4vw,48px); }\n"
        "  section { padding:clamp(48px,8vw,120px) 0; }\n"
        "  h1,h2,h3 { font-family:var(--heading-font); letter-spacing:-0.02em; line-height:1.15; }\n"
        "  nav { display:flex; justify-content:space-between; align-items:center; padding:24px 0; }\n"
        "  nav a { color:var(--text); text-decoration:none; opacity:0.8; margin-left:24px; }\n"
        "  .btn { display:inline-block; padding:14px 28px; border-radius:10px; text-decoration:none; font-weight:600; }\n"
        "  .btn-primary { background:linear-gradient(135deg,var(--primary),var(--secondary)); color:#fff; }\n"
        "  .btn-ghost { background:rgba(255,255,255,0.06); color:var(--text); }\n"
        "  .screenshot { margin-top:48px; border-radius:20px; border:1px solid rgba(255,255,255,0.08); background:linear-gradient(135deg,rgba(124,58,237,0.15),rgba(15,23,42,0.9)); padding:32px; aspect-ratio:16/9; display:flex; align-items:center; justify-content:center; color:var(--muted); }\n"
        "  .feature-row { display:grid; grid-template-columns:1fr 1fr; gap:48px; align-items:center; margin-bottom:64px; }\n"
        "  .feature-row:nth-child(even) .copy { order:2; }\n"
        "  .feature-row .visual { border-radius:16px; padding:48px; background:linear-gradient(135deg,rgba(124,58,237,0.12),rgba(67,56,202,0.08)); border:1px solid rgba(255,255,255,0.06); aspect-ratio:4/3; display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:2.2rem; }\n"
        "  .testimonials { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:24px; }\n"
        "  .testimonial { padding:28px; background:var(--surface); border:1px solid rgba(255,255,255,0.06); border-radius:16px; }\n"
        "  .testimonial p { margin-bottom:20px; opacity:0.9; }\n"
        "  .testimonial .author { display:flex; align-items:center; gap:12px; }\n"
        "  .testimonial .avatar { width:40px; height:40px; border-radius:50%; background:linear-gradient(135deg,var(--primary),var(--accent)); }\n"
        "  .testimonial .name { font-weight:600; font-size:0.9rem; }\n"
        "  .testimonial .role { color:var(--muted); font-size:0.8rem; }\n"
        "  .logos { display:flex; justify-content:center; flex-wrap:wrap; gap:48px; opacity:0.6; }\n"
        "  .logo { padding:10px 18px; border:1px solid rgba(255,255,255,0.12); border-radius:8px; font-weight:600; letter-spacing:0.04em; }\n"
        "  footer { padding:48px 0; border-top:1px solid rgba(255,255,255,0.08); opacity:0.6; text-align:center; font-size:0.9rem; }\n"
        "</style>"
    )
    return Template(
        id="landing_product",
        name="Landing — Product Showcase",
        category="Marketing",
        description="Product-focused landing: hero + screenshot, 3 alternating feature rows, testimonials, integration logos, CTA.",
        aspect_ratio="landing",
        icon="🛍",
        mode="landing",
        pages=[
            {"title": "Landing", "notes": "", "html": f"""<!DOCTYPE html><html><head>{s}</head><body>
<div class="page">
  <nav>
    <strong style="font-size:1.2rem;">◆ Product</strong>
    <div><a href="#features">Features</a><a href="#testimonials">Customers</a><a href="#cta" class="btn btn-primary" style="padding:10px 20px;margin-left:24px;">Start free trial</a></div>
  </nav>
  <section style="text-align:center;">
    <div style="display:inline-block;padding:6px 16px;background:rgba(124,58,237,0.15);border:1px solid rgba(124,58,237,0.3);border-radius:20px;font-size:0.85rem;margin-bottom:24px;">✨ Now with AI-powered insights</div>
    <h1 style="font-size:clamp(2.8rem,6vw,5rem);font-weight:800;max-width:960px;margin:0 auto 20px;">The all-in-one platform your team actually enjoys using.</h1>
    <p style="font-size:clamp(1.1rem,2vw,1.4rem);opacity:0.7;max-width:640px;margin:0 auto 32px;">One workspace for plans, docs, and updates — so the work stays in flow and the context stays in one place.</p>
    <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;">
      <a href="#cta" class="btn btn-primary">Start free — no credit card</a>
      <a href="#features" class="btn btn-ghost">Watch the tour</a>
    </div>
    <div class="screenshot">🖼 Product screenshot placeholder</div>
  </section>
  <section id="features">
    <h2 style="font-size:clamp(1.8rem,3vw,2.6rem);margin-bottom:48px;text-align:center;">Everything, in one place.</h2>
    <div class="feature-row">
      <div class="copy"><h3 style="font-size:1.8rem;margin-bottom:16px;">Plan work the way your team thinks.</h3><p style="opacity:0.75;font-size:1.05rem;">Boards, lists, calendars, and docs — all linked so context never gets lost in another tab.</p></div>
      <div class="visual">📋</div>
    </div>
    <div class="feature-row">
      <div class="copy"><h3 style="font-size:1.8rem;margin-bottom:16px;">Write, share, and review together.</h3><p style="opacity:0.75;font-size:1.05rem;">Real-time collaborative docs with threaded comments, suggestions, and version history.</p></div>
      <div class="visual">📝</div>
    </div>
    <div class="feature-row">
      <div class="copy"><h3 style="font-size:1.8rem;margin-bottom:16px;">Let AI do the busywork.</h3><p style="opacity:0.75;font-size:1.05rem;">Summaries, action items, and status rollups — generated automatically from your team's work.</p></div>
      <div class="visual">🤖</div>
    </div>
  </section>
  <section id="testimonials">
    <h2 style="font-size:clamp(1.8rem,3vw,2.4rem);margin-bottom:48px;text-align:center;">Teams are shipping faster.</h2>
    <div class="testimonials">
      <div class="testimonial"><p>"Cut our weekly status meetings by half. The AI summaries are eerily good."</p><div class="author"><div class="avatar"></div><div><div class="name">Maya Chen</div><div class="role">Head of Product · Acme</div></div></div></div>
      <div class="testimonial"><p>"Finally, a tool my engineers don't complain about. Onboarding was done in an afternoon."</p><div class="author"><div class="avatar"></div><div><div class="name">David Okoye</div><div class="role">VP Engineering · Globex</div></div></div></div>
      <div class="testimonial"><p>"We replaced three tools with this. Same cost, a lot less context-switching."</p><div class="author"><div class="avatar"></div><div><div class="name">Priya Shah</div><div class="role">Chief of Staff · Initech</div></div></div></div>
    </div>
  </section>
  <section>
    <p style="text-align:center;color:var(--muted);text-transform:uppercase;letter-spacing:0.15em;font-size:0.8rem;margin-bottom:32px;">Connects with the tools you already use</p>
    <div class="logos"><div class="logo">SLACK</div><div class="logo">GITHUB</div><div class="logo">FIGMA</div><div class="logo">LINEAR</div><div class="logo">NOTION</div><div class="logo">ZOOM</div></div>
  </section>
  <section id="cta" style="text-align:center;">
    <h2 style="font-size:clamp(2rem,3vw,2.8rem);margin-bottom:16px;">Try it free for 14 days.</h2>
    <p style="opacity:0.7;margin-bottom:28px;">No credit card. Cancel anytime.</p>
    <a href="#" class="btn btn-primary">Create your workspace</a>
  </section>
  <footer>© Product · <a href="#" style="color:inherit;">Privacy</a> · <a href="#" style="color:inherit;">Terms</a> · <a href="#" style="color:inherit;">Status</a></footer>
</div></body></html>"""},
        ],
    )


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════

_TEMPLATES: list[Template] | None = None


def _init_templates() -> list[Template]:
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = [
            # Phase 2.3.B — Blank starters per mode, pinned first in
            # the gallery under the "Starters" category.
            _blank_deck(),
            _blank_document(),
            _blank_landing(),
            _blank_app_mockup(),
            _blank_storyboard(),
            # Legacy blank_canvas kept as a deck-mode alias for any
            # bookmarks / tool callers that pass the old id.
            _blank_canvas(),
            # Phase 2.3.G — Interactive seed templates for the three
            # newer modes. Richer starting points than the blanks.
            _landing_hero(),
            _app_mockup_starter(),
            _storyboard_4shot(),
            _pitch_deck(),
            _status_report(),
            _marketing_one_pager(),
            _product_launch(),
            _social_media(),
            _wireframe_kit(),
            _dashboard_desktop(),
            _resume_onepage(),
            _landing_product(),
        ]
    return _TEMPLATES


def get_templates() -> list[Template]:
    """Return all available templates."""
    return list(_init_templates())


def get_template(template_id: str) -> Template | None:
    """Return a single template by ID, or None."""
    for t in _init_templates():
        if t.id == template_id:
            return t
    return None


def get_template_categories() -> list[str]:
    """Return distinct template categories in display order."""
    seen = []
    for t in _init_templates():
        if t.category not in seen:
            seen.append(t.category)
    return seen


def get_templates_for_mode(mode: str) -> list[Template]:
    """Return templates whose ``mode`` matches the given designer mode.

    Phase 2.3.A — gallery filtering. Unknown / empty modes fall back to
    the full list so callers that don't yet carry a mode (legacy paths)
    still see every template. Blank starters are always included in
    their own mode's slice; the generic ``blank_canvas`` alias surfaces
    under ``deck`` only.
    """

    key = (mode or "").strip().lower()
    if not key:
        return list(_init_templates())
    return [t for t in _init_templates() if (t.mode or "deck").lower() == key]
