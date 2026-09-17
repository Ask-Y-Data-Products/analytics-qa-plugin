"""Turn finished QA work into an anonymised know-how article for the plugin community.

The digest stays on this machine: it reads the analyst's own session transcripts,
prompts and cases, which contain client material. Only the article the user
explicitly approved, after redaction, is published to the knowledge-base
repository. Nothing here decides that an article is safe to share; the redaction
report is a checklist for the agent and the user, not a guarantee.
"""
import argparse
from collections import Counter
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
TOPICS_FILE = HERE.parent / "retrospective/topics.json"
# Sections of a session digest, most useful first; the char budget is spent in this order.
SECTION_ORDER = ["user_prompts", "skills", "errors", "assistant_texts", "bash_commands"]
DIGEST_WARNING = ("Local evidence only. This digest quotes the analyst's prompts, commands and case "
                  "records and can contain client material; it must never be published. Only a "
                  "redacted, user-approved article leaves this machine.")

# --- redaction ---------------------------------------------------------------
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
GUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
URL = re.compile(r"\bhttps?://[^\s)\]}>\"'`]+")
WINDOWS_USER_PATH = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'`<>|]+(?:[\\/]+[^\s\"'`<>|]*)*")
POSIX_USER_PATH = re.compile(r"/(?:home|Users)/[^/\s\"'`<>|]+(?:/[^\s\"'`<>|]*)*")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
PHONE = re.compile(r"(?<![\w.\-])(?:\+\d{1,3}[ .\-]?)?(?:\(\d{3}\)|\d{3})[ .\-]\d{3}[ .\-]\d{4}(?![\d\-])")
CAPITALISED = re.compile(r"\b[A-Z][A-Za-z0-9]+(?:[ \-][A-Z][A-Za-z0-9]+)+\b")
CURRENCY = re.compile(r"[$€£¥]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|CAD|AUD|ILS|CHF)\b")
BIG_NUMBER = re.compile(r"\b\d{1,3}(?:,\d{3})+\b|\b\d{5,}\b")
PORT_PREFIX = re.compile(r"port\s*$", re.I)
ALLOWED_URL_HOSTS = {"docs.microsoft.com", "learn.microsoft.com"}

# Capitalised words that are ordinary BI / process vocabulary, not a client identity.
COMMON_CAPITALISED = {
    "power", "bi", "desktop", "service", "google", "ads", "meta", "ga4", "bigquery", "dbt", "dax",
    "analytics", "qa", "sql", "duckdb", "clickhouse", "postgres", "snowflake", "excel", "sharepoint",
    "crm", "salesforce", "hubspot", "data", "model", "measure", "table", "column", "report", "page",
    "visual", "card", "slicer", "chart", "filter", "context", "summary", "issues", "found", "open",
    "questions", "detection", "methods", "design", "rules", "date", "time", "intelligence", "zone",
    "campaign", "conversion", "rate", "attribution", "window", "lead", "leads", "spend", "cost",
    "claude", "code", "python", "windows", "linux", "json", "csv", "parquet", "api", "mcp", "git",
    "github", "readme", "skill", "plugin", "knowledge", "base", "article", "review", "sign", "off",
    "the", "this", "that", "these", "those", "a", "an", "and", "or", "but", "if", "when", "while",
    "for", "in", "on", "at", "of", "to", "by", "with", "from", "into", "over", "under", "after",
    "before", "it", "we", "you", "they", "do", "does", "use", "used", "using", "add", "keep", "each",
    "every", "not", "no", "yes", "one", "two", "three", "first", "last", "next", "same", "other",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "utc", "iso", "id", "ids", "url", "uri", "http", "https"}

STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had", "her", "was", "one",
    "our", "out", "day", "get", "has", "him", "his", "how", "man", "new", "now", "old", "see", "two",
    "way", "who", "boy", "did", "its", "let", "put", "say", "she", "too", "use", "with", "that",
    "this", "from", "have", "were", "been", "they", "them", "then", "than", "when", "what", "will",
    "your", "each", "into", "more", "most", "some", "such", "only", "over", "very", "also", "just",
    "like", "make", "made", "does", "done", "here", "there", "these", "those", "which", "while",
    "would", "could", "should", "about", "after", "before", "because", "every", "other", "their"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def load_config(config, project):
    """kb.json, given directly or relative to the project."""
    candidates = [Path(config), Path(project) / config]
    for candidate in candidates:
        if candidate.is_file():
            return read(candidate)
    raise ValueError(f"Knowledge-base config not found: tried {', '.join(str(c) for c in candidates)}. "
                     "Ask the user for the repository URL and the terms to anonymise, then write kb.json.")


def sanitize_project_path(path):
    """Claude Code's per-project session folder name: every non-alphanumeric becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


# --- digest ------------------------------------------------------------------
def transcript_files(project, since_days, sessions_dir=None):
    """Recent transcripts for this project: the CLI's session store plus headless runs."""
    project = Path(project).resolve()
    folder = Path(sessions_dir) if sessions_dir else Path.home() / ".claude/projects" / sanitize_project_path(project)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)
    found = []
    if folder.is_dir():
        found += [(p, "sessions") for p in sorted(folder.glob("*.jsonl"))]
    found += [(p, "evaluations") for p in sorted(project.glob("evaluations/*/transcript.jsonl"))]
    recent = []
    for path, source in found:
        try:
            modified = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        except OSError:
            continue
        if modified >= cutoff:
            recent.append({"path": path, "source": source, "modified_at": modified.isoformat()})
    return recent


