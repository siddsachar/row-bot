# Security Policy

How Row-Bot keeps its agent in bounds, and what is not a boundary, is described
in [How Row-Bot Keeps An Agent In Bounds](https://row-bot.ai/docs/privacy-safety/agent-in-bounds).

## Reporting a vulnerability

Please do not open a public GitHub issue for security problems. Report them
privately, either way:

- **GitHub**: choose **Report a vulnerability** on the repository's
  [Security](https://github.com/siddsachar/row-bot/security) tab. It opens a
  private advisory that only the maintainer can see.
- **Email**: syd@row-bot.ai

Include as much detail as you can safely share:

- Affected version or commit
- Operating system
- Steps to reproduce
- Impact and severity estimate
- Logs, screenshots, or proof-of-concept code if relevant

## What happens next

- I will try to acknowledge reports within 7 days.
- If the issue is valid, I will coordinate a fix and publish a security release
  when needed.
- Anything with real impact gets a published GitHub security advisory (GHSA),
  with a CVE ID when its impact warrants one.
- Reporters are credited unless they prefer to stay anonymous.

## Scope

Security-sensitive areas include:

- Shell execution and approval gates
- Browser automation
- Prompt-injection defenses
- Local file access and workspace isolation
- The Developer sandbox and its network gate
- Auto-update manifest, SHA256 verification, and code-signature checks
- Channel adapters and webhook/tunnel handling
- API key storage and OAuth flows

## Supported versions

Only the latest stable release is actively supported. Critical fixes may be
backported when practical.

## Past advisories

| Published | Advisory | Severity | Fixed in |
| --- | --- | --- | --- |
| 13 Nov 2026 | [GHSA-68j6-243q-h2hp](https://github.com/siddsachar/row-bot/security/advisories/GHSA-68j6-243q-h2hp): an unreadable tool settings file re-enabled switched-off tools and cleared the shell's blocked-commands list | Low | 5.3.3 |
