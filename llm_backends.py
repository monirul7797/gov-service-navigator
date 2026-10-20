"""
llm_backends.py — one thin interface, swappable LLM providers.

The rest of the pipeline never imports an SDK directly. It asks for a backend via
get_backend() and calls backend.complete(system, user). Switching providers — Gemini
(free tier) today, Claude tomorrow, a local model later — is a one-line/config change
here, with no edits anywhere else.

Pick the provider with the LLM_PROVIDER env var (default "gemini"), or pass provider=...
to get_backend(). Each backend reads its own API key from the environment:
    Gemini : GEMINI_API_KEY  (or GOOGLE_API_KEY)      pip install google-genai
    Claude : ANTHROPIC_API_KEY                        pip install anthropic
    openai : LLM_BASE_URL + LLM_MODEL + LLM_API_KEY   pip install openai
             ^ ANY OpenAI-compatible endpoint. One backend for z.ai (GLM), Moonshot
               (Kimi), DeepSeek, Groq, OpenRouter, Together, or a local server
               (Ollama/vLLM). Point LLM_BASE_URL at the provider's base URL, set
               LLM_MODEL to a model id they offer, LLM_API_KEY to your key.
               e.g. z.ai free tier: LLM_MODEL=glm-4.5-flash (get the base URL from
               z.ai's API docs after signing up).

SDK imports are lazy (inside each backend) so you only need the SDK for the provider
you actually use.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod


class LLMBackend(ABC):
    """Minimal contract: turn (system prompt, user text) into a text reply."""

    name: str = "base"

    @abstractmethod
    def complete(self, system: str, user: str,
                 max_tokens: int = 256, temperature: float = 0.0) -> str:
        ...


class GeminiBackend(LLMBackend):
    name = "gemini"

    # Free-tier covers Flash / Flash-Lite models. Flash-Lite is the cheapest and has
    # the most generous free quota — a good default for the small transliteration job.
    # Confirm the current free-tier model id in Google AI Studio if this ever 404s.
    def __init__(self, model: str = "gemini-2.5-flash-lite", api_key: str | None = None):
        from google import genai                      # lazy import
        from google.genai import types
        self._types = types
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) for the Gemini backend.")
        self.client = genai.Client(api_key=key)
        self.model = model

    def complete(self, system, user, max_tokens=256, temperature=0.0):
        resp = self.client.models.generate_content(
            model=self.model,
            contents=user,
            config=self._types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return (resp.text or "").strip()


class ClaudeBackend(LLMBackend):
    name = "claude"

    # Haiku is the cheapest Claude tier and more than enough for transliteration.
    def __init__(self, model: str = "claude-haiku-4-5-20251001", api_key: str | None = None):
        import anthropic                               # lazy import
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("Set ANTHROPIC_API_KEY for the Claude backend.")
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model

    def complete(self, system, user, max_tokens=256, temperature=0.0):
        msg = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, temperature=temperature,
            system=system, messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


class OpenAICompatibleBackend(LLMBackend):
    name = "openai"

    # Works with ANY OpenAI-compatible endpoint — z.ai/GLM, Moonshot/Kimi, DeepSeek,
    # Groq, OpenRouter, Together, or a local server (Ollama/vLLM). Everything is set by
    # env so switching providers never touches code:
    #   LLM_BASE_URL  the provider's base URL (from their API docs), e.g.
    #                 z.ai:      https://api.z.ai/api/paas/v4
    #                 Moonshot:  https://api.moonshot.ai/v1
    #                 Groq:      https://api.groq.com/openai/v1
    #                 Ollama:    http://localhost:11434/v1   (local, no key needed)
    #   LLM_MODEL     a model id that provider offers, e.g. glm-4.5-flash
    #   LLM_API_KEY   your key for that provider ("ollama" or anything for local)
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None):
        from openai import OpenAI                     # lazy import; pip install openai
        base_url = base_url or os.environ.get("LLM_BASE_URL")
        self.model = model or os.environ.get("LLM_MODEL")
        key = api_key or os.environ.get("LLM_API_KEY")
        missing = [n for n, v in (("LLM_BASE_URL", base_url), ("LLM_MODEL", self.model),
                                  ("LLM_API_KEY", key)) if not v]
        if missing:
            raise RuntimeError("OpenAI-compatible backend needs these env vars: "
                               + ", ".join(missing))
        # Bounded wait: the SDK default is 10 minutes, long enough to hang a user's turn.
        # LLM_TIMEOUT_S (seconds, default 60) lets callers fail fast and fall back instead.
        self.client = OpenAI(base_url=base_url, api_key=key,
                             timeout=float(os.environ.get("LLM_TIMEOUT_S", "60")))
        # Opt-in latency cut for reasoning models (z.ai/GLM): set LLM_DISABLE_THINKING=1
        # to turn off the thinking phase for simple tasks like transliteration. Sent only
        # when the flag is set, so it never interferes with providers that don't use it.
        # (Note: z.ai's hosted API has been reported to sometimes ignore this — if the
        # delay doesn't drop, the reasoning is baked in and a faster provider is the fix.)
        self.extra_body: dict = {}
        if os.environ.get("LLM_DISABLE_THINKING", "").lower() in ("1", "true", "yes"):
            self.extra_body = {"thinking": {"type": "disabled"}}

    def complete(self, system, user, max_tokens=256, temperature=0.0):
        kwargs = {"extra_body": self.extra_body} if self.extra_body else {}
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=temperature,
            **kwargs,
        )
        return (resp.choices[0].message.content or "").strip()


class LocalBackend(LLMBackend):
    name = "local"

    # Placeholder for the future cost-optimisation path. Most local servers (llama.cpp,
    # Ollama, vLLM, LM Studio) expose an OpenAI-compatible endpoint; wire it here when
    # you have the hardware. Kept as a stub so the interface is ready and nothing else
    # in the pipeline changes when you switch to it.
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "LocalBackend not wired yet — point it at your local OpenAI-compatible "
            "endpoint (Ollama/llama.cpp/vLLM) when you move normalization off the API."
        )

    def complete(self, system, user, max_tokens=256, temperature=0.0):
        raise NotImplementedError


_BACKENDS = {
    "gemini": GeminiBackend,
    "claude": ClaudeBackend,
    "openai": OpenAICompatibleBackend,       # generic — z.ai, Kimi, Groq, DeepSeek, local...
    "compatible": OpenAICompatibleBackend,   # alias for the same thing
    "local": LocalBackend,
}


def get_backend(provider: str | None = None, **kwargs) -> LLMBackend:
    """Factory. provider defaults to $LLM_PROVIDER, else 'gemini'. kwargs pass through
    (e.g. model='...', api_key='...')."""
    provider = (provider or os.environ.get("LLM_PROVIDER", "gemini")).lower()
    if provider not in _BACKENDS:
        raise ValueError(f"Unknown provider '{provider}'. Options: {sorted(_BACKENDS)}")
    return _BACKENDS[provider](**kwargs)
