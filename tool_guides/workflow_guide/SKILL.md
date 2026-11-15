---
name: workflow_guide
display_name: Workflow Guide
icon: "⚡"
description: How workflows run (scheduled chats), how to write and change them, hand work between them, and check what a run did.
tools:
  - task
tags: []
---
A WORKFLOW IS A SCHEDULED CHAT (task_create / task_update):
- Each run sends the workflow's messages (`prompts`, usually one) as chat turns in the workflow's own
  conversation, so a run sees what earlier runs did there.
- A run has every tool and skill a chat has, found with tool search. Its approval level decides what it may do:
  block (reads only), approve (each action asks the person), auto (a reviewer approves what matches the
  workflow, else the person). Hands-off is the person's choice alone. Give a workflow the least it needs:
  block for one that only reads and reports.
- Write its message as a short brief: what to make, for whom, in what voice, where results go. The app
  enforces approvals and limits, so don't restate them. Say branching in words: "If nothing is new since the
  last run, stop and say so."
- A post, email or other action asks on its own card with the exact account, recipient and full text. One with
  a time (x_post post_at) waits for the person in Home › Review; make a run's posts in one step so they wait
  there together.
- Schedules keep a time zone (task_create timezone, e.g. 'Europe/London'; the computer's by default).

CHANGING ONE: read it first with task_list(task_id=...): its messages, notes, schedule, time zone and
conversation. Show the person the new message before saving it.

NOTES: a run reads its workflow's notes first and keeps them current with task_update(notes=...): what it
checked and when, ids it needs again, what's left. That saves re-checking everything every run.

HANDING WORK ON: conversation_send reaches a workflow by its name or id. reply=false for an update that needs
no answer; when="next_run" leaves it for the workflow's next run instead of starting a turn now. Don't answer
a reply just to acknowledge it.

WAITING: to carry on when something happens (a build, CI, a deploy, a time, another workflow's run), use
wait_for; nothing runs while it waits. A workflow that watches for something rare (a release) checks once or
twice a day and remembers what it saw in its notes, rather than running every hour.

CHECKING A RUN: task_list(include_history=true) gives each run's status, its outcome in words and the
actions it took (with links). "completed" means the turn finished, not that anything was posted or sent:
read the actions. When the person says no on a card the run stops there and its notes say so: don't do
it another way.
