#!/usr/bin/env bash
# The shell every GitHub workflow's run steps go through (defaults.run.shell: bash scripts/ci/gh-step.sh {0}).
#
# It runs the step the way GitHub's own bash does (--noprofile --norc -eo pipefail), streaming
# its output as usual. When the step fails, the last 40 lines of its output also become one error
# annotation. A public repo's job logs need a signed-in reader, but its annotations are readable
# through the API without a token. Six jobs stayed red for ten runs with only "Process completed
# with exit code 2" to go on (T-1554).
#
# Does not cover: a step killed by the job's timeout (nothing runs after it), or output over 40
# lines from the cause (only the tail is annotated; the full log is still in the job).
set -u
step=$1
log=$(mktemp "${RUNNER_TEMP:-/tmp}/gh-step.XXXXXX")
bash --noprofile --norc -eo pipefail "$step" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
  tail=$(tail -n 40 "$log")
  # GitHub's workflow-command escaping for a message: % first, then the line breaks.
  tail=${tail//'%'/'%25'}
  tail=${tail//$'\r'/'%0D'}
  tail=${tail//$'\n'/'%0A'}
  printf '::error title=step failed (exit %s)::%s\n' "$rc" "$tail"
fi
rm -f "$log"
exit "$rc"
