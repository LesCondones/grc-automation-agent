# GRC Automation Agent — Build Plan

## Final spec (locked)
- **LLM:** Ollama `qwen3:8b` (local, tool-calling)
- **Repo:** `LesCondones/helpdesk-ai-grc`
- **Branch:** `main` (direct commits)
- **Auth:** Fine-grained PAT in `.env` (`GITHUB_TOKEN`)
- **Framework:** LangGraph (`create_react_agent`) + `langchain-ollama`
- **UI:** Streamlit chat — natural-language instructions, direct commit on each tool call
- **Memory:** Short-term per session (LangGraph state / Streamlit `session_state`)

## Tool surface (per spec)
| Tool | Signature | Behavior |
|---|---|---|
| `read_github_file` | `(path: str)` | Return file contents as string |
| `list_github_files` | `(directory: str)` | Return list of paths under directory |
| `update_github_file` | `(path, content, commit_message)` | Direct commit (creates file if missing) |
| `get_file_section` | `(path, section_header)` | Return body of a `## Section` from a markdown file |

## Commit-message convention
Every commit message auto-appends a trailer block:

```
<agent's commit_message>

GRC mappings:
- NIST 800-53 CM-3 (Configuration Change Control)
- NIST 800-53 CA-7 (Continuous Monitoring)
- NIST AI RMF MANAGE 2.4 (AI-assisted risk management)

Co-Authored-By: GRC Automation Agent <agent@local>
```

## Repo file conventions taught to the agent
- Risk register: `docs/grc/risk-register.md` (created on first write)
- Incident log: appended to `docs/ai-rmf/manage.md` (existing file)
- Control implementation status: `docs/grc/control-mapping.md` (created on first write)
- OWASP LLM Top 10 status: `docs/owasp/llm-top10.md` (existing file)

## Architecture
```
GRC-Automation-Agent/
├── agent.py        ← LangGraph ReAct agent (ChatOllama + tools)
├── tools.py        ← PyGithub-backed @tool functions
├── prompts.py      ← System prompt with GRC conventions
├── app.py          ← Streamlit chat UI
├── .env            ← GITHUB_TOKEN, GITHUB_REPO, OLLAMA_MODEL  (gitignored)
├── .env.example    ← committed template
└── pyproject.toml
```

## Tasks
- [ ] #1 Add deps (langgraph, langchain-core, langchain-ollama, pygithub, streamlit, python-dotenv)
- [ ] #2 .env.example + .gitignore
- [ ] #3 tools.py — 4 tools per spec (direct commit, NIST trailer appended)
- [ ] #4 prompts.py — teach file conventions + commit-msg style + risk/incident schemas
- [ ] #5 agent.py — LangGraph ReAct, qwen3:8b
- [ ] #6 app.py — Streamlit chat (no approval gate)
- [ ] #7 Smoke test against helpdesk-ai-grc

## Review
_(to be filled after build)_
