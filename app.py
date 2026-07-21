"""Streamlit chat UI for the GRC Automation Agent."""

from __future__ import annotations

import json
import uuid

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import build_agent, runtime_info

st.set_page_config(
    page_title="GRC Automation Agent",
    page_icon="📋",
    layout="wide",
)


# Code-block language hint per tool so st.code renders nicely.
TOOL_RESULT_LANG = {
    "read_github_file": "markdown",
    "get_file_section": "markdown",
    "list_repo_files": "text",
    "list_github_files": "text",
    "search_repo_content": "text",
    "update_github_file": "text",
}


def _init_state() -> None:
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = str(uuid.uuid4())
    if "display_messages" not in st.session_state:
        st.session_state.display_messages = []


def _sidebar() -> None:
    info = runtime_info()
    with st.sidebar:
        st.header("GRC Automation Agent")
        st.markdown(
            f"""
- **Repo:** `{info['repo']}`
- **Branch:** `{info['branch']}`
- **Model:** `{info['model']}` (Anthropic API)
- **Effort:** `{info['effort']}` · **Max tokens:** `{info['max_tokens']}`
- **Prompt cache:** `{info['cache_ttl']}` TTL
- **Session:** `{st.session_state.thread_id[:8]}`
"""
        )
        st.divider()
        st.caption(
            "Commits are written directly to the branch above. "
            "Every commit message is auto-tagged with NIST 800-53 CM-3, "
            "CA-7, and AI RMF MANAGE 2.4."
        )
        if st.button("New session", use_container_width=True):
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.display_messages = []
            st.rerun()

        st.divider()
        st.subheader("Try")
        examples = [
            "What files are in this repository?",
            "What risks are currently open in the README risk register?",
            "Which OWASP LLM Top 10 items are still Medium?",
            "Mark R-008 (RAG/data poisoning) as mitigated in the README risk register.",
            "Update the OWASP LLM01 status to mitigated in both README and llm-top10.md.",
            "Add an incident log entry for today's UFW finding.",
        ]
        for ex in examples:
            if st.button(ex, key=f"ex-{ex}", use_container_width=True):
                st.session_state._queued_input = ex
                st.rerun()


def _extract_text(content) -> str:
    """Return only the visible text from an AIMessage.content payload.

    With extended thinking enabled, content is a list of blocks like
    [{"type": "thinking", ...}, {"type": "text", "text": "..."}].
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content else ""


def _args_preview(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)


def _format_args(args: dict) -> str:
    try:
        return json.dumps(args, indent=2, default=str)
    except Exception:
        return str(args)


def _render_tool_result(container, name: str, args: dict, result: str) -> None:
    """Render a completed tool call as a collapsed breadcrumb.

    The raw tool output and call args are tucked inside an expander so the
    model's reply is the primary thing the user sees. Click the expander
    label to view the raw output for verification.
    """
    label = f"↳ called `{name}({_args_preview(args)})`"
    with container.expander(label, expanded=False):
        st.code(result, language=TOOL_RESULT_LANG.get(name, "text"))
        st.caption("Call args:")
        st.code(_format_args(args), language="json")


def _render_history() -> None:
    for msg in st.session_state.display_messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                for call in msg.get("tool_calls", []):
                    _render_tool_result(
                        st,
                        call["name"],
                        call["args"],
                        call["result"],
                    )
            if msg.get("content"):
                st.markdown(msg["content"])


def _run_turn(user_input: str) -> None:
    st.session_state.display_messages.append(
        {"role": "user", "content": user_input}
    )
    with st.chat_message("user"):
        st.markdown(user_input)

    agent = build_agent()
    config = {"configurable": {"thread_id": st.session_state.thread_id}}

    assistant_record = {"role": "assistant", "content": "", "tool_calls": []}
    pending_calls: dict[str, dict] = {}

    with st.chat_message("assistant"):
        live_container = st.container()
        try:
            for chunk in agent.stream(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
                stream_mode="updates",
            ):
                for node_name, node_state in chunk.items():
                    for m in node_state.get("messages", []):
                        if isinstance(m, AIMessage):
                            for call in getattr(m, "tool_calls", []) or []:
                                args = call.get("args", {}) or {}
                                pending_calls[call["id"]] = {
                                    "name": call["name"],
                                    "args": args,
                                }
                            text = _extract_text(m.content)
                            if text:
                                assistant_record["content"] = text
                        elif isinstance(m, ToolMessage):
                            call_id = getattr(m, "tool_call_id", None)
                            meta = pending_calls.pop(call_id, None)
                            name = meta["name"] if meta else "tool"
                            args = meta["args"] if meta else {}
                            result = _extract_text(m.content) or (
                                m.content if isinstance(m.content, str) else str(m.content)
                            )
                            assistant_record["tool_calls"].append(
                                {
                                    "name": name,
                                    "args": args,
                                    "result": result,
                                }
                            )
                            _render_tool_result(live_container, name, args, result)
            if assistant_record["content"]:
                live_container.markdown(assistant_record["content"])
        except Exception as e:
            error_msg = f"⚠️ Agent error: `{type(e).__name__}: {e}`"
            live_container.error(error_msg)
            assistant_record["content"] = error_msg

    st.session_state.display_messages.append(assistant_record)


def main() -> None:
    _init_state()
    _sidebar()

    st.title("GRC Automation Agent")
    st.caption(
        "Natural-language commands that read and update the GRC markdown "
        "documentation in your GitHub repo."
    )

    _render_history()

    queued = st.session_state.pop("_queued_input", None)
    user_input = st.chat_input("Ask a question or describe a change…")
    if queued and not user_input:
        user_input = queued

    if user_input:
        _run_turn(user_input)


if __name__ == "__main__":
    main()