def block_text(content):
    """Text of a message body in either shape, ignoring tool results and thinking."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content
             if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    return "\n".join(p for p in parts if p.strip()).strip()


def first_line(text):
    return text.strip().splitlines()[0][:300] if text.strip() else ""


def summarise_session(path, max_chars=40000):
    """What one transcript says the analyst asked for and what the agent actually ran.

    Tolerant by design: a malformed line is counted, never fatal. Both the
    interactive (sessionId, per-event timestamp) and the headless stream-json
    (session_id) shapes are read.
    """
    path = Path(path)
    record = {"path": str(path), "session_id": None, "started_at": None, "ended_at": None,
              "events": 0, "event_types": {}, "malformed_lines": 0, "truncated": False,
              "user_prompts": [], "assistant_texts": [], "bash_commands": [], "errors": [], "skills": []}
    types, assistant = Counter(), []
    for line in path.open(encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            record["malformed_lines"] += 1
            continue
        if not isinstance(event, dict):
            record["malformed_lines"] += 1
            continue
        record["events"] += 1
        types[str(event.get("type"))] += 1
        record["session_id"] = record["session_id"] or event.get("sessionId") or event.get("session_id")
        stamp = event.get("timestamp")
        if isinstance(stamp, str) and stamp:
            record["started_at"] = min(record["started_at"] or stamp, stamp)
            record["ended_at"] = max(record["ended_at"] or stamp, stamp)
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        if event.get("type") == "user":
            text = block_text(content)
            if text:
                record["user_prompts"].append(text[:2000])
                if text.startswith("/analytics-qa:"):
                    record["skills"].append(first_line(text))
        elif event.get("type") == "assistant":
            text = block_text(content)
            if text:
                assistant.append(text)
                if text.lstrip().startswith("/analytics-qa:"):
                    record["skills"].append(first_line(text))
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                if block.get("name") == "Bash" and arguments.get("command"):
                    record["bash_commands"].append(str(arguments["command"])[:200])
                elif block.get("name") == "Skill":
                    record["skills"].append(" ".join(str(arguments.get(k, "")) for k in ("skill", "args")).strip())
            elif block.get("type") == "tool_result" and block.get("is_error"):
                body = block.get("content")
                record["errors"].append((body if isinstance(body, str) else json.dumps(body, default=str))[:300])
    record["assistant_texts"] = [t[:4000] if i >= len(assistant) - 3 else first_line(t)
                                 for i, t in enumerate(assistant)]
    record["event_types"] = dict(sorted(types.items()))
    return bound_sections(record, max_chars)


def bound_sections(record, max_chars):
    """Spend a fixed character budget across a session's sections, most useful first."""
    remaining = max_chars
    for name in SECTION_ORDER:
        items, kept = record.get(name, []), []
        for item in items:
            size = len(item if isinstance(item, str) else json.dumps(item, default=str))
            if size > remaining:
                break
            remaining -= size
            kept.append(item)
        if len(kept) != len(items):
            record["truncated"] = True
        record[name] = kept
    record["chars"] = max_chars - remaining
    return record


