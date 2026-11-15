"""One chat's messages for "Save as a skill" (C1) tests: earlier exchanges, a request, its steps, the answer."""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

RESULTS = {
    "ok": "Done.",
    "failed": "Tool error: that didn't work.",
    "denied": "Approval: asked; denied by you — did not run",
    "blocked": "BLOCKED: Run command is unavailable while this thread is in Block approval mode.",
}


class Turn:
    """Builds one chat's messages: earlier exchanges, then a request, its steps and the answer."""

    def __init__(self, chat: str) -> None:
        self.chat = chat
        self.messages: list[Any] = []
        self.count = 0

    def _id(self, kind: str) -> str:
        self.count += 1
        return f"{self.chat}-{kind}{self.count}"

    def said(self, text: str, reply: str = "Done.") -> Turn:
        self.messages += [HumanMessage(id=self._id("h"), content=text), AIMessage(id=self._id("a"), content=reply)]
        return self

    def ask(self, request: str, steps: list[tuple], answer: str = "All done: the file is saved.") -> Turn:
        self.messages.append(HumanMessage(id=self._id("h"), content=request))
        for step in steps:
            name, args, outcome = (*step, "ok")[:3] if len(step) == 2 else step
            call_id = self._id("call")
            self.messages.append(AIMessage(id=self._id("s"), content="",
                                           tool_calls=[{"name": name, "args": args, "id": call_id}]))
            text = RESULTS.get(outcome, outcome)
            self.messages.append(ToolMessage(id=self._id("t"), tool_call_id=call_id, name=name, content=text,
                                             status="error" if outcome == "failed" else "success"))
        self.messages.append(AIMessage(id=self._id("final"), content=answer))
        return self

    @property
    def reply_id(self) -> str:
        return str(self.messages[-1].id)
