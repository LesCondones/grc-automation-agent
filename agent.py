"""LangChain agent wired to Anthropic Claude (Opus 4.7) and GitHub tools.

Uses `langchain.agents.create_agent` (the successor to
`langgraph.prebuilt.create_react_agent`) with
`AnthropicPromptCachingMiddleware` so the long GRC system prompt and the
tool schemas are cached server-side — every turn after the first reads
the cached prefix at ~10% of the base input-token cost.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.middleware.prompt_caching import (
    AnthropicPromptCachingMiddleware,
)
from langgraph.checkpoint.memory import MemorySaver

from prompts import system_prompt
from tools import ALL_TOOLS

load_dotenv()


def _config() -> dict:
    return {
        "model": os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7"),
        "effort": os.getenv("ANTHROPIC_EFFORT", "high"),
        "max_tokens": int(os.getenv("ANTHROPIC_MAX_TOKENS", "16384")),
        "cache_ttl": os.getenv("ANTHROPIC_CACHE_TTL", "5m"),
        "repo": os.getenv("GITHUB_REPO", "LesCondones/helpdesk-ai-grc"),
        "branch": os.getenv("GITHUB_BRANCH", "main"),
    }


def _build_llm(cfg: dict) -> ChatAnthropic:
    """Construct ChatAnthropic with adaptive thinking + effort.

    Falls back to a vanilla constructor if this langchain-anthropic build
    doesn't yet accept the adaptive thinking / effort arguments.
    """
    base_kwargs: dict = {
        "model": cfg["model"],
        "max_tokens": cfg["max_tokens"],
        "api_key": os.getenv("ANTHROPIC_API_KEY"),
    }
    try:
        return ChatAnthropic(
            **base_kwargs,
            temperature=1,
            thinking={"type": "adaptive"},
            model_kwargs={"output_config": {"effort": cfg["effort"]}},
        )
    except TypeError as e:
        print(
            f"[AGENT] adaptive thinking/effort not accepted by this "
            f"langchain-anthropic build ({e}); falling back.",
            file=sys.stderr,
            flush=True,
        )
        return ChatAnthropic(**base_kwargs, temperature=0)


@lru_cache(maxsize=1)
def build_agent():
    """Compile and cache the LangChain agent."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env "
            "(get one at https://console.anthropic.com/)."
        )

    cfg = _config()
    print(
        f"[AGENT] building: model={cfg['model']} effort={cfg['effort']} "
        f"max_tokens={cfg['max_tokens']} cache_ttl={cfg['cache_ttl']} "
        f"repo={cfg['repo']} branch={cfg['branch']}",
        file=sys.stderr,
        flush=True,
    )
    print(
        f"[AGENT] binding {len(ALL_TOOLS)} tools: "
        + ", ".join(t.name for t in ALL_TOOLS),
        file=sys.stderr,
        flush=True,
    )
    llm = _build_llm(cfg)
    prompt = system_prompt(repo=cfg["repo"], branch=cfg["branch"])

    caching = AnthropicPromptCachingMiddleware(
        ttl=cfg["cache_ttl"],
        unsupported_model_behavior="warn",
    )

    agent = create_agent(
        model=llm,
        tools=ALL_TOOLS,
        system_prompt=prompt,
        middleware=[caching],
        checkpointer=MemorySaver(),
    )
    print("[AGENT] ready (prompt caching enabled)", file=sys.stderr, flush=True)
    return agent


def runtime_info() -> dict:
    """Return current config so the UI can display it."""
    return _config()
