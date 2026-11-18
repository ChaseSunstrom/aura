#!/usr/bin/env bash
# The shell every GitHub workflow's run steps go through (defaults.run.shell: bash scripts/ci/gh-step.sh {0}).
#
# It runs the step the way GitHub's own bash does (--noprofile --norc -eo pipefail), streaming
# its output as usual. When the step fails, the last 40 lines of its output also become one error
# annotation. A public repo's job logs need a signed-in reader, but its annotations are readable
# through the API without a token. Six jobs stayed red for ten runs with only "Process completed
# with exit code 2" to go on (T-1554).
#
# Does not cover: a step killed by the job's timeout (nothing runs after it), or a cause more than
# 40 lines or 3900 characters from the end once stack frames are dropped (the full log is still in
# the job).
set -u
step=$1
log=$(mktemp "${RUNNER_TEMP:-/tmp}/gh-step.XXXXXX")
bash --noprofile --norc -eo pipefail "$step" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
  # GitHub keeps an annotation's first 4096 characters, so the end (where a build says why) must
  # fit: stack frames go first (Gradle's unit job annotated forty of them and then "BU"), then the
  # last 40 lines, then at most their last 3900 characters.
  tail=$(grep -vE '^[[:space:]]+(at |\.\.\. [0-9]+ more)' "$log" | tail -n 40)
  if [ "${#tail}" -gt 3900 ]; then
    tail=${tail: -3900}
  fi
  # GitHub's workflow-command escaping for a message: % first, then the line breaks.
  tail=${tail//'%'/'%25'}
  tail=${tail//$'\r'/'%0D'}
  tail=${tail//$'\n'/'%0A'}
  printf '::error title=step failed (exit %s)::%s\n' "$rc" "$tail"
fi
rm -f "$log"
exit "$rc"
