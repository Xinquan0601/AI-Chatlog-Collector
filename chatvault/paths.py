"""Helpers for user-relative collection paths.

Agent clients keep their data inside the current operating system user profile
(for example C:/Users/<name>/.claude/projects, /home/<name>/.codex/sessions or
/Users/<name>/.zcode).  Persisting a concrete account name makes a database or
config unusable on another workstation.

AgentTrail therefore stores every configured root in a portable form:

* "~/.claude/projects" expands to the home directory of the current user;
* "%APPDATA%/Code/User" is expanded through environment variables.

Legacy configurations that still contain an absolute path such as
"C:/Users/Administrator/.claude/projects" are recognised and rewritten to
"~/.claude/projects" by normalize_roots(), so an existing database keeps
working after switching accounts or machines.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

HOME_TOKEN = "~"
BACKSLASH = chr(92)

# Entries below a "users" directory that are not personal profiles.
_RESERVED_USER_NAMES = {
    "public",
    "default",
    "default user",
    "default user0",
    "all users",
    "defaultapppool",
    "shared",
}

# Both patterns run on slash-unified text, which keeps this module free of
# platform specific escaping.  The optional drive covers C:/Users/<name>.
_WINDOWS_HOME_RE = re.compile(r"^(?:[A-Za-z]:)?/Users/([^/]+)(/.*)?$", re.IGNORECASE)
_POSIX_HOME_RE = re.compile(r"^/(?:home|Users)/([^/]+)(/.*)?$", re.IGNORECASE)


def user_home() -> Path:
    """Return the home directory of the current operating system user."""
    try:
        return Path.home()
    except RuntimeError:  # pragma: no cover - unusual environments
        return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")


def username() -> str:
    """Return the current account name for display and diagnostics."""
    for key in ("USERNAME", "USER", "LOGNAME"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return user_home().name


def profile_dir() -> str:
    return str(user_home())


def unify_separators(text: str) -> str:
    """Return text with backslashes converted to slashes and long-path prefix removed."""
    value = str(text or "").replace(BACKSLASH, "/")
    if value.startswith("//?/"):
        value = value[4:]
    return value


def expand_env(text: str) -> str:
    """Expand $VAR / %VAR% placeholders without touching the filesystem."""
    expanded = os.path.expandvars(text)
    if "%" in expanded:
        for name, value in os.environ.items():
            expanded = expanded.replace("%" + name + "%", value)
    return expanded


def _strip_home_prefix(unified: str, home: Path | None = None) -> str | None:
    """Return the part below a user home, or None when text is not user scoped."""
    home_path = Path(home) if home is not None else user_home()
    home_unified = unify_separators(str(home_path)).rstrip("/")
    lowered = unified.lower()
    if home_unified:
        home_lower = home_unified.lower()
        if lowered == home_lower:
            return ""
        if lowered.startswith(home_lower + "/"):
            return unified[len(home_unified) + 1:]
    match = _WINDOWS_HOME_RE.match(unified)
    if match and match.group(1).lower() not in _RESERVED_USER_NAMES:
        return (match.group(2) or "").strip("/")
    match = _POSIX_HOME_RE.match(unified)
    if match and match.group(1).lower() not in _RESERVED_USER_NAMES:
        return (match.group(2) or "").strip("/")
    return None


def to_user_template(path: str | os.PathLike[str] | None, *, home: Path | None = None) -> str:
    """Rewrite a user-scoped absolute path as a "~"-relative template.

    Non user scoped paths are returned unchanged (surrounding quotes and
    redundant whitespace removed).
    """
    if path is None:
        return ""
    text = str(path).strip().strip('"').strip("'")
    if not text:
        return ""
    text = expand_env(text)
    unified = unify_separators(text)
    if unified == HOME_TOKEN or unified.startswith(HOME_TOKEN + "/"):
        rest = unified[1:].strip("/")
        return HOME_TOKEN + "/" + rest if rest else HOME_TOKEN
    rest = _strip_home_prefix(unified, home)
    if rest is None:
        return text
    return HOME_TOKEN + "/" + rest if rest else HOME_TOKEN


def expand_user_path(path: str | os.PathLike[str] | None, *, home: Path | None = None) -> str:
    """Expand "~" and environment variables into an absolute path string."""
    if path is None:
        return ""
    text = str(path).strip().strip('"').strip("'")
    if not text:
        return ""
    text = expand_env(text)
    unified = unify_separators(text)
    home_path = Path(home) if home is not None else user_home()
    if unified == HOME_TOKEN:
        return str(home_path)
    if unified.startswith(HOME_TOKEN + "/"):
        rest = unified[2:].strip("/")
        return str(home_path / rest) if rest else str(home_path)
    return text


def normalize_root(path: str, *, home: Path | None = None) -> str:
    """Normalize a single root into its canonical portable form."""
    template = to_user_template(path, home=home)
    if not template:
        return ""
    if is_user_template(template):
        rest = unify_separators(template[1:]).strip("/")
        return HOME_TOKEN + "/" + rest if rest else HOME_TOKEN
    text = template.strip()
    while len(text) > 1 and text[-1] in ("/", BACKSLASH):
        if len(text) == 3 and text[1] == ":":
            break
        text = text[:-1]
    return text


def normalize_roots(roots: Iterable[str] | None, *, home: Path | None = None) -> list[str]:
    """Normalize, de-duplicate and drop empty roots."""
    result: list[str] = []
    seen: set[str] = set()
    for root in roots or []:
        normalized = normalize_root(str(root), home=home)
        if not normalized:
            continue
        key = os.path.normcase(normalized)
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def is_user_template(path: str) -> bool:
    unified = unify_separators((path or "").strip())
    return unified == HOME_TOKEN or unified.startswith(HOME_TOKEN + "/")


def describe_root(path: str, *, home: Path | None = None) -> dict[str, str]:
    """Return the stored template together with its resolved absolute path."""
    template = normalize_root(path, home=home)
    resolved = expand_user_path(template, home=home)
    try:
        resolved = str(Path(resolved).expanduser())
    except OSError:
        pass
    return {"template": template, "resolved": resolved}
