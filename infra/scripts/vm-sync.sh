#!/usr/bin/env bash
# Mirrors the JAP checkouts on this Mac to the machine that runs their Docker stacks
# (make vm-sync). The Mac stays where the code is edited; the other machine only runs it.
#
# What goes across, for this checkout, each of its git worktrees, and every checkout named in
# JAP_VM_SYNC_EXTRA:
#   code      everything but build output and caches, deletions included, so the copy is
#             exact. Bind mounts and live reload pick a change up over there; a new
#             dependency or a migration still needs `make start` / `make migrate` there.
#   settings  .env, written by rsync and never printed. Live's own files (any other .env.*
#             and data-live/) cross only with ENV=live, which a coding agent may not run
#             (AGENTS.md), so that one is Aditya's to start.
#   data/     added to and never deleted from, and only while that checkout's stack is not
#             running on the VM: once it runs there, its data/ is the VM's to write.
# What does not: the databases. Each side has had its own since they were first copied.
#
# It only ever writes Mac -> VM, so work done in the VM's copy would be overwritten. Before a
# run it asks the VM whether anything changed there since the last one (a file written after
# the stamp that run left, or a different HEAD) and if so syncs nothing of that repository
# and names the file. FORCE=1 overwrites. This is a backstop, not a lock: an edit made on
# the VM in the seconds a sync takes is not seen. To work on the VM, turn the job off.
#
# Where the VM is stays out of the repository, which is public:
#   ~/.config/jap/vm-sync.conf   JAP_VM_HOST=<ssh alias>
#                                JAP_VM_DIR=<folder under its home, default projects>
#                                JAP_VM_SYNC_EXTRA="<other checkouts to sync, by path, with a
#                                                   colon between them as in PATH>"
#                                JAP_VM_TUNNEL=<ssh alias whose LocalForward lines carry the
#                                               stacks' ports, so localhost:<port> on the
#                                               Mac is the stack on the VM>
#
#   vm-sync.sh            sync now                     (make vm-sync [ENV=live] [FORCE=1])
#   vm-sync.sh auto-on    sync every two minutes       (make vm-sync-auto [ENV=live])
#   vm-sync.sh auto-off   stop doing that              (make vm-sync-auto-off)
#   vm-sync.sh status     is the job loaded, and the last lines it logged
#   vm-sync.sh ps         every container on the VM, running or not   (make vm-ps)
#   vm-sync.sh tunnel-on  keep the port forwards up, across sleep and network changes
#   vm-sync.sh tunnel-off drop them                    (make vm-tunnel / vm-tunnel-off)
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD

CONF=${JAP_VM_CONF:-$HOME/.config/jap/vm-sync.conf}
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
HOST=${JAP_VM_HOST:-}
DEST=${JAP_VM_DIR:-projects}
MODE=${VM_SYNC_ENV:-local}
AUTO=${VM_SYNC_AUTO:-}
EVERY=${JAP_VM_SYNC_EVERY:-120}
LABEL=com.jap.vm-sync
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
LOG=$HOME/Library/Logs/jap-vm-sync.log
TUNNEL_LABEL=com.jap.vm-tunnel
TUNNEL_PLIST=$HOME/Library/LaunchAgents/$TUNNEL_LABEL.plist
BLOCKED=$HOME/Library/Caches/jap-vm-sync.blocked
LOCK=${TMPDIR:-/tmp}/jap-vm-sync.lock
# One connection shared by every ssh and rsync of a run, and kept for the next run.
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 -o ControlMaster=auto -o ControlPersist=300 -o ControlPath=$HOME/.ssh/cm-jap-%C"

case "$MODE" in local | live) ;; *) echo "ENV must be local or live" >&2; exit 1 ;; esac
[ -n "$HOST" ] || { echo "No VM is configured. Put JAP_VM_HOST=<ssh alias> in $CONF" >&2; exit 1; }

# Never synced: what a build or a tool makes, and each side's own data folders.
FILTERS=(--exclude='.venv*/' --exclude=node_modules/ --exclude=__pycache__/ --exclude=.mypy_cache/
  --exclude=.ruff_cache/ --exclude=.pytest_cache/ --exclude=frontend/dist/
  --exclude=frontend/.vite/ --exclude='*.tsbuildinfo' --exclude=.DS_Store
  --exclude='/*/data/' --exclude='/*/data-live/')
DATA_DIRS="data"
if [ "$MODE" = live ]; then
  DATA_DIRS="data data-live"
else
  FILTERS+=(--include=.env.example --include=.env.tunnel.example --exclude='.env.*')
fi

say() { if [ -n "$AUTO" ]; then echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; else echo "$*"; fi; }

# One notification per stretch of trouble, not one every run.
notify() {
  [ -n "$AUTO" ] && [ ! -f "$BLOCKED" ] || return 0
  mkdir -p "$(dirname "$BLOCKED")" && : > "$BLOCKED"
  osascript -e "display notification \"$1\" with title \"JAP vm-sync\"" > /dev/null 2>&1 || true
}

