#!/usr/bin/env bash
# Idempotent bootstrap for the qben_redflag_scanner repo.
# - initialises git if needed
# - installs git-lfs and tracks *.pdf
# - performs the first commit if none exists
# - optionally creates the GitHub remote via gh and pushes
#
# Usage:
#   bash scripts/init_repo.sh                  # local-only bootstrap
#   bash scripts/init_repo.sh --remote         # also create gh remote + push
#
# Requires (for --remote): gh CLI authenticated with `gh auth login`.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

DO_REMOTE=0
if [[ "${1:-}" == "--remote" ]]; then
  DO_REMOTE=1
fi

CONFIG_FILE="config/provenance.json"
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[init_repo] missing $CONFIG_FILE"
  exit 1
fi

OWNER="$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['github_owner'])")"
REPO="$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['github_repo'])")"
BRANCH="$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['branch'])")"

if [[ "$OWNER" == "REPLACE_ME" || -z "$OWNER" ]]; then
  echo "[init_repo] please set github_owner in $CONFIG_FILE before running"
  exit 1
fi

if [[ ! -d .git ]]; then
  echo "[init_repo] git init"
  git init -b "$BRANCH"
else
  echo "[init_repo] git repo already initialised"
fi

if ! command -v git-lfs >/dev/null 2>&1; then
  echo "[init_repo] git-lfs not found. install via: brew install git-lfs"
  exit 1
fi

git lfs install --local
git lfs track "*.pdf" >/dev/null
git add .gitattributes

if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  echo "[init_repo] creating initial commit"
  git add -A
  git commit -m "v3 bootstrap: provenance + dashboard scaffold" >/dev/null
else
  echo "[init_repo] HEAD already exists, skipping initial commit"
fi

if [[ "$DO_REMOTE" -eq 1 ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "[init_repo] gh CLI not found. install via: brew install gh"
    exit 1
  fi
  if ! git remote get-url origin >/dev/null 2>&1; then
    echo "[init_repo] creating gh repo $OWNER/$REPO"
    gh repo create "$OWNER/$REPO" --public --source=. --remote=origin --push
  else
    echo "[init_repo] remote origin already set; pushing"
    git push -u origin "$BRANCH"
  fi
fi

echo "[init_repo] done. next: .venv/bin/python scripts/refresh_provenance.py"