def read_text_file(path, max_chars):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return {"path": str(path), "error": str(exc)}
    return {"path": str(path), "chars": len(text), "text": text[:max_chars], "truncated": len(text) > max_chars}


def collect_inputs(project, max_chars=40000):
    """The analyst's own QA inputs: definitions, prompts and the project's configuration keys."""
    project = Path(project).resolve()
    inputs = {"definitions": [], "prompts": [], "pilot": {}}
    pilot = project / "pilot.json"
    config = {}
    if pilot.is_file():
        try:
            config = read(pilot)
        except ValueError as exc:
            inputs["pilot"] = {"error": f"unreadable pilot.json: {exc}"}
    if config:
        paths = {k: v for k, v in config.items() if isinstance(v, str)
                 and ("/" in v or "\\" in v or (project / v).exists())}
        inputs["pilot"] = {"path": str(pilot), "keys": sorted(config), "paths": paths}
    for candidate in [project / "context.md", project / config.get("definition_file", "__none__")]:
        if candidate.is_file() and all(candidate.resolve() != Path(d["path"]).resolve() for d in inputs["definitions"]):
            inputs["definitions"].append(read_text_file(candidate, max_chars))
    inputs["prompts"] = [read_text_file(p, max_chars) for p in sorted((project / "prompts").glob("*.md"))]
    return inputs


def collect_cases(project):
    """Every case's intent, claims, findings, limitations and components, trimmed for reading."""
    project = Path(project).resolve()
    cases = []
    for path in sorted(project.glob("cases/*/case.json")):
        try:
            case = read(path)
        except (OSError, ValueError) as exc:
            cases.append({"path": str(path), "error": str(exc)})
            continue
        cases.append({
            "path": str(path), "id": case.get("id"), "intent": case.get("intent"), "summary": case.get("summary"),
            "claims": [{"id": c.get("id"), "status": c.get("status"), "layer": c.get("layer"),
                        "detector": c.get("detector"), "expected": str(c.get("expected", ""))[:300],
                        "observed": str(c.get("observed", ""))[:300]} for c in case.get("claims", [])],
            "findings": [{"title": f.get("title"), "severity": f.get("severity"),
                          "explanation": str(f.get("explanation", ""))[:300]} for f in case.get("findings", [])],
            "limitations": case.get("limitations", []),
            "components": [{"id": c.get("id"), "name": c.get("name"), "page": c.get("page"),
                            "scenarios": len(c.get("scenarios", []))} for c in case.get("components", [])]})
    return cases


