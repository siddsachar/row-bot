---
name: task_automation
display_name: Task Automation
icon: "⚙️"
description: Design effective automated workflows using scheduled tasks, clear messages, and delivery channels.
enabled_by_default: true
version: "4.2"
tags:
  - automation
  - productivity
activation:
  phrases:
    - recurring task
    - scheduled task
    - every monday
    - automate this
    - reminder workflow
  keywords:
    - recurring
    - schedule
    - scheduled
    - task
    - workflow
    - monday
    - reminder
  negative_phrases:
    - meeting notes
    - human tone
  examples:
    - Help me set up a recurring task every Monday
author: Row-Bot
---

When the user wants to **set up automations**, **create recurring workflows**, **schedule something**, or **set a reminder**, apply these principles:

## How Workflows Work

A workflow is a chat that runs on its own. Each run sends its message to the workflow's own conversation, in the background, as a chat turn: it has every tool a chat has, finds apps and tools the same way, and asks before actions at the workflow's approval level, on the same cards. Every run is a new turn in that same conversation, so a run can see what earlier runs found.

Each workflow also keeps short **notes** between runs. A run's first message starts with them, and the run keeps them current with `task_update(task_id=..., notes=...)` before it ends: what it checked and when, ids it will need again, what's left. So a run doesn't check again what an earlier one already did.

There are three kinds:
- **Message workflows** — usually one message, sometimes a few. A second message is sent after the first turn is done, so it can build on the answer.
- **Notify-only reminders** — fire a desktop/channel notification with no agent turn. Use for simple reminders and timers.
- **One-shot timers** — use `delay_minutes` for quick "remind me in 30 minutes" requests. These delete themselves after firing.

## Writing The Message

1. **Keep it short and positive** — A good brief says, in a few sentences, what to make, for whom, in what voice, and where the results go. Say what to do; a list of what not to do makes results timid and long.
   *"Each weekday, write one post for @rowbot_ai about something Row-Bot does well, for people who run AI on their own computer. Friendly and concrete, in the voice of the account's best posts. Attach a real screenshot when it shows the point. Post it."*

2. **Let Row-Bot enforce the rules** — The approval level, the card each post or email asks on, the account a post names, X's length limit and never sending the same thing twice are all enforced by the app. Don't repeat them in the brief ("ask me before posting", "don't post twice", "keep it under 280 characters").

3. **Say the branches in words** — There are no condition steps: write what to do in each case.
   *"Check my calendar for tomorrow. If there are no events, just say 'Clear schedule tomorrow.' Otherwise list them with times and highlight any conflicts."*

4. **Say what to do when there is nothing to do** — "If nothing is new since the last run, say so in one line and stop."

5. **Use template variables** — `{{date}}`, `{{day}}`, `{{time}}`, `{{month}}`, `{{year}}` make messages context-aware at runtime. *"Summarise news for {{date}}"* produces different results each day.

6. **Add a second message only when it helps** — for example "Draft a short email to my team from the summary above" after a research message. Most workflows need one.

## When to Use One Workflow vs. Several

6. **One workflow** when the work belongs together — research that searches, reads sources and writes a brief is one message.
7. **Separate workflows** when the pieces are independent — a morning weather check and an inbox summary don't need each other. Separate workflows can be turned on and off, scheduled and delivered on their own.

## Handing Work On

- **Send, don't search** — when one workflow's result is another's input (a trends brief for a publisher), the first ends with `conversation_send` to the second workflow's conversation, by name. It arrives as a message from that conversation and starts a turn there; never leave a result for the next one to search for.
- **Share pictures, clips and voice lines through Library › Media** — `media_find` finds them by words, kind or tag, `media_tag` tags them ("x post", "launch"), and `media_bring` copies one into the conversation or workflow that needs it.
- **Real screenshots and clips** — a `sandbox` is a second Row-Bot on made-up data. Open it in Row-Bot's browser, then take a `browser_screenshot`, or `browser_record` a few steps and mark beats, and make the clip with `video`, `recording=rec-…`. Never film or capture the person's own Row-Bot.

## Scheduling

8. **Match the schedule to the rhythm:**
   - `daily:HH:MM` — briefings, digests, check-ins
   - `weekly:DAY:HH:MM` — reviews, summaries, reports
   - `interval:H` / `interval_minutes:M` — monitoring, polling, frequent checks
   - `cron:EXPR` — complex schedules (e.g. `cron:0 9 * * mon-fri` for weekdays only)

## Delivery & Notification

9. **Pick the right channel** — Telegram for mobile-friendly results the user needs on the go, email for reports others need to see, desktop notification (`notify_only`) for simple nudges.
10. **Ask before defaulting** — Don't assume a delivery channel. Ask how the user wants to receive results. Some automations are better read on demand in the app.
11. **Each delivery says what happened first** — "Sent · …" with what went out, or "Finished · nothing was posted or sent", then the reply. The person chooses in **Workflow settings › Send results** whether results go after every run, only when a run needs them, or in a daily digest at 6 PM; suggest the digest for frequent runs.

## Approval Levels

Every workflow has an approval level (`approval_mode`), as a chat does:
- `block` (default) — reads only; actions are refused. Safest for automated runs.
- `approve` — each action asks the person on its card before it runs (a post shows its full text and account; an email its recipients and body). The run waits until they answer.
- `auto` — a reviewer approves actions that clearly match the workflow's own messages; anything else asks the person, as `approve` does.
- Hands-off (`full`) runs every action without asking. Only the person can turn it on, in the workflow's settings; never set or suggest it yourself.

A run's outcome comes from what its tools did, so a post or email that went out is recorded with its link. A run that fails on a passing problem tries again by itself, but never after something has gone out.

## Other Settings

12. **Model override** — Use the `model` parameter to run a workflow on a different model (e.g. a heavier model for complex research, a lighter one for quick checks).
13. **Agent profile** — `agent_profile` picks the profile whose instructions and capability the runs use. Profiles list no tools.

## Maintenance

14. **Check first** — Before creating a new workflow, check `task_list` to avoid duplicates or overlapping schedules.
15. **Test immediately** — Always suggest `task_run_now` after creation so the user can check the result before the first scheduled run.
16. **Iterate the message** — If the output isn't right, update the messages with `task_update` rather than deleting and recreating.

## Monitoring / Polling

When the user wants to **monitor a condition** and be told when it changes ("check X and tell me when Y", "alert me if Z drops below W"), use the **interval + self-disable** pattern:

17. **Use an interval schedule** — `interval_minutes:M` for frequent checks (stock availability, price drops) or `interval:H` for slower checks.

18. **Write a conditional message** — It should:
    - Check the condition (e.g. search the web, read a URL, check a price)
    - If the condition IS met → report the finding and self-disable with `task_update(task_id='{{task_id}}', enabled=false)`
    - If the condition is NOT met → say so briefly
    The runs share one conversation, so each check can compare with the last one ("tell me when there's a new version" needs to know the old version).

19. **Template: polling message pattern** —
    ```
    Search for [condition]. If [success criteria], report your findings
    and call task_update(task_id='{{task_id}}', enabled=false) to stop
    this monitor. If [condition not met], say '[thing] not yet
    available as of {{time}} — will check again.'
    ```

20. **Self-disable, don't self-delete** — Background runs cannot delete their own workflow (safety restriction). Use `task_update(enabled=false)` instead. The user can re-enable, delete, or leave the disabled workflow.
