#!/bin/sh
# Installed over /usr/local/bin/agent-browser (deploy/Dockerfile). Hermes gives every browser
# session its own agent-browser daemon and socket dir (AGENT_BROWSER_SOCKET_DIR) but every one
# inherits the single AGENT_BROWSER_PROFILE, so a second session's Chrome died on the first one's
# SingletonLock ("the browser would not open: a stale Chrome profile lock", 2026-11-09, T-0795).
# This picks the profile per session, then execs the real binary: the session that holds the
# shared (logged-in) profile keeps it; a session that finds it held works on a private,
# empty profile inside its own socket dir, which Hermes deletes when it discards the session.
# It never prints or logs profile contents. Not covered: two sessions starting in the same
# millisecond are serialised by the flock, but nothing here has run against two real Chromes.
real=${AGENT_BROWSER_REAL:-/usr/local/lib/node_modules/agent-browser/bin/agent-browser-linux-x64}
shared=${AGENT_BROWSER_PROFILE:-}
mine=${AGENT_BROWSER_SOCKET_DIR:-}
[ -n "$shared" ] && [ -n "$mine" ] || exec "$real" "$@"
owner_file="$shared.owner"

# a *.pid in the socket dir $1 naming a live process: that session's daemon is up
daemon_alive() {
    for f in "$1"/*.pid; do
        [ -f "$f" ] || continue
        pid=$(cat "$f" 2>/dev/null)
        case $pid in '' | *[!0-9]*) continue ;; esac
        kill -0 "$pid" 2>/dev/null && return 0
    done
    return 1
}

# SingletonLock is a symlink to "host-pid": live only when that pid is a Chrome, so a pid the
# kernel has since handed to something else does not count
chrome_live() {
    pid=$(readlink "$shared/SingletonLock" 2>/dev/null)
    pid=${pid##*-}
    case $pid in '' | *[!0-9]*) return 1 ;; esac
    kill -0 "$pid" 2>/dev/null || return 1
    exe=$(tr '\0' '\n' <"/proc/$pid/cmdline" 2>/dev/null | head -n 1)
    case ${exe##*/} in chrom*) return 0 ;; esac
    return 1
}

# prints the profile this session uses; runs under the flock
pick() {
    if [ -d "$mine/profile" ] && daemon_alive "$mine"; then
        echo "$mine/profile" # already private, and its daemon is running: never flip mid-life
        return
    fi
    owner=$(cat "$owner_file" 2>/dev/null)
    if [ "$owner" != "$mine" ]; then
        if [ -n "$owner" ] && { daemon_alive "$owner" || [ -n "$(find "$owner_file" -mmin -1 2>/dev/null)" ]; } ||
            chrome_live; then
            mkdir -p "$mine/profile" && chmod 700 "$mine/profile" && echo "$mine/profile" && return
        fi
    fi
    # free, stale, or mine: a lock whose Chrome is gone would make the next Chrome refuse
    chrome_live || rm -f "$shared/SingletonLock" "$shared/SingletonCookie" "$shared/SingletonSocket"
    mkdir -p "$mine" && : >"$mine/profile-shared"
    echo "$mine" >"$owner_file.$$" && mv "$owner_file.$$" "$owner_file"
    echo "$shared"
}

# fd 9 is the lock and stays in the subshell: the daemon the real binary spawns must not inherit it
profile=$( (flock 9 && pick) 9>"$shared.lock" 2>/dev/null) || profile=
[ -n "$profile" ] || profile=$shared
export AGENT_BROWSER_PROFILE="$profile"
exec "$real" "$@"