def collect_evaluation_errors(project):
    errors = []
    for path in sorted(Path(project).resolve().glob("evaluations/*/evaluation.json")):
        try:
            evaluation = read(path)
        except (OSError, ValueError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        if evaluation.get("errors"):
            errors.append({"path": str(path), "errors": evaluation["errors"]})
    return errors


def digest(project, since_days=30, sessions_dir=None, max_chars_per_session=40000):
    project = Path(project).resolve()
    sessions = []
    for found in transcript_files(project, since_days, sessions_dir):
        session = summarise_session(found["path"], max_chars_per_session)
        session.update({"source": found["source"], "modified_at": found["modified_at"]})
        sessions.append(session)
    cases = collect_cases(project)
    result = {"generated_at": now(), "project": str(project), "since_days": since_days,
              "sessions": sessions, "inputs": collect_inputs(project, max_chars_per_session),
              "cases": cases, "evaluation_errors": collect_evaluation_errors(project),
              "warning": DIGEST_WARNING}
    result["stats"] = {"sessions": len(sessions), "malformed_lines": sum(s["malformed_lines"] for s in sessions),
                       "truncated_sessions": sum(1 for s in sessions if s["truncated"]),
                       "bash_commands": sum(len(s["bash_commands"]) for s in sessions),
                       "tool_errors": sum(len(s["errors"]) for s in sessions),
                       "cases": len(cases), "findings": sum(len(c.get("findings", [])) for c in cases)}
    return result


# --- redaction ---------------------------------------------------------------
def replacement_pattern(term):
    """Whole-word where the term's edges are word characters, literal otherwise."""
    pattern = re.escape(term)
    if term[:1].isalnum():
        pattern = r"\b" + pattern
    if term[-1:].isalnum():
        pattern = pattern + r"\b"
    return re.compile(pattern, re.I)


def allowed_url(url, repository):
    host = (urlparse(url).netloc or "").lower().split("@")[-1].split(":")[0]
    if host in ALLOWED_URL_HOSTS:
        return True
    if host != "github.com" or not repository:
        return False
    kb = urlparse(str(repository))
    wanted = "/".join(kb.path.strip("/").split("/")[:2]).removesuffix(".git").lower()
    return bool(wanted) and urlparse(url).path.strip("/").lower().startswith(wanted)


def redact_text(text, config):
    """Apply the configured replacements, then scrub identifiers, then scan for leftovers.

    Returns the redacted text and a report the agent must read: what was replaced,
    what was scrubbed automatically, and what still looks identifying. Nothing here
    proves the text is safe; residual items are for a human to resolve.
    """
    rules = config.get("redaction", config) or {}
    replace = rules.get("replace") or {}
    allow = {str(a).lower() for a in (rules.get("allow") or [])}
    repository = config.get("repository")
    replacements, scrubbed = {}, Counter()
    for term in sorted(replace, key=lambda t: (-len(t), t)):
        text, count = replacement_pattern(term).subn(str(replace[term]), text)
        replacements[term] = count
    for kind, pattern, token in [("email", EMAIL, "[email]"), ("id", GUID, "[id]")]:
        text, count = pattern.subn(token, text)
        scrubbed[kind] += count

    def url_token(match):
        if allowed_url(match.group(0), repository):
            return match.group(0)
        scrubbed["url"] += 1
        return "[url]"

    text = URL.sub(url_token, text)
    for pattern in (WINDOWS_USER_PATH, POSIX_USER_PATH):
        text, count = pattern.subn("[path]", text)
        scrubbed["path"] += count
    text, count = IPV4.subn("[ip]", text)
    scrubbed["ip"] += count
    text, count = PHONE.subn("[phone]", text)
    scrubbed["phone"] += count
    return text, {"replacements": replacements, "scrubbed": {k: v for k, v in sorted(scrubbed.items()) if v},
                  "residual": residual_scan(text, replace, allow), "status": "clean"}


def residual_scan(text, replace, allow):
    """Names, money and absolute figures a reader could tie back to the client."""
    residual = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in CAPITALISED.finditer(line):
            words = re.split(r"[ \-]", match.group(0))
            if all(w.lower() in COMMON_CAPITALISED or w.lower() in allow for w in words):
                continue
            if match.group(0).lower() in allow:
                continue
            residual.append({"kind": "name", "text": match.group(0), "line": number})
        for match in CURRENCY.finditer(line):
            residual.append({"kind": "currency", "text": match.group(0), "line": number})
        for match in BIG_NUMBER.finditer(line):
            if PORT_PREFIX.search(line[:match.start()]) or match.group(0).lower() in allow:
                continue
            if len(match.group(0).replace(",", "")) < 5:
                continue
            if any(r["kind"] == "currency" and match.group(0) in r["text"] and r["line"] == number for r in residual):
                continue
            residual.append({"kind": "number", "text": match.group(0), "line": number})
        for term in replace:
            if replacement_pattern(term).search(line):
                residual.append({"kind": "replace_term", "text": term, "line": number})
    return residual


def redact_file(draft, config, out, report_file):
    text, report = redact_text(Path(draft).read_text(encoding="utf-8-sig"), config)
    report["status"] = "review" if report["residual"] else "clean"
    report.update({"source": str(Path(draft).resolve()), "article": str(Path(out).resolve()), "redacted_at": now()})
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(text, encoding="utf-8")
    dump(report_file, report)
    return {"article": str(Path(out).resolve()), "report": str(Path(report_file).resolve()),
            "status": report["status"], "residual": len(report["residual"]),
            "replacements": report["replacements"], "scrubbed": report["scrubbed"],
            "note": "Fix every residual item in the draft and rerun; do not publish an unexplained flag."}


# --- repository --------------------------------------------------------------
GIT_TIMEOUT_SECONDS = 300


def git(arguments, cwd=None, check=True):
    # Never wait on an invisible credential dialog: fail with a message instead.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", GIT_ASKPASS="echo")
    try:
        result = subprocess.run(["git", *arguments], cwd=str(cwd) if cwd else None, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_SECONDS)
    except FileNotFoundError:
        raise ValueError("git executable not found on PATH; the knowledge base is a git repository")
    except subprocess.TimeoutExpired:
        raise ValueError(f"git {' '.join(arguments)} did not finish within {GIT_TIMEOUT_SECONDS}s; check network access and stored credentials (gh auth setup-git)")
    if result.returncode != 0 and re.search(r"could not read Username|Authentication failed|terminal prompts disabled", result.stderr or ""):
        raise ValueError(f"git {' '.join(arguments)} needs credentials that are not stored; run `gh auth setup-git` or store them with the credential manager once, then rerun")
    if check and result.returncode != 0:
        raise ValueError(f"git {' '.join(arguments)} failed: {(result.stderr or result.stdout).strip()[:500]}")
    return result


def kb_path(config, project, override=None):
    if override:
        return Path(override).resolve()
    local = config.get("local_path", "kb")
    path = Path(local)
    return (path if path.is_absolute() else Path(project) / path).resolve()


def head_commit(path):
    result = git(["rev-parse", "HEAD"], cwd=path, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def ensure_branch(path, branch):
    current = git(["symbolic-ref", "--short", "HEAD"], cwd=path, check=False).stdout.strip()
    if current == branch:
        return
    if head_commit(path):
        if git(["checkout", branch], cwd=path, check=False).returncode != 0:
            git(["checkout", "-b", branch], cwd=path)
    else:
        git(["symbolic-ref", "HEAD", f"refs/heads/{branch}"], cwd=path)


def sync(config, project):
    """Clone the knowledge base beside the project, or fast-forward the existing clone."""
    repository, branch = config.get("repository"), config.get("branch", "main")
    if not repository:
        raise ValueError("kb.json needs a repository URL")
    path = kb_path(config, project)
    note = "cloned"
    if (path / ".git").is_dir():
        pull = git(["pull", "--ff-only", "origin", branch], cwd=path, check=False)
        note = "up to date" if pull.returncode == 0 else f"pull skipped: {(pull.stderr or pull.stdout).strip()[:200]}"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        git(["clone", str(repository), str(path)])
    ensure_branch(path, branch)
    articles = sorted((path / "articles").glob("*.md")) if (path / "articles").is_dir() else []
    return {"path": str(path), "branch": branch, "head": head_commit(path),
            "articles": len(articles), "note": note}


# --- article publishing ------------------------------------------------------
def load_topics():
    if not TOPICS_FILE.is_file():
        raise ValueError(f"Topic vocabulary missing: {TOPICS_FILE}")
    return read(TOPICS_FILE)["topics"]


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
    return slug[:80].strip("-") or "article"


def yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def front_matter(meta):
    """YAML front matter, written in the fixed key order the index expects."""
    lines = ["---"]
    for key in ["title", "date", "topics", "tags", "summary", "author", "approved_by",
                "plugin_version", "source", "anonymized"]:
        if key not in meta:
            continue
        value = meta[key]
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(yaml_scalar(v) for v in value)}]")
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    return "\n".join(lines + ["---", ""])


