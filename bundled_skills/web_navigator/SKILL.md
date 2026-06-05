---
name: web_navigator
display_name: Web Navigator
icon: "🌐"
description: Strategic patterns for effective browser automation — research, forms, and data extraction.
enabled_by_default: true
version: "1.0"
tags:
  - browser
  - automation
activation:
  phrases:
    - browse this site
    - extract pricing
    - inspect a website
    - browser workflow
    - web data extraction
  keywords:
    - browse
    - site
    - website
    - pricing
    - extract
    - browser
    - forms
  negative_phrases:
    - meeting notes
    - human tone
  examples:
    - Browse this site and extract pricing info
author: Row-Bot
---

When the user asks you to **browse a website**, **fill out a form**, **extract data from a page**, or perform any multi-step browser interaction, apply these strategic patterns:

## Research Patterns

1. **Snapshot First, Act Second** — After every navigation or interaction, always read the snapshot before deciding your next action. Never chain clicks blindly.
2. **Progressive Disclosure** — Start with the visible content. If you need more, scroll down and take another snapshot. Don't assume content below the fold exists or doesn't.
3. **Multi-Tab Research** — When comparing options across sites (prices, reviews, specs), open each source in a separate tab. Gather all data first, then synthesise. This avoids losing context by navigating away.
4. **Read URL for Bulk Text** — If you only need the text content of a page (no interaction needed), use `read_url` instead of the browser. Reserve the browser for when you need to click, scroll, or interact.

## Form Filling

5. **Survey the Form** — Before filling anything, take a snapshot to understand all the fields. Plan the fill order based on what you see.
6. **Type Carefully** — Use `browser_type` with the correct ref for each field. After filling critical fields (payment, addresses), snapshot to verify the values took.
7. **Handle Dropdowns and Selects** — Click the dropdown first, wait for the snapshot showing options, then click the desired option. Don't try to type into select elements.
8. **Confirm Before Submit** — Always snapshot and summarise what you've filled in before clicking a submit button. Let the user verify.

## Data Extraction

9. **Structured Extraction** — When extracting tabular data (product listings, search results, comparison tables), present it in a clean markdown table or structured format.
10. **Pagination** — If the data spans multiple pages, mention how many pages there are and ask whether to continue after the first page. Don't silently paginate through 50 pages.
11. **Save Long Results** — For large extractions, offer to save results to a workspace file or memory rather than dumping everything into the chat.

## Error Recovery

12. **Stale Refs** — If a click fails or doesn't produce the expected result, take a fresh snapshot. Page state may have changed (dynamic content, overlays, redirects).
13. **Pop-ups and Overlays** — Cookie banners, newsletter pop-ups, and chat widgets are common. Look for dismiss/close buttons in the snapshot and clear them before proceeding with the main task.
14. **Login Walls** — If a login page appears unexpectedly, tell the user immediately and ask them to log in via the visible browser window. Snapshot after they confirm they're done.
