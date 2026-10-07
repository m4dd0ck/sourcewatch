#!/usr/bin/env bash
# One-time: create the orphan `data` branch that daily runs commit observations to.
# Run from the repo root after the first push of main. Safe to re-run: exits if the branch exists.
set -euo pipefail
if git ls-remote --exit-code --heads origin data >/dev/null 2>&1; then
  echo "data branch already exists on origin"; exit 0
fi
tmp=$(mktemp -d)
git -C "$tmp" init --initial-branch data -q
cat > "$tmp/README.md" <<'MD'
# sourcewatch observations

One Parquet file per day under `history/`, plus `state/` (incidents, baselines, last run).
Written by the Daily run workflow as github-actions[bot]; read by `sourcewatch site`.
MD
git -C "$tmp" add README.md
git -C "$tmp" -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit -q -m "data: initialise the observations branch"
git -C "$tmp" remote add origin "$(git remote get-url origin)"
git -C "$tmp" push -q origin data
rm -rf "$tmp"
echo "created and pushed the data branch"
