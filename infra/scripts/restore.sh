#!/usr/bin/env sh
# Restores a backup made by backup.sh: infra/scripts/restore.sh data/backups/<file>.sql.gz
#
# A .sql.gz.age file is an encrypted dump (IDEA-11). It is decrypted here, on the machine
# running the restore, with the private key named by KEY (or JAP_BACKUP_AGE_KEY) — which is
# the whole point of encrypting it: that key is not on the server, so the server cannot read
# its own backups and neither can anyone who takes it.
set -eu
COMPOSE="${COMPOSE:-docker compose -f infra/compose.yaml}"
FILE="${1:?Usage: restore.sh data/backups/<file>.sql.gz}"
KEY="${KEY:-${JAP_BACKUP_AGE_KEY:-}}"
[ -f "$FILE" ] || { echo "No such file: $FILE" >&2; exit 1; }

case "$FILE" in
  *.age)
    command -v age > /dev/null || {
      echo "$FILE is encrypted and age is not installed here (brew install age)." >&2
      exit 1
    }
    [ -n "$KEY" ] || {
      echo "$FILE is encrypted. Say which private key opens it:" >&2
      echo "  make restore FILE=$FILE KEY=~/.config/jap/backup-key.txt" >&2
      exit 1
    }
    [ -f "$KEY" ] || { echo "No such key file: $KEY" >&2; exit 1; }
    DECRYPT="age --decrypt --identity $KEY"
    ;;
  *)
    DECRYPT="cat"
    ;;
esac

printf 'This replaces the current database with %s. Type yes to continue: ' "$FILE"
read -r answer
[ "$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
# shellcheck disable=SC2086
$DECRYPT < "$FILE" | gunzip -c | $COMPOSE exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
echo "Restored $FILE"
