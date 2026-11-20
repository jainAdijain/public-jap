#!/usr/bin/env sh
# Copy the server's newest database dump to this Mac (make backup-pull SERVER=<ssh host>).
#
# The server's own backups are Hetzner's disk snapshots plus the daily pg_dump in
# data-live/backups, and both of those live with the server: lose the hosting account and
# you lose every copy at once. This is the off-site one — hardware you own, reachable
# without the provider — and it costs nothing but the transfer.
#
# Read-only on the server: it lists and copies, and changes nothing there.
set -eu
cd "$(dirname "$0")/../.."

SERVER="${1:-}"
[ -n "$SERVER" ] || {
  echo "Usage: make backup-pull SERVER=<ssh host>   (a Host entry in ~/.ssh/config)" >&2
  exit 1
}
REMOTE_DIR="${JAP_SERVER_DIR:-/srv/public-jap}/data-live/backups"
LOCAL_DIR="data/backups-live"

newest=$(ssh "$SERVER" "ls -1t '$REMOTE_DIR'/jap-*.sql.gz 2>/dev/null | head -1") || {
  echo "Could not list $REMOTE_DIR on $SERVER" >&2
  exit 1
}
[ -n "$newest" ] || { echo "No backup in $REMOTE_DIR on $SERVER yet." >&2; exit 1; }

mkdir -p "$LOCAL_DIR"
name=$(basename "$newest")
if [ -f "$LOCAL_DIR/$name" ]; then
  echo "Already here: $LOCAL_DIR/$name"
  exit 0
fi
scp -q "$SERVER:$newest" "$LOCAL_DIR/$name.part"
mv "$LOCAL_DIR/$name.part" "$LOCAL_DIR/$name"
echo "Pulled $LOCAL_DIR/$name ($(wc -c < "$LOCAL_DIR/$name" | tr -d ' ') bytes)"
echo "Copies kept here: $(ls -1 "$LOCAL_DIR"/jap-*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