def parse_front_matter(text):
    """The small YAML subset front_matter writes; returns (meta, body)."""
    meta = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end < 0:
        return meta, text
    for line in text[3:end].splitlines():
        if ":" not in line or not line.strip():
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [unquote(v) for v in re.findall(r'"(?:[^"\\]|\\.)*"|[^,\[\]]+', value[1:-1]) if v.strip()]
        elif value in ("true", "false"):
            meta[key.strip()] = value == "true"
        else:
            meta[key.strip()] = unquote(value)
    return meta, text[end + 4:].lstrip("\n")


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def stem(token):
    for suffix, minimum in (("ing", 6), ("ed", 5), ("es", 5), ("s", 4)):
        if token.endswith(suffix) and len(token) >= minimum and not token.endswith("ss"):
            return token[: -len(suffix)]
    return token


def tokenise(text):
    return [stem(t) for t in re.findall(r"[a-z0-9]+", str(text).lower()) if len(t) > 2 and t not in STOPWORDS]


def extract_keywords(text, n=20):
    """Distinctive terms of one body: term frequency with a mild boost for longer terms."""
    counts = Counter(tokenise(text))
    scored = sorted(counts.items(), key=lambda kv: (-kv[1] * (1 + math.log(len(kv[0]))), kv[0]))
    return [term for term, _ in scored[:n]]


