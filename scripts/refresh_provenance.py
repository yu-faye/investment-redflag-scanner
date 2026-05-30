"""Refresh provenance fields in data/sources.json.

For every entry, compute:
  - sha256 of local_pdf and local_txt (when present)
  - latest git commit SHA that touched each file
  - github_blob_url + github_raw_url derived from config/provenance.json

Run:
  .venv/bin/python scripts/refresh_provenance.py

Idempotent: re-running just refreshes the SHAs/URLs in place.
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
SOURCES_PATH = os.path.join(PROJECT_ROOT, "data", "sources.json")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "provenance.json")


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _sha256_of(path):
    if not path:
        return None
    abs_path = os.path.join(PROJECT_ROOT, path)
    if not os.path.isfile(abs_path):
        return None
    h = hashlib.sha256()
    with open(abs_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_sha_for(path):
    """Latest git commit SHA that touched `path`. None if file is untracked
    or the repo has no history yet."""
    if not path:
        return None
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", path],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        sha = out.stdout.strip()
        return sha or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _git_head_sha():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _build_urls(cfg, path, sha):
    if not path:
        return {}
    owner = cfg["github_owner"]
    repo = cfg["github_repo"]
    branch = cfg.get("branch", "main")
    head_url = cfg["head_blob_url_template"].format(
        owner=owner, repo=repo, branch=branch, path=path
    )
    out = {"github_head_url": head_url}
    if sha:
        out["github_blob_url"] = cfg["blob_url_template"].format(
            owner=owner, repo=repo, sha=sha, path=path
        )
        out["github_raw_url"] = cfg["raw_url_template"].format(
            owner=owner, repo=repo, sha=sha, path=path
        )
    return out


def refresh():
    cfg = _load_json(CONFIG_PATH)
    if cfg["github_owner"] == "REPLACE_ME":
        print(
            "[refresh_provenance] github_owner is still REPLACE_ME in "
            "config/provenance.json; URLs will use the placeholder. Edit the "
            "config and re-run to get clickable links."
        )

    registry = _load_json(SOURCES_PATH)
    head_sha = _git_head_sha()
    enriched_count = 0
    missing_files = []

    for entry in registry["sources"]:
        prov = {
            "computed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repo_head_sha": head_sha,
        }
        for kind in ("local_pdf", "local_txt"):
            path = entry.get(kind)
            if not path:
                continue
            abs_path = os.path.join(PROJECT_ROOT, path)
            sha256 = _sha256_of(path)
            git_sha = _git_sha_for(path)
            if sha256 is None and not os.path.isfile(abs_path):
                missing_files.append(path)
            prov[kind] = {
                "path": path,
                "exists": os.path.isfile(abs_path),
                "sha256": sha256,
                "git_sha": git_sha,
                **_build_urls(cfg, path, git_sha or head_sha),
            }
        entry["provenance"] = prov
        enriched_count += 1

    registry["provenance_config"] = {
        "github_owner": cfg["github_owner"],
        "github_repo": cfg["github_repo"],
        "branch": cfg.get("branch", "main"),
        "head_sha": head_sha,
        "pages_url": cfg["pages_url_template"].format(
            owner=cfg["github_owner"], repo=cfg["github_repo"]
        ),
    }
    registry["refreshed_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with open(SOURCES_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)

    print(f"[refresh_provenance] enriched {enriched_count} sources")
    if missing_files:
        print(
            f"[refresh_provenance] WARNING: {len(missing_files)} files referenced in "
            "sources.json do not exist locally:"
        )
        for p in missing_files:
            print(f"  - {p}")
    print(f"[refresh_provenance] head_sha={head_sha or 'NONE (git history empty)'}")
    return registry


if __name__ == "__main__":
    refresh()