# Runs on the VM. Prints `running <name>` for a checkout whose stack is up there and
# `changed <name>: <why>` for one that was touched there since its stamp.
read -r -d '' REMOTE_CHECK << 'EOF' || true
dest=$HOME/$1; mode=$2; shift 2
# Without ENV=live the other .env.* files are not synced, so the VM's own are not a change.
skip='.env.*'; [ "$mode" = live ] && skip='.vm-sync-nothing'
running=$(docker ps --format '{{.Label "com.docker.compose.project.working_dir"}}' 2> /dev/null || true)
for name in "$@"; do
  dir=$dest/$name; stamp=$dest/.vm-sync/$name
  case "$running" in *"$dir/"*) echo "running $name" ;; esac
  [ -f "$stamp" ] && [ -d "$dir" ] || continue
  if [ "$(git -C "$dir" rev-parse HEAD 2> /dev/null)" != "$(cat "$stamp")" ]; then
    echo "changed $name: a commit or a checkout made on the VM"; continue
  fi
  files=$(cd "$dir" && find . \( -name .git -o -name node_modules -o -name '.venv*' \
    -o -name __pycache__ -o -name .mypy_cache -o -name .ruff_cache -o -name .pytest_cache \
    -o -path ./data -o -path ./data-live -o -path ./frontend/dist -o -path ./frontend/.vite \) \
    -prune -o -type f -cnewer "$stamp" ! -name '*.tsbuildinfo' ! -name .DS_Store \
    \( -name .env.example -o -name .env.tunnel.example -o ! -name "$skip" \) -print \
    | head -4 | tr '\n' ' ')
  [ -z "$files" ] || echo "changed $name: $files"
done
EOF

# Runs on the VM after the code is across: points each worktree and the main checkout at
# each other by the VM's paths (the two files that hold them are not synced), then leaves
# the stamp the next check measures from.
read -r -d '' REMOTE_STAMP << 'EOF' || true
dest=$HOME/$1; main=$2; shift 2
mkdir -p "$dest/.vm-sync"
for pair in "$@"; do
  name=${pair%%:*}; id=${pair#*:}
  echo "gitdir: $dest/$main/.git/worktrees/$id" > "$dest/$name/.git"
  echo "$dest/$name/.git" > "$dest/$main/.git/worktrees/$id/gitdir"
done
for name in "$main" "${@%%:*}"; do
  git -C "$dest/$name" rev-parse HEAD > "$dest/.vm-sync/$name" 2> /dev/null || true
done
EOF

# One repository: its main checkout and every worktree of it.
sync_repo() {
  local repo=$1 dirs=() names=() pairs=() excludes=() d name main report changed out count
  [ -e "$repo/.git" ] || { say "skipped $repo: not a git checkout"; return 0; }
  while read -r d; do
    [ -e "$d/.git" ] || continue
    name=$(basename "$d")
    case "$name" in *[!A-Za-z0-9._\ -]*) say "skipped $d: a name the VM cannot take"; continue ;; esac
    dirs+=("$d") && names+=("$name")
    # A worktree's .git is a file holding a Mac path, and the main checkout holds the path
    # back; both are written on the VM instead.
    if [ -f "$d/.git" ]; then
      pairs+=("$name:$(basename "$(git -C "$d" rev-parse --git-dir)")")
      excludes+=("--exclude=/$name/.git")
    fi
  done < <(git -C "$repo" worktree list --porcelain | sed -n 's/^worktree //p')
  main=${names[0]}
  excludes+=("--exclude=/$main/.git/worktrees/*/gitdir")

  # Called as `sync_repo || ...`, where `set -e` does not apply: each step is checked here.
  # ssh hands the remote shell one string, so each argument is quoted for it: a checkout's
  # name may hold a space.
  report=$($SSH "$HOST" "bash -s -- $(printf '%q ' "$DEST" "$MODE" "${names[@]}")" <<< "$REMOTE_CHECK") || {
    say "FAILED: could not ask the VM about $main"; return 1
  }
  changed=$(sed -n 's/^changed //p' <<< "$report")
  if [ -n "$changed" ] && [ -z "${FORCE:-}" ]; then
    say "NOT synced: $main has work on the VM that a sync would overwrite"
    sed 's/^/    /' <<< "$changed"
    echo "    Copy it back to this Mac first, or overwrite it with: make vm-sync FORCE=1"
    notify "$main was changed on the VM. Nothing was synced; see $LOG"
    return 1
  fi

  out=$(rsync -a --delete --stats -e "$SSH" "${FILTERS[@]}" "${excludes[@]}" "${dirs[@]}" "$HOST:$DEST/") || {
    say "FAILED: rsync of $main stopped part way; the next run finishes it"
    notify "The sync of $main failed; see $LOG"
    return 1
  }
  $SSH "$HOST" "bash -s -- $(printf '%q ' "$DEST" "$main" ${pairs[@]+"${pairs[@]}"})" <<< "$REMOTE_STAMP" || {
    say "FAILED: could not leave the stamp for $main on the VM"; return 1
  }
  count=$(sed -n 's/^Number of files transferred: //p' <<< "$out")

  for d in "${dirs[@]}"; do
    name=$(basename "$d")
    grep -qx "running $name" <<< "$report" && continue
    for sub in $DATA_DIRS; do
      if [ -d "$d/$sub" ]; then
        rsync -a -u -e "$SSH" --exclude=.DS_Store "$d/$sub" "$HOST:$DEST/${name// /\\ }/" || {
          say "FAILED: $name/$sub did not finish; the next run continues it"; return 1
        }
      fi
    done
  done

  if [ -z "$AUTO" ] || [ "${count:-0}" != 0 ]; then
    say "synced $main and $((${#names[@]} - 1)) worktrees ($MODE): ${count:-0} files changed"
  fi
}

