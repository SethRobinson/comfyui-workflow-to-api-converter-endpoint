"""File cleanup endpoints: /files/delete and /files/list.

An addition in this repository, on top of the workflow converter. ComfyUI core
cannot delete a file from output/ or input/ over HTTP - its only DELETE route is
/userdata/{file}, and the Assets API removes the database row while the file on
disk stays. API clients that fetch their result through /view therefore leave the
copy behind on the server forever.

This module adds the two endpoints the AiPic generator scripts use:

    POST /files/delete  {"files":[{"filename","subfolder","type"}, ...]}
    GET  /files/list?type=output&prefix=&older_than_min=&limit=&all=1

Safety: a target must be a plain relative path that resolves inside ComfyUI's own
output/, input/ or temp/ directory. Absolute paths, drive letters, UNC paths, "~"
and any ".." are refused before the filesystem is touched; symlinks/junctions that
resolve outside the base dir are refused too, and only regular files are removed.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from aiohttp import web

import folder_paths

logger = logging.getLogger(__name__)

MAX_FILES_PER_REQUEST = 64
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 2000

# Resolved per request: --output-directory and friends are applied after custom
# nodes are imported, so the getters are stored instead of their values.
BASE_DIRS = {
    "output": folder_paths.get_output_directory,
    "input": folder_paths.get_input_directory,
    "temp": folder_paths.get_temp_directory,
}

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def _as_parts(value) -> tuple[str, ...]:
    return Path(str(value).replace("\\", "/")).parts


def _inside(base: Path, path: Path) -> bool:
    """True when the fully resolved path stays inside base.

    Path arithmetic is not enough on Windows: a junction or symlink inside the
    base dir resolves somewhere else, so every entry is checked after resolution.
    """
    try:
        path.resolve().relative_to(base)
    except (OSError, ValueError):
        return False
    return True


def _resolve(type_name: str, subfolder, filename: str):
    """Resolve one request entry into (base_dir, target_path, error)."""
    get_base = BASE_DIRS.get(type_name)
    if get_base is None:
        return None, None, "type must be one of: %s" % ", ".join(sorted(BASE_DIRS))
    if not filename:
        return None, None, "filename is required"
    for part in (filename, subfolder):
        if part and (".." in _as_parts(part) or _DRIVE_PREFIX.match(str(part))
                     or str(part).startswith(("/", "\\"))
                     or str(part).startswith("~")):
            return None, None, "%r is not a relative path inside %s/" % (part, type_name)

    base = Path(get_base()).resolve()
    relative = str(subfolder or "").replace("\\", "/").strip("/")
    target = base / relative / filename
    if not _inside(base, target):
        return None, None, "resolves outside %s/ on the server" % type_name
    return base, target, None


def _label(base: Path, target: Path, type_name: str) -> str:
    return "%s/%s" % (type_name, target.relative_to(base).as_posix())


async def delete_files(request: web.Request) -> web.Response:
    """POST /files/delete - remove files the client already fetched locally."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid json body"}, status=400)
    if not isinstance(body, dict):
        # Valid json that is not an object ("null", a list, a bare number).
        return web.json_response({"ok": False, "error": "expected a json object with a 'files' list"}, status=400)

    files = body.get("files")
    if not isinstance(files, list) or not files:
        return web.json_response({"ok": False, "error": "'files' must be a non-empty list"}, status=400)
    if len(files) > MAX_FILES_PER_REQUEST:
        return web.json_response(
            {"ok": False, "error": "too many files (max %d per request)" % MAX_FILES_PER_REQUEST}, status=400)

    deleted, absent, skipped = [], [], []
    for item in files:
        if not isinstance(item, dict):
            skipped.append({"filename": str(item), "reason": "not an object"})
            continue
        type_name = str(item.get("type") or "output")
        filename = str(item.get("filename") or "")
        base, target, error = _resolve(type_name, item.get("subfolder"), filename)
        if error:
            skipped.append({"filename": filename, "reason": error})
            continue
        # Already gone is success, so a retry after a lost response stays idempotent.
        if not target.exists():
            absent.append(_label(base, target, type_name))
            continue
        if not target.is_file():
            skipped.append({"filename": filename, "reason": "not a regular file"})
            continue
        try:
            target.unlink()
        except OSError as exc:
            skipped.append({"filename": filename, "reason": "unlink failed: %s" % exc})
            continue
        deleted.append(_label(base, target, type_name))

    logger.info("[files/delete] %s deleted=%d absent=%d skipped=%d",
                request.remote, len(deleted), len(absent), len(skipped))
    if deleted or absent:
        logger.info("[files/delete] removed: %s", ", ".join(deleted + absent))
    for entry in skipped:
        logger.warning("[files/delete] refused %r: %s", entry["filename"], entry["reason"])

    return web.json_response({
        "ok": not skipped,
        "deleted": deleted,
        "absent": absent,
        "skipped": skipped,
    })


async def list_files(request: web.Request) -> web.Response:
    """GET /files/list - browse output/ (or input//temp/) to find cleanup candidates.

    `prefix` matches the path relative to the base dir, `older_than_min` keeps only
    files untouched for that many minutes, `all=1` lifts the default limit.
    """
    type_name = request.query.get("type", "output")
    get_base = BASE_DIRS.get(type_name)
    if get_base is None:
        return web.json_response({"error": "type must be one of: %s" % ", ".join(sorted(BASE_DIRS))}, status=400)

    try:
        older_than_min = float(request.query.get("older_than_min") or 0)
        limit = int(request.query.get("limit") or DEFAULT_LIST_LIMIT)
    except ValueError:
        return web.json_response({"error": "older_than_min and limit must be numbers"}, status=400)

    base = Path(get_base()).resolve()
    if not base.is_dir():
        return web.json_response({"error": "%s/ not found on the server" % type_name}, status=404)

    prefix = request.query.get("prefix") or ""
    cutoff = time.time() - older_than_min * 60 if older_than_min > 0 else None
    return_all = request.query.get("all") in ("1", "true", "yes")
    if not return_all:
        limit = min(max(limit, 1), MAX_LIST_LIMIT)

    matches = []
    for dirpath, dirnames, filenames in os.walk(base):
        # A linked directory would walk the listing (and the cleanup) out of the base dir.
        dirnames[:] = [d for d in dirnames if _inside(base, Path(dirpath) / d)]
        for name in filenames:
            path = Path(dirpath) / name
            if not _inside(base, path):
                continue
            relative = path.relative_to(base).as_posix()
            if prefix and not relative.startswith(prefix):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if cutoff is not None and stat.st_mtime >= cutoff:
                continue
            matches.append((relative, stat.st_size, int(stat.st_mtime)))

    matches.sort(key=lambda row: row[2], reverse=True)
    total = len(matches)
    truncated = not return_all and total > limit
    if not return_all:
        matches = matches[:limit]

    files = []
    for relative, size, modified in matches:
        path = Path(relative)
        files.append({
            "filename": path.name,
            "subfolder": "" if path.parent == Path(".") else path.parent.as_posix(),
            "type": type_name,
            "bytes": size,
            "modified": modified,
        })

    return web.json_response({
        "type": type_name,
        "directory": str(base),
        "total": total,
        "truncated": truncated,
        "files": files,
    })


def register_file_endpoints(routes) -> None:
    routes.post("/files/delete")(delete_files)
    routes.get("/files/list")(list_files)
