#!/usr/bin/env bash
# Build a starter library, then commit it so a fresh deploy is not empty.
#
# Costs roughly 15c per paper, once. Pick papers your audience will recognise
# on sight -- the point is that they arrive at finished output instead of a
# progress bar.
set -u

PAPERS="${@:-1706.03762 2005.14165 2312.00752 2010.11929 2305.18290 1810.04805}"

mkdir -p seed
for id in $PAPERS; do
  echo "=== $id ==="
  paper2vid "$id" --format web --library library || echo "  skipped $id"
done

cp -f library/*.html library/*.json seed/ 2>/dev/null
echo
echo "seed/ now holds $(ls seed/*.html 2>/dev/null | wc -l) pages"
du -sh seed 2>/dev/null
echo "commit them:  git add seed && git commit -m 'seed library' && git push"
