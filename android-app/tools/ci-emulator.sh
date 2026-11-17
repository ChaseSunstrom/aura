#!/usr/bin/env bash
# Boot (and stop) the CI emulator, without reactivecircus/android-emulator-runner.
#
#   bash android-app/tools/ci-emulator.sh boot
#   bash android-app/tools/ci-emulator.sh stop
#
# On GitHub the action did three things for the e2e job: install the system
# image, create the AVD, and boot it with a readiness gate. This does the same
# three, with the two options that action taught us the hard way carried over
# verbatim — see `-no-snapshot-load` below, and e2e.yml's header.
#
# Environment (all set by the job; the defaults are the job's values):
#   ANDROID_HOME          the SDK (sdkmanager, platform-tools, emulator live under it)
#   ANDROID_AVD_HOME      where the AVD is created — a cached directory
#   API_LEVEL             30
#   EMULATOR_TARGET       google_apis (never google_apis_playstore: unrootable, slower)
#   EMULATOR_ARCH         x86_64
#   EMULATOR_PROFILE      pixel_2 — one fixed geometry so screenshots compare run to run
#   ANDROID_SERIAL        emulator-5554 — the console port is taken from it, so adb
#                         in the rest of the job targets this emulator and no other
#
# `-e`: everything here is setup, and a setup step that fails must stop the
# job before the suite runs against nothing. Contrast run-instrumented-e2e.sh,
# which deliberately runs without errexit because it collects evidence.
set -euo pipefail

cmd="${1:-boot}"
API_LEVEL="${API_LEVEL:-30}"
TARGET="${EMULATOR_TARGET:-google_apis}"
ARCH="${EMULATOR_ARCH:-x86_64}"
PROFILE="${EMULATOR_PROFILE:-pixel_2}"
SERIAL="${ANDROID_SERIAL:-emulator-5554}"
PORT="${SERIAL#emulator-}"
AVD="ci-api${API_LEVEL}-${TARGET}-${ARCH}-${PROFILE}"
IMAGE="system-images;android-${API_LEVEL};${TARGET};${ARCH}"
ARTIFACTS="${EMULATOR_ARTIFACTS:-android-app/artifacts}"

: "${ANDROID_HOME:?ANDROID_HOME must point at the SDK}"
export ANDROID_AVD_HOME="${ANDROID_AVD_HOME:-$HOME/.android/avd}"
SDKMANAGER="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
AVDMANAGER="$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager"
ADB="$ANDROID_HOME/platform-tools/adb"
EMULATOR="$ANDROID_HOME/emulator/emulator"

case "$cmd" in
  boot)
    # KVM first, before a gigabyte of downloads: without it the emulator
    # falls back to software virtualisation and never boots inside the job's
    # timeout, and the failure would arrive twenty minutes later as a
    # readiness timeout that reads like a flaky boot.
    if [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; then
      echo "ERROR: /dev/kvm is not readable and writable by this job. The runner needs KVM (tag: kvm; a Docker executor with devices = [\"/dev/kvm\"], or a shell executor in the kvm group)."
      ls -l /dev/kvm 2>&1 || true
      exit 1
    fi

    mkdir -p "$ARTIFACTS" "$ANDROID_AVD_HOME"
    yes | "$SDKMANAGER" --licenses >/dev/null 2>&1 || true
    # The emulator package and the system image are what the action's own
    # setup installed; platform-tools is already there from
    # bootstrap-toolchain.sh but sdkmanager is idempotent about it.
    "$SDKMANAGER" --install "platform-tools" "emulator" "$IMAGE" >/dev/null

    if [ -d "$ANDROID_AVD_HOME/$AVD.avd" ]; then
      echo "AVD $AVD restored from the cache"
    else
      echo "creating AVD $AVD from $IMAGE (profile $PROFILE)"
      # `echo no`: avdmanager asks whether to create a custom hardware
      # profile; the device profile is the answer.
      echo no | "$AVDMANAGER" create avd --force --name "$AVD" --package "$IMAGE" --device "$PROFILE" >/dev/null
    fi

    # `-no-snapshot-load` is doing real work here; do not "optimise" it away.
    # Restoring a snapshot made the GitHub job fail on EVERY run once the AVD
    # cache started hitting: `sys.boot_completed` is part of the state a
    # snapshot restores, so it reads `1` the instant the snapshot loads and
    # says nothing about whether THIS boot's system_server is alive. The
    # readiness gate below is therefore vacuous on a restore, and the unlock
    # keyevent sent immediately afterwards lands on a system that is still
    # coming back up — `android.os.DeadSystemException` before a single test.
    # A cold boot costs about a minute; the AVD cache still earns its keep by
    # skipping `avdmanager create` and the system-image unpack.
    # `-no-snapshot-save`: a boot snapshot is a gigabyte of disk and restore
    # time buying nothing once nothing loads it.
    echo "booting $AVD on port $PORT"
    nohup "$EMULATOR" -avd "$AVD" -port "$PORT" \
      -no-snapshot-load -no-snapshot-save -no-window -gpu swiftshader_indirect \
      -noaudio -no-boot-anim -camera-back none \
      > "$ARTIFACTS/emulator.log" 2>&1 &
    echo $! > "$ARTIFACTS/emulator.pid"
    pid="$(cat "$ARTIFACTS/emulator.pid")"

    "$ADB" start-server >/dev/null
    # The gate: sys.boot_completed from THIS boot (see above), polled rather
    # than slept for, with the emulator's own death noticed rather than
    # waited on. 20 minutes is the action's default boot timeout.
    deadline=$(( $(date +%s) + 1200 ))
    while :; do
      if ! kill -0 "$pid" 2>/dev/null; then
        echo "ERROR: the emulator exited while booting; its log:"
        tail -n 60 "$ARTIFACTS/emulator.log" || true
        exit 1
      fi
      booted="$("$ADB" -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
      [ "$booted" = "1" ] && break
      if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "ERROR: $SERIAL did not report sys.boot_completed within 1200s; its log:"
        tail -n 60 "$ARTIFACTS/emulator.log" || true
        exit 1
      fi
      sleep 5
    done
    echo "$SERIAL booted"

    # What the action did after the gate: unlock, and turn animations off so
    # a screenshot is of a settled screen.
    "$ADB" -s "$SERIAL" shell input keyevent 82 || true
    for setting in window_animation_scale transition_animation_scale animator_duration_scale; do
      "$ADB" -s "$SERIAL" shell settings put global "$setting" 0 || true
    done
    "$ADB" -s "$SERIAL" shell getprop ro.build.version.sdk | tr -d '\r' | sed 's/^/API level on the device: /'
    ;;

  stop)
    # Best effort, in the job's after_script: the runner would kill it anyway,
    # but a clean `emu kill` lets the log end rather than stop.
    if [ -f "$ARTIFACTS/emulator.pid" ]; then
      "$ADB" -s "$SERIAL" emu kill >/dev/null 2>&1 || true
      pid="$(cat "$ARTIFACTS/emulator.pid")"
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    "$ADB" kill-server >/dev/null 2>&1 || true
    ;;

  *)
    echo "usage: $0 boot|stop" >&2
    exit 2
    ;;
esac