def build_index(repo_path):
    """Rebuild index.json from the articles on disk; the articles are the source of truth."""
    repo_path = Path(repo_path)
    articles = []
    for path in sorted((repo_path / "articles").glob("*.md")) if (repo_path / "articles").is_dir() else []:
        meta, body = parse_front_matter(path.read_text(encoding="utf-8-sig"))
        articles.append({"path": path.relative_to(repo_path).as_posix(),
                         "title": meta.get("title", path.stem), "date": meta.get("date", ""),
                         "topics": meta.get("topics", []), "tags": meta.get("tags", []),
                         "summary": meta.get("summary", ""),
                         "keywords": extract_keywords(body), "words": len(body.split())})
    articles.sort(key=lambda a: (a["date"], a["path"]), reverse=True)
    return {"generated_at": now(), "articles": articles}


README_INTRO = """# Analytics QA knowledge base

Anonymised know-how from real BI and marketing-analytics reviews run with the
`analytics-qa` Claude Code plugin. Each article describes an issue class the way a
reader can recognise it in their own report: the symptom on screen, how it was
detected, the root cause, and the data-model or DAX rule that avoids it. Client
names, people, paths, URLs and absolute business figures are removed before an
article is published, and every article was published only after the analyst who
ran the review approved it. The plugin's skills search this repository through
`retrospective.py search`, so write for that reader: concrete symptoms, named
detectors, reusable rules.
"""


def build_readme(index, topics, existing=None):
    marker = "<!-- generated: articles -->"
    intro = existing.split(marker)[0].rstrip() if existing and marker in existing else (existing or README_INTRO).rstrip()
    lines = [intro, "", marker, "", "## Articles", "", "| Date | Title | Topics | Summary |", "| --- | --- | --- | --- |"]
    for article in index["articles"]:
        summary = str(article.get("summary", "")).replace("|", "/")
        lines.append(f"| {article.get('date', '')} | [{article['title']}]({article['path']}) | "
                     f"{', '.join(article.get('topics', []))} | {summary} |")
    counts = Counter(t for a in index["articles"] for t in a.get("topics", []))
    lines += ["", "## Topics", ""]
    for topic in topics:
        lines.append(f"- **{topic['id']}** ({counts.get(topic['id'], 0)}) — {topic['label']}: {topic['description']}")
    lines += ["", f"_{len(index['articles'])} article(s). Generated by analytics-qa retrospective._", ""]
    return "\n".join(lines)


