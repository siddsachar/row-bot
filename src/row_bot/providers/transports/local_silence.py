"""A local model whose reply never comes, in plain words, on every local engine.

Ollama's transport (``ollama_local.py``) and the OpenAI-compatible one, for an endpoint on this computer
(``execution_location: "local"``: llama.cpp, LM Studio, vLLM, SGLang, Run on this computer), end a request
that receives nothing for ``LOCAL_CHAT_IDLE_SECONDS``, and a reply that reached its length limit with
nothing of it shown, with ``LocalModelSilentError``. Neither is asked again: a model that went silent, or
wrote until its limit, does so again, and the person waits twice as long for it.
"""

from __future__ import annotations

# The longest a local chat request may receive nothing. A local server is silent while it loads the model, reads
# the prompt and (Ollama, LM Studio) writes a tool call, which it holds back until the call is complete. 10
# minutes leaves room for all three on slow hardware, while a model stuck in a tool call it never closes no longer
# holds the GPU for half an hour.
LOCAL_CHAT_IDLE_SECONDS = 600.0


class LocalModelSilentError(RuntimeError):
    """The local model's reply never came: its server sent nothing for longer than the request's idle limit, or
    the model wrote until the reply's length limit without any of it showing (a tool call it never closed).

    Its words are the person's: the agent shows them as they are."""

    @classmethod
    def idle(cls, seconds: float | None) -> LocalModelSilentError:
        span = "too long" if not seconds else f"{seconds / 60:g} minutes" if seconds >= 60 else f"{seconds:g} seconds"
        return cls(f"The local model sent nothing for {span}, so Row-Bot stopped waiting for it. "
                   "Try again, or try another model.")

    @classmethod
    def filled_window(cls) -> LocalModelSilentError:
        return cls("The local model filled its whole context window without sending a reply, so Row-Bot stopped it. "
                   "Try again, or try another model.")

    @classmethod
    def reply_limit(cls) -> LocalModelSilentError:
        return cls("The local model wrote until its reply length limit without sending a reply, so Row-Bot "
                   "stopped it. Try again, or try another model.")


def is_local_silence(text: str) -> bool:
    """Whether an error's text is a ``LocalModelSilentError``'s own words."""

    return str(text or "").startswith("The local model ")
