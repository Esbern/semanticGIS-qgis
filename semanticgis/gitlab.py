"""Writing to an extension project on GitLab: commits through GitLab's API (no git on the user's
machine) and data files in the project's package registry. The user's GitLab token is added to
each request by access.py.

A save names the version of each file it started from (last_commit_id); when someone else has
changed the file since, GitLab refuses the commit and ConflictError says which file.
"""

import json
from urllib.parse import quote

from .extensions import project_api
from .network import send


class GitLabError(Exception):
    pass


class ConflictError(GitLabError):
    """A file was changed by someone else since it was read."""


def commit(extension, message, actions):
    """One commit with several file actions: {"action": "create"|"update", "file_path", "content",
    "last_commit_id" (for updates)}. Returns the new commit's id."""
    body = json.dumps({"branch": extension.branch, "commit_message": message, "actions": actions}).encode("utf-8")
    status, reply = send("POST", f"{project_api(extension)}/repository/commits", body)
    data = _json(reply)
    if status == 201:
        return data["id"]
    message = data.get("message") if isinstance(data, dict) else reply[:300]
    if status == 400 and "has changed since you started editing" in str(message):
        raise ConflictError(str(message))
    raise GitLabError(f"GitLab refused the commit (HTTP {status}): {message}")


def upload_package_file(extension, package, version, file_name, path):
    """Upload a file to the project's generic package registry; returns its download URL."""
    url = (f"{project_api(extension)}/packages/generic/{quote(package, safe='')}/"
           f"{quote(version, safe='')}/{quote(file_name, safe='')}")
    with open(path, "rb") as f:
        status, reply = send("PUT", url, f.read(), "application/octet-stream")
    if status not in (200, 201):
        raise GitLabError(f"Upload of {file_name} failed (HTTP {status}): {_json(reply)}")
    return url


def _json(data):
    try:
        return json.loads(data or b"{}")
    except ValueError:
        return {"message": data[:300].decode("utf-8", "replace")}


def last_change(extension, path, get):
    """Who last changed a file, when and why: (author, date, commit title), or None."""
    url = (f"{project_api(extension)}/repository/commits?ref_name={quote(extension.branch, safe='')}"
           f"&path={quote(path, safe='')}&per_page=1")
    try:
        commits = json.loads(get(url))
    except (OSError, ValueError):
        return None
    if not commits:
        return None
    c = commits[0]
    return c.get("author_name", "?"), (c.get("created_at") or "")[:16].replace("T", " "), c.get("title", "")
