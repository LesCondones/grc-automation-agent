"""GitHub-backed tools for the GRC Automation Agent.

Exposes @tool-decorated functions:
    - list_repo_files(path)
    - search_repo_content(query)
    - list_github_files(directory)
    - read_github_file(path)
    - get_file_section(path, section_header)
    - update_github_file(path, content, commit_message)

All writes commit DIRECTLY to GITHUB_BRANCH (default: main). Every commit
message is augmented with a NIST trailer block before being sent to GitHub.

Every tool call is logged to both stderr (visible in the terminal running
Streamlit) and /tmp/grc_agent.log so we can verify which tools the model
is actually invoking.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from functools import lru_cache

from dotenv import load_dotenv
from github import Github, GithubException, UnknownObjectException
from github.Repository import Repository
from langchain_core.tools import tool

load_dotenv()


_READ_CACHE: dict[tuple, tuple[float, str]] = {}
_READ_CACHE_TTL = 60.0


def _cache_get(tool_name: str, *args) -> str | None:
    key = (tool_name, *args)
    entry = _READ_CACHE.get(key)
    if entry is None:
        return None
    ts, result = entry
    if time.monotonic() - ts > _READ_CACHE_TTL:
        _READ_CACHE.pop(key, None)
        return None
    return result


def _cache_set(tool_name: str, *args, result: str) -> None:
    _READ_CACHE[(tool_name, *args)] = (time.monotonic(), result)


def _cache_clear() -> None:
    _READ_CACHE.clear()


_LOG_PATH = "/tmp/grc_agent.log"
logger = logging.getLogger("grc_agent.tools")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(_LOG_PATH)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(fh)
    logger.propagate = False


def _trace(tool_name: str, **kwargs) -> None:
    """Emit a one-line trace of a tool invocation to stderr + log file."""
    parts = []
    for k, v in kwargs.items():
        if isinstance(v, str) and len(v) > 80:
            parts.append(f"{k}=<str len={len(v)}>")
        else:
            parts.append(f"{k}={v!r}")
    msg = f"[TOOL] {tool_name}({', '.join(parts)})"
    print(msg, file=sys.stderr, flush=True)
    logger.info(msg)


def _trace_result(tool_name: str, result: str) -> None:
    preview = result.replace("\n", " ⏎ ")
    if len(preview) > 160:
        preview = preview[:157] + "..."
    msg = f"[TOOL ←] {tool_name}: {preview}"
    print(msg, file=sys.stderr, flush=True)
    logger.info(msg)


COMMIT_TRAILER = (
    "\n\n"
    "GRC mappings:\n"
    "- NIST 800-53 CM-3 (Configuration Change Control)\n"
    "- NIST 800-53 CA-7 (Continuous Monitoring)\n"
    "- NIST AI RMF MANAGE 2.4 (AI-assisted risk management)\n"
    "\n"
    "Co-Authored-By: GRC Automation Agent <agent@local>\n"
)


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return value


@lru_cache(maxsize=1)
def _repo() -> Repository:
    token = _env("GITHUB_TOKEN")
    repo_full = _env("GITHUB_REPO")
    return Github(token).get_repo(repo_full)


def _branch() -> str:
    return os.getenv("GITHUB_BRANCH", "main")


def _with_trailer(message: str) -> str:
    message = (message or "").strip() or "Update via GRC Automation Agent"
    if "GR  C mappings:" in message:
        return message
    return message + COMMIT_TRAILER


@tool
def read_github_file(path: str) -> str:
    """Read the full contents of a file from the GRC GitHub repository.

    Use this whenever you need to inspect a markdown document before editing
    it, or to answer questions about the current state of GRC documentation.

    Args:
        path: Path relative to the repo root, e.g. "docs/ai-rmf/manage.md".

    Returns:
        The decoded UTF-8 contents of the file, or an error message string
        if the file does not exist.
    """
    _trace("read_github_file", path=path)
    cached = _cache_get("read_github_file", path)
    if cached is not None:
        _trace_result("read_github_file", f"CACHE HIT len={len(cached)}")
        return cached
    try:
        content_file = _repo().get_contents(path, ref=_branch())
    except UnknownObjectException:
        out = f"ERROR: file not found at path '{path}' on branch '{_branch()}'."
        _trace_result("read_github_file", out)
        return out
    except GithubException as e:
        out = f"ERROR: GitHub API error reading '{path}': {e.data}"
        _trace_result("read_github_file", out)
        return out
    if isinstance(content_file, list):
        out = f"ERROR: '{path}' is a directory, not a file. Use list_github_files instead."
        _trace_result("read_github_file", out)
        return out
    text = content_file.decoded_content.decode("utf-8")
    _cache_set("read_github_file", path, result=text)
    _trace_result("read_github_file", f"OK len={len(text)}")
    return text


@tool
def list_github_files(directory: str = "") -> str:
    """List the files and subdirectories inside a directory of the GRC repo.

    Use this to discover what GRC artifacts exist (risk registers, control
    mappings, incident logs) before deciding which file to read or update.

    Args:
        directory: Directory path relative to repo root. Pass "" or "/" for
            the repo root. Examples: "docs", "docs/ai-rmf", "docs/grc".

    Returns:
        A newline-separated listing where each entry is "<type>\\t<path>"
        with type being "file" or "dir". Returns an error message string if
        the directory does not exist.
    """
    _trace("list_github_files", directory=directory)
    path = (directory or "").strip().lstrip("/")
    cached = _cache_get("list_github_files", path)
    if cached is not None:
        _trace_result("list_github_files", "CACHE HIT")
        return cached
    try:
        contents = _repo().get_contents(path, ref=_branch())
    except UnknownObjectException:
        out = f"ERROR: directory not found: '{path or '<root>'}' on branch '{_branch()}'."
        _trace_result("list_github_files", out)
        return out
    except GithubException as e:
        out = f"ERROR: GitHub API error listing '{path}': {e.data}"
        _trace_result("list_github_files", out)
        return out
    if not isinstance(contents, list):
        out = f"ERROR: '{path}' is a file, not a directory. Use read_github_file instead."
        _trace_result("list_github_files", out)
        return out
    if not contents:
        out = f"(empty directory: {path or '<root>'})"
        _trace_result("list_github_files", out)
        return out
    lines = [f"{c.type}\t{c.path}" for c in sorted(contents, key=lambda x: (x.type != "dir", x.path))]
    out = "\n".join(lines)
    _cache_set("list_github_files", path, result=out)
    _trace_result("list_github_files", f"OK {len(lines)} entries")
    return out


@tool
def update_github_file(path: str, content: str, commit_message: str) -> str:
    """Commit a new or updated markdown file to the GRC repository.

    This writes DIRECTLY to the configured branch (default: main). The commit
    message is automatically extended with a NIST GRC-mapping trailer
    (CM-3 / CA-7 / AI RMF MANAGE 2.4) so commits are auditable.

    Use this to:
      - Add a new risk to docs/grc/risk-register.md
      - Change a risk's status (open / mitigated / accepted)
      - Append an incident log entry to docs/ai-rmf/manage.md
      - Update control implementation status in docs/grc/control-mapping.md
      - Update OWASP LLM Top 10 status in docs/owasp/llm-top10.md

    Always pass the FULL new file contents in `content`. If you only want to
    change part of a file, first call read_github_file to get the current
    contents, modify them, then pass the result here.

    Args:
        path: Repo-relative path, e.g. "docs/grc/risk-register.md".
        content: Full new UTF-8 contents of the file.
        commit_message: Short, imperative summary of the change. The NIST
            trailer is appended automatically; do not include it yourself.

    Returns:
        A success message including the commit SHA, or an error message string.
    """
    _trace(
        "update_github_file",
        path=path,
        content_len=len(content or ""),
        commit_message=commit_message,
    )
    repo = _repo()
    branch = _branch()
    full_message = _with_trailer(commit_message)

    try:
        existing = repo.get_contents(path, ref=branch)
    except UnknownObjectException:
        existing = None
    except GithubException as e:
        out = f"ERROR: GitHub API error checking '{path}': {e.data}"
        _trace_result("update_github_file", out)
        return out

    try:
        if existing is None:
            result = repo.create_file(
                path=path,
                message=full_message,
                content=content,
                branch=branch,
            )
            sha = result["commit"].sha
            _cache_clear()
            out = f"OK: created '{path}' on '{branch}' (commit {sha[:7]})."
            _trace_result("update_github_file", out)
            return out
        else:
            if isinstance(existing, list):
                out = f"ERROR: '{path}' is a directory, cannot update."
                _trace_result("update_github_file", out)
                return out
            existing_text = existing.decoded_content.decode("utf-8")
            # Safety guard: refuse if this would shrink the file dramatically.
            # Small local models often emit a docstring fragment as `content`,
            # which would clobber the entire file with a stub.
            existing_len = len(existing_text)
            new_len = len(content or "")
            if existing_len > 1000 and new_len < existing_len * 0.5:
                shrink_pct = round((1 - new_len / existing_len) * 100)
                out = (
                    f"ERROR: refusing to commit — this would shrink '{path}' "
                    f"by {shrink_pct}% ({existing_len} → {new_len} bytes). "
                    f"This pattern usually means the model emitted a stub "
                    f"instead of the full file. For targeted edits, use "
                    f"`replace_in_file(path, find, replace, commit_message)` "
                    f"instead. If you genuinely intend a full rewrite, include "
                    f"the phrase 'FULL REWRITE' in your commit_message."
                )
                if "FULL REWRITE" not in (commit_message or "").upper():
                    _trace_result("update_github_file", out)
                    return out
            if existing_text == content:
                out = f"NOOP: '{path}' already matches the provided content. No commit made."
                _trace_result("update_github_file", out)
                return out
            result = repo.update_file(
                path=path,
                message=full_message,
                content=content,
                sha=existing.sha,
                branch=branch,
            )
            sha = result["commit"].sha
            _cache_clear()
            out = f"OK: updated '{path}' on '{branch}' (commit {sha[:7]})."
            _trace_result("update_github_file", out)
            return out
    except GithubException as e:
        out = f"ERROR: commit failed for '{path}': {e.data}"
        _trace_result("update_github_file", out)
        return out


@tool
def get_file_section(path: str, section_header: str) -> str:
    """Return the body of a single markdown section by its heading.

    Useful when a file is large and you only need one risk entry, one control,
    or one section like "## LLM01: Prompt Injection". The match is on heading
    text only (the leading "#" characters are stripped before comparison) and
    is case-insensitive.

    The section body starts on the line AFTER the matching heading and ends
    at the next heading of equal or higher level (or end of file).

    Args:
        path: Repo-relative file path.
        section_header: The heading text to match. You may pass it with or
            without leading "#"s, e.g. "## LLM01: Prompt Injection",
            "LLM01: Prompt Injection", or "MAP-007".

    Returns:
        The section body (heading line included), or an error message if the
        file or section is not found.
    """
    _trace("get_file_section", path=path, section_header=section_header)
    content = read_github_file.invoke({"path": path})
    if content.startswith("ERROR"):
        _trace_result("get_file_section", content)
        return content

    target = section_header.lstrip("#").strip().lower()
    if not target:
        out = "ERROR: section_header is empty."
        _trace_result("get_file_section", out)
        return out

    lines = content.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

    start_idx: int | None = None
    start_level: int = 0
    for i, line in enumerate(lines):
        m = heading_re.match(line)
        if not m:
            continue
        text = m.group(2).strip().lower()
        if text == target or text.startswith(target) or target in text:
            start_idx = i
            start_level = len(m.group(1))
            break

    if start_idx is None:
        out = f"ERROR: section '{section_header}' not found in '{path}'."
        _trace_result("get_file_section", out)
        return out

    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        m = heading_re.match(lines[j])
        if m and len(m.group(1)) <= start_level:
            end_idx = j
            break

    out = "\n".join(lines[start_idx:end_idx]).rstrip() + "\n"
    _trace_result("get_file_section", f"OK lines={end_idx - start_idx}")
    return out


def _render_tree(paths: list[str], root_label: str) -> str:
    """Render a sorted list of paths as an ASCII tree string."""
    Node = dict  # nested {name: Node | None} where None marks a file
    root: dict = {}
    for p in paths:
        parts = p.split("/")
        cursor = root
        for part in parts[:-1]:
            if part not in cursor or cursor[part] is None:
                cursor[part] = {}
            cursor = cursor[part]
        cursor[parts[-1]] = None

    lines: list[str] = [f"{root_label}/"]

    def walk(node: dict, prefix: str) -> None:
        items = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0].lower()))
        for i, (name, child) in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            suffix = "/" if isinstance(child, dict) else ""
            lines.append(f"{prefix}{connector}{name}{suffix}")
            if isinstance(child, dict):
                walk(child, prefix + ("    " if is_last else "│   "))

    walk(root, "")
    return "\n".join(lines)


@tool
def list_repo_files(path: str = "") -> str:
    """Recursively list every file in the GRC repo (or a subdirectory) as an
    ASCII file tree.

    Call this FIRST in every new conversation to map the repository before
    deciding which files to read or update. The agent SHOULD reply with the
    raw tree output verbatim before adding any analysis.

    Args:
        path: Optional repo-relative subdirectory to restrict the listing,
            e.g. "docs/owasp". Pass "" for the entire repo.

    Returns:
        An ASCII file tree such as:

            helpdesk-ai-grc/
            ├── README.md
            ├── configs/
            │   └── Caddyfile
            └── docs/
                └── owasp/
                    └── llm-top10.md

        Or an error message string.
    """
    _trace("list_repo_files", path=path)
    cached = _cache_get("list_repo_files", path or "")
    if cached is not None:
        _trace_result("list_repo_files", "CACHE HIT")
        return cached
    repo = _repo()
    branch = _branch()
    try:
        head = repo.get_branch(branch).commit.sha
        tree = repo.get_git_tree(head, recursive=True)
    except GithubException as e:
        out = f"ERROR: GitHub API error reading tree: {e.data}"
        _trace_result("list_repo_files", out)
        return out

    prefix = (path or "").strip().strip("/")
    paths: list[str] = []
    for entry in tree.tree:
        if entry.type != "blob":
            continue
        if prefix and not (entry.path == prefix or entry.path.startswith(prefix + "/")):
            continue
        if prefix:
            rel = entry.path[len(prefix) + 1:] if entry.path != prefix else entry.path
            paths.append(rel)
        else:
            paths.append(entry.path)

    if not paths:
        out = f"(no files found under '{prefix or '<root>'}' on branch '{branch}')"
        _trace_result("list_repo_files", out)
        return out

    root_label = prefix if prefix else (repo.name or "repo")
    out = _render_tree(sorted(paths), root_label)
    _cache_set("list_repo_files", path or "", result=out)
    _trace_result("list_repo_files", f"OK files={len(paths)}")
    return out


@tool
def search_repo_content(query: str, path_prefix: str = "") -> str:
    """Search markdown files in the repo for a keyword or phrase.

    Use this when the user mentions something (a risk ID, a control number,
    a topic) and you don't yet know which file documents it. The search is
    case-insensitive substring matching over `.md` files only.

    Args:
        query: Text to look for. Required and non-empty.
        path_prefix: Optional repo-relative prefix to limit the search,
            e.g. "docs/owasp".

    Returns:
        Matching files with line numbers and the matching line, formatted as:

            path:line: <matching line>

        Up to 50 matches are returned. Returns "(no matches)" if nothing
        is found, or an error message string.
    """
    _trace("search_repo_content", query=query, path_prefix=path_prefix)
    cached = _cache_get("search_repo_content", query, path_prefix or "")
    if cached is not None:
        _trace_result("search_repo_content", "CACHE HIT")
        return cached
    q = (query or "").strip()
    if not q:
        out = "ERROR: query is empty."
        _trace_result("search_repo_content", out)
        return out
    q_lower = q.lower()

    repo = _repo()
    branch = _branch()
    try:
        head = repo.get_branch(branch).commit.sha
        tree = repo.get_git_tree(head, recursive=True)
    except GithubException as e:
        out = f"ERROR: GitHub API error reading tree: {e.data}"
        _trace_result("search_repo_content", out)
        return out

    prefix = (path_prefix or "").strip().strip("/")
    md_paths = [
        e.path
        for e in tree.tree
        if e.type == "blob"
        and e.path.lower().endswith(".md")
        and (not prefix or e.path == prefix or e.path.startswith(prefix + "/"))
    ]

    if not md_paths:
        out = f"(no markdown files found under '{prefix or '<root>'}')"
        _trace_result("search_repo_content", out)
        return out

    matches: list[str] = []
    files_scanned = 0
    for path in sorted(md_paths):
        if len(matches) >= 50:
            break
        files_scanned += 1
        try:
            content_file = repo.get_contents(path, ref=branch)
            if isinstance(content_file, list):
                continue
            text = content_file.decoded_content.decode("utf-8", errors="replace")
        except GithubException:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if q_lower in line.lower():
                snippet = line.rstrip()
                if len(snippet) > 240:
                    snippet = snippet[:237] + "..."
                matches.append(f"{path}:{lineno}: {snippet}")
                if len(matches) >= 50:
                    break

    if not matches:
        out = f"(no matches for {q!r} in {files_scanned} markdown file(s))"
        _trace_result("search_repo_content", out)
        return out
    header = f"Found {len(matches)} match(es) for {q!r} across {files_scanned} markdown file(s):\n"
    out = header + "\n".join(matches)
    _cache_set("search_repo_content", query, path_prefix or "", result=out)
    _trace_result("search_repo_content", f"OK matches={len(matches)}")
    return out


@tool
def replace_in_file(
    path: str, find: str, replace: str, commit_message: str
) -> str:
    """Edit a single line or fragment in an existing file and commit it.

    Use this for **targeted edits** — flipping a status cell, changing a
    line, updating one row in a table. The tool reads the file server-side,
    verifies the `find` string appears EXACTLY ONCE, substitutes `replace`,
    and commits the result. You do NOT need to pass the rest of the file.

    Prefer `replace_in_file` over `update_github_file` whenever you can
    describe the change as a single string substitution. Reserve
    `update_github_file` for creating new files or rewriting an entire file.

    Args:
        path: Repo-relative path of an existing file.
        find: The EXACT text fragment to replace. Must match a unique
            substring of the file byte-for-byte (including spaces and
            emoji). If `find` is missing or appears more than once, the
            commit is rejected.
        replace: The replacement text. Pass exactly what should appear in
            the file after the edit.
        commit_message: Short, imperative commit message. The system
            auto-appends the NIST GRC-mapping trailer.

    Returns:
        Success message with commit SHA, or a descriptive error string
        (e.g. "ERROR: 'find' not present in file" / "ERROR: 'find' appears
        3 times — make it more specific").
    """
    _trace(
        "replace_in_file",
        path=path,
        find_len=len(find or ""),
        replace_len=len(replace or ""),
        commit_message=commit_message,
    )
    if not find:
        out = "ERROR: 'find' is empty."
        _trace_result("replace_in_file", out)
        return out

    repo = _repo()
    branch = _branch()
    try:
        existing = repo.get_contents(path, ref=branch)
    except UnknownObjectException:
        out = f"ERROR: file not found at path '{path}' on branch '{branch}'."
        _trace_result("replace_in_file", out)
        return out
    except GithubException as e:
        out = f"ERROR: GitHub API error reading '{path}': {e.data}"
        _trace_result("replace_in_file", out)
        return out
    if isinstance(existing, list):
        out = f"ERROR: '{path}' is a directory, cannot edit."
        _trace_result("replace_in_file", out)
        return out

    current = existing.decoded_content.decode("utf-8")
    occurrences = current.count(find)
    if occurrences == 0:
        out = (
            f"ERROR: 'find' string is not present in '{path}'. "
            f"Check that the snippet matches byte-for-byte (spaces, "
            f"emoji, table pipes). Use read_github_file or "
            f"get_file_section to copy the exact text first."
        )
        _trace_result("replace_in_file", out)
        return out
    if occurrences > 1:
        out = (
            f"ERROR: 'find' string appears {occurrences} times in '{path}'. "
            f"Make it more specific (include surrounding columns/words) so "
            f"there is exactly one match."
        )
        _trace_result("replace_in_file", out)
        return out

    new_content = current.replace(find, replace, 1)
    if new_content == current:
        out = f"NOOP: 'find' and 'replace' produce the same text in '{path}'."
        _trace_result("replace_in_file", out)
        return out

    full_message = _with_trailer(commit_message)
    try:
        result = repo.update_file(
            path=path,
            message=full_message,
            content=new_content,
            sha=existing.sha,
            branch=branch,
        )
    except GithubException as e:
        out = f"ERROR: commit failed for '{path}': {e.data}"
        _trace_result("replace_in_file", out)
        return out

    sha = result["commit"].sha
    _cache_clear()
    out = (
        f"OK: edited '{path}' on '{branch}' (commit {sha[:7]}). "
        f"Replaced 1 occurrence."
    )
    _trace_result("replace_in_file", out)
    return out


ALL_TOOLS = [
    list_repo_files,
    search_repo_content,
    list_github_files,
    read_github_file,
    get_file_section,
    replace_in_file,
    update_github_file,
]
