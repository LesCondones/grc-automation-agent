# GRC Automation Agent

A Streamlit chat agent that reads and edits GRC markdown documentation — risk registers, NIST 800-53 / AI RMF control mappings, OWASP LLM Top 10 status, incident logs — directly in a GitHub repository, using natural language.

Ask it a question ("What risks are open or partially mitigated?") and it searches and reports. Give it an instruction ("Mark R-008 as mitigated") and it edits the file and commits, with every commit message auto-tagged with the relevant NIST control mapping.

## How it works

- **`agent.py`** — builds a [LangChain](https://python.langchain.com/) agent (`create_agent`) on top of `ChatAnthropic`, with `AnthropicPromptCachingMiddleware` so the system prompt and tool schemas are cached server-side after the first turn.
- **`prompts.py`** — the system prompt: repo/file map, status-symbol vocabulary, tool-selection rules, and worked examples that steer the model toward targeted `replace_in_file` edits over full-file rewrites.
- **`tools.py`** — GitHub-backed tools (via [PyGithub](https://pygithub.readthedocs.io/)) for listing, reading, searching, and editing files on a target repo/branch. Writes go straight to the configured branch; every commit gets a NIST 800-53 CM-3 / CA-7 / AI RMF MANAGE 2.4 trailer appended automatically.
- **`app.py`** — the Streamlit chat UI. Streams the agent's tool calls as collapsible breadcrumbs so raw tool output stays out of the way of the model's actual reply.

### Tools available to the agent

| Tool | Purpose |
|---|---|
| `list_repo_files(path)` | Recursive ASCII file tree of the target repo |
| `search_repo_content(query, path_prefix)` | Case-insensitive substring search across markdown files |
| `list_github_files(directory)` | One-level directory listing |
| `read_github_file(path)` | Full contents of one file |
| `get_file_section(path, section_header)` | Body of a single markdown section by heading |
| `replace_in_file(path, find, replace, commit_message)` | Single-substring edit + commit (preferred for status flips, single-row updates) |
| `update_github_file(path, content, commit_message)` | Full file create/rewrite + commit |

## Setup

Requires Python 3.13+.

```bash
uv sync          # or: pip install -e .
cp .env.example .env
```

Edit `.env`:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Required. From [console.anthropic.com](https://console.anthropic.com/). |
| `ANTHROPIC_MODEL` | Default `claude-opus-4-7`. |
| `ANTHROPIC_EFFORT` | Default `high`. |
| `ANTHROPIC_MAX_TOKENS` | Default `16384`. |
| `ANTHROPIC_CACHE_TTL` | Prompt cache TTL, default `5m`. |
| `GITHUB_TOKEN` | Fine-grained GitHub PAT with **Contents: read + write** on the target repo only. |
| `GITHUB_REPO` | `owner/name` of the repo the agent reads from and writes to. |
| `GITHUB_BRANCH` | Branch the agent commits to. Default `main`. |

## Run

```bash
streamlit run app.py
```

## Notes

- Writes commit **directly** to the configured branch — there's no PR/review step. Scope the GitHub token to a single repo and treat the target branch accordingly.
- `replace_in_file` requires the `find` string to match exactly once; `update_github_file` refuses to shrink an existing file by more than 50% unless the commit message contains `FULL REWRITE`, as a guard against the model emitting a stub instead of the full file.
- Tool calls are logged to stderr and `/tmp/grc_agent.log` for auditing which tools the model actually invoked.