sync_all() {
  local repo failed=0 extras=()
  # A run that was killed leaves its lock; an hour is far longer than any run takes.
  find "$LOCK" -maxdepth 0 -mmin +60 -exec rmdir {} \; 2> /dev/null || true
  mkdir "$LOCK" 2> /dev/null || { [ -n "$AUTO" ] || echo "Another vm-sync is running."; exit 0; }
  trap 'rmdir "$LOCK"' EXIT
  if [ -n "$AUTO" ] && [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1000000 ]; then
    tail -n 200 "$LOG" > "$LOCK.keep" && cat "$LOCK.keep" > "$LOG" && rm -f "$LOCK.keep"
  fi
  if ! $SSH "$HOST" true 2> /dev/null; then
    # Off the network is ordinary for a laptop: the job says nothing and tries again.
    [ -n "$AUTO" ] && exit 0
    echo "Cannot reach $HOST over ssh." >&2
    exit 1
  fi
  IFS=: read -r -a extras <<< "${JAP_VM_SYNC_EXTRA:-}"
  for repo in "$ROOT" ${extras[@]+"${extras[@]}"}; do
    sync_repo "$repo" || failed=1
  done
  [ "$failed" = 0 ] && rm -f "$BLOCKED"
  return "$failed"
}

auto_on() {
  mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
  cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/infra/scripts/vm-sync.sh</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin</string>
    <key>VM_SYNC_ENV</key><string>$MODE</string>
    <key>VM_SYNC_AUTO</key><string>1</string>
  </dict>
  <key>StartInterval</key><integer>$EVERY</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2> /dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "vm-sync runs every $EVERY seconds from now on ($MODE). Log: $LOG"
  echo "Stop it with: make vm-sync-auto-off"
}

auto_off() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2> /dev/null || true
  rm -f "$PLIST" "$BLOCKED"
  echo "vm-sync no longer runs by itself."
}

status() {
  if launchctl print "gui/$(id -u)/$LABEL" > /dev/null 2>&1; then
    echo "The job is loaded: every $(/usr/libexec/PlistBuddy -c 'Print :StartInterval' "$PLIST") seconds," \
      "ENV=$(/usr/libexec/PlistBuddy -c 'Print :EnvironmentVariables:VM_SYNC_ENV' "$PLIST")."
  else
    echo "The job is not loaded. Turn it on with: make vm-sync-auto"
  fi
  [ -f "$BLOCKED" ] && echo "The last run was refused or failed."
  [ -f "$LOG" ] && tail -n 8 "$LOG"
  return 0
}

containers() {
  {
    printf 'STACK\tSERVICE\tSTATE\tSINCE\n'
    $SSH "$HOST" bash -s << 'EOF' | sort
docker ps -a --format '{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}\t{{.State}}\t{{.Status}}'
EOF
  } | column -t -s "$(printf '\t')"
}

# The forwards are one ssh that launchd restarts whenever it ends, so they come back after
# the Mac sleeps or changes network. Which ports is the ssh alias's business, not this file's.
tunnel_on() {
  [ -n "${JAP_VM_TUNNEL:-}" ] || { echo "Put JAP_VM_TUNNEL=<ssh alias with LocalForward lines> in $CONF" >&2; exit 1; }
  mkdir -p "$(dirname "$TUNNEL_PLIST")"
  cat > "$TUNNEL_PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$TUNNEL_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/ssh</string><string>-N</string>
    <string>-o</string><string>BatchMode=yes</string>
    <string>-o</string><string>ServerAliveInterval=30</string>
    <string>-o</string><string>ServerAliveCountMax=3</string>
    <string>$JAP_VM_TUNNEL</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/jap-vm-tunnel.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$TUNNEL_LABEL" 2> /dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$TUNNEL_PLIST"
  echo "The forwards of $JAP_VM_TUNNEL are up and stay up. Drop them with: make vm-tunnel-off"
}

tunnel_off() {
  launchctl bootout "gui/$(id -u)/$TUNNEL_LABEL" 2> /dev/null || true
  rm -f "$TUNNEL_PLIST"
  echo "The forwards are down."
}

case "${1:-sync}" in
  sync) sync_all ;;
  auto-on) auto_on ;;
  auto-off) auto_off ;;
  status) status ;;
  ps) containers ;;
  tunnel-on) tunnel_on ;;
  tunnel-off) tunnel_off ;;
  *) echo "Usage: vm-sync.sh [sync|auto-on|auto-off|status|ps|tunnel-on|tunnel-off]" >&2; exit 1 ;;
esac