def plugin_version():
    try:
        return str(read(HERE.parent / ".claude-plugin/plugin.json")["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def check_publishable(article_text, config, report_file, accept_residual):
    """Everything that must hold before an article leaves the machine."""
    report = None
    if report_file and Path(report_file).is_file():
        report = read(report_file)
    if report is None:
        raise ValueError(f"No redaction report beside the article ({report_file}); run `retrospective.py redact` first")
    if report.get("status") == "review" and not accept_residual:
        items = "; ".join(f"{r['kind']} {r['text']!r} (line {r['line']})" for r in report.get("residual", [])[:8])
        raise ValueError(f"Redaction report status is 'review': fix the draft and rerun redact, or pass "
                         f"--accept-residual with a justification for each item. Residual: {items}")
    still_present = [t for t in (config.get("redaction", {}).get("replace") or {})
                     if replacement_pattern(t).search(article_text)]
    if still_present:
        raise ValueError(f"Configured redaction terms still appear in the article: {sorted(still_present)}")
    return report


def publish(config, project, article, title, topics, tags, summary, approved_by, confirm,
            report_file=None, accept_residual=False, push=True, kb=None):
    if not confirm:
        raise ValueError("Publishing needs --confirm and --approved-by, and those are only passed after the "
                         "user explicitly approved this article in chat")
    if not str(approved_by or "").strip():
        raise ValueError("--approved-by must name the person who approved publication in chat")
    vocabulary = load_topics()
    known = {t["id"] for t in vocabulary}
    unknown = [t for t in topics if t not in known]
    if not topics or unknown:
        raise ValueError(f"Unknown topic(s) {unknown or '(none given)'}; use `retrospective.py topics`. "
                         f"Vocabulary: {sorted(known)}")
    path = kb_path(config, project, kb)
    if not (path / ".git").is_dir():
        raise ValueError(f"No knowledge-base clone at {path}; run `retrospective.py sync` first")
    text = Path(article).read_text(encoding="utf-8-sig")
    report_file = report_file or Path(article).with_suffix(".report.json")
    check_publishable(text, config, report_file, accept_residual)
    date = dt.date.today().isoformat()
    meta = {"title": title, "date": date, "topics": list(topics), "tags": list(tags or []),
            "summary": summary, "author": config.get("author", "analytics-qa"), "approved_by": approved_by,
            "plugin_version": plugin_version(), "source": "retrospective", "anonymized": True}
    body = parse_front_matter(text)[1]
    target = path / "articles" / f"{date}-{slugify(title)}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(front_matter(meta) + body.strip() + "\n", encoding="utf-8")
    index = build_index(path)
    dump(path / "index.json", index)
    existing = (path / "README.md").read_text(encoding="utf-8-sig") if (path / "README.md").is_file() else None
    (path / "README.md").write_text(build_readme(index, vocabulary, existing), encoding="utf-8")
    if not (path / "topics.json").is_file():
        dump(path / "topics.json", {"topics": vocabulary})
    branch = config.get("branch", "main")
    author = f"{config.get('author', 'analytics-qa')} <analytics-qa@users.noreply.github.com>"
    git(["add", "-A"], cwd=path)
    git(["-c", f"user.name={config.get('author', 'analytics-qa')}",
         "-c", "user.email=analytics-qa@users.noreply.github.com",
         "commit", "-m", f"Add retrospective: {title}", "--author", author], cwd=path)
    pushed = False
    if push:
        git(["push", "-u", "origin", branch], cwd=path)
        pushed = True
    return {"path": str(target), "commit": head_commit(path), "pushed": pushed, "branch": branch,
            "articles": len(index["articles"]), "topics": list(topics),
            "url": article_url(config, branch, target.name)}


def article_url(config, branch, filename):
    repository = str(config.get("repository", "")).removesuffix(".git")
    return f"{repository}/blob/{branch}/articles/{filename}" if repository.startswith("http") else ""


# --- search ------------------------------------------------------------------
def field_tokens(entry):
    """Query-able fields with their weights; title and topics outrank the body."""
    return [(tokenise(entry.get("title", "")), 3), (tokenise(entry.get("summary", "")), 2),
            (tokenise(" ".join(list(entry.get("topics", [])) + list(entry.get("tags", [])))), 3),
            (tokenise(entry.get("body", "")), 1)]


def rank(entries, query, topic=None, limit=5):
    """TF-IDF over weighted article fields; exact topic filter. Pure, for tests and search."""
    candidates = [e for e in entries if not topic or topic in (e.get("topics") or [])]
    terms = set(tokenise(query))
    if not candidates or not terms:
        return []
    documents = [set(t for tokens, _ in field_tokens(e) for t in tokens) for e in candidates]
    total = len(candidates)
    idf = {term: math.log((total + 1) / (1 + sum(term in d for d in documents))) + 1 for term in terms}
    results = []
    for entry, document in zip(candidates, documents):
        score = 0.0
        for tokens, weight in field_tokens(entry):
            counts = Counter(tokens)
            length = max(len(tokens), 1)
            for term in terms:
                if counts[term]:
                    score += weight * idf[term] * (1 + math.log(counts[term])) / math.sqrt(length)
        if score <= 0:
            continue
        results.append({"path": entry.get("path"), "title": entry.get("title"), "date": entry.get("date", ""),
                        "topics": entry.get("topics", []), "score": round(score, 4),
                        "snippet": snippet(entry.get("body", ""), terms)})
    results.sort(key=lambda r: (-r["score"], r["path"] or ""))
    return results[:limit]


def snippet(body, terms, width=200):
    """The ~200-character window of the body that carries the most query terms."""
    text = " ".join(str(body).split())
    if not text:
        return ""
    words = text.split(" ")
    stems = [stem(re.sub(r"[^a-z0-9]", "", w.lower())) for w in words]
    best, best_hits = 0, -1
    for start in range(len(words)):
        length, end = 0, start
        while end < len(words) and length + len(words[end]) + 1 <= width:
            length += len(words[end]) + 1
            end += 1
        hits = sum(1 for s in stems[start:end] if s in terms)
        if hits > best_hits:
            best, best_hits = start, hits
        if end >= len(words):
            break
    window = []
    length = 0
    for word in words[best:]:
        if length + len(word) + 1 > width:
            break
        window.append(word)
        length += len(word) + 1
    return ("… " if best else "") + " ".join(window) + ("" if best + len(window) >= len(words) else " …")


def load_entries(path):
    """index.json entries with their article bodies attached."""
    path = Path(path)
    index = read(path / "index.json") if (path / "index.json").is_file() else build_index(path)
    entries = []
    for entry in index.get("articles", []):
        article = path / entry["path"]
        entry = dict(entry)
        entry["body"] = parse_front_matter(article.read_text(encoding="utf-8-sig"))[1] if article.is_file() else ""
        entries.append(entry)
    return entries


def search(config, project, query, topic=None, limit=5, kb=None):
    path = kb_path(config, project, kb)
    if not (path / ".git").is_dir():
        sync(config, project)
    if not path.is_dir():
        raise ValueError(f"No knowledge-base clone at {path} and the repository could not be cloned; run sync")
    return rank(load_entries(path), query, topic, limit)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    dig = sub.add_parser("digest", help="Summarise recent sessions, inputs and cases into a local digest")
    dig.add_argument("--project", required=True); dig.add_argument("--since-days", type=int, default=30)
    dig.add_argument("--sessions-dir"); dig.add_argument("--max-chars-per-session", type=int, default=40000)
    dig.add_argument("--out", required=True)
    red = sub.add_parser("redact", help="Anonymise a draft article and report what still looks identifying")
    red.add_argument("--in", dest="draft", required=True); red.add_argument("--config", required=True)
    red.add_argument("--project", default="."); red.add_argument("--out", required=True); red.add_argument("--report", required=True)
    syn = sub.add_parser("sync", help="Clone or fast-forward the knowledge-base repository")
    syn.add_argument("--config", required=True); syn.add_argument("--project", default=".")
    pub = sub.add_parser("publish", help="Publish an approved, redacted article and rebuild the index")
    pub.add_argument("--config", required=True); pub.add_argument("--project", default=".")
    pub.add_argument("--article", required=True); pub.add_argument("--title", required=True)
    pub.add_argument("--topics", required=True, help="Comma-separated ids from `retrospective.py topics`")
    pub.add_argument("--tags", default=""); pub.add_argument("--summary", required=True)
    pub.add_argument("--approved-by", default="", help="Who approved publication in chat")
    pub.add_argument("--confirm", action="store_true"); pub.add_argument("--report")
    pub.add_argument("--accept-residual", action="store_true"); pub.add_argument("--no-push", action="store_true")
    pub.add_argument("--kb")
    sea = sub.add_parser("search", help="Search the knowledge base other reviews published")
    sea.add_argument("--config", required=True); sea.add_argument("--project", default=".")
    sea.add_argument("--query", required=True); sea.add_argument("--topic"); sea.add_argument("--limit", type=int, default=5)
    sea.add_argument("--kb")
    sub.add_parser("topics", help="List the controlled topic vocabulary")
    a = p.parse_args()
    if a.command == "digest":
        result = digest(a.project, a.since_days, a.sessions_dir, a.max_chars_per_session)
        dump(a.out, result)
        result = {"out": str(Path(a.out).resolve()), "stats": result["stats"], "warning": DIGEST_WARNING}
    elif a.command == "redact":
        result = redact_file(a.draft, load_config(a.config, a.project), a.out, a.report)
    elif a.command == "sync":
        result = sync(load_config(a.config, a.project), a.project)
    elif a.command == "publish":
        result = publish(load_config(a.config, a.project), a.project, a.article, a.title,
                         [t.strip() for t in a.topics.split(",") if t.strip()],
                         [t.strip() for t in a.tags.split(",") if t.strip()], a.summary, a.approved_by,
                         a.confirm, a.report, a.accept_residual, not a.no_push, a.kb)
    elif a.command == "search":
        result = search(load_config(a.config, a.project), a.project, a.query, a.topic, a.limit, a.kb)
    else:
        result = load_topics()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
