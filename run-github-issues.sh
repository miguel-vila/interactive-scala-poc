#!/usr/bin/env bash
set -euo pipefail

# Run from the repository whose GitHub issues should be worked on.
repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

for tool in codex gh python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$tool" >&2
    exit 1
  fi
done

scratch=$(mktemp -d "${TMPDIR:-/tmp}/codex-issues.XXXXXX")
trap 'rm -rf "$scratch"' EXIT

cat > "$scratch/result.schema.json" <<'JSON'
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string",
      "enum": ["completed", "needs_decision", "no_issue", "blocked"]
    },
    "issue_number": {"type": ["integer", "null"]},
    "summary": {"type": "string"}
  },
  "required": ["status", "issue_number", "summary"],
  "additionalProperties": false
}
JSON

prompt=$(cat <<'PROMPT'
from github, choose the issue with the lowest number with no dependencies and implement it / fix it. When done, commit your changes and mark the issue as done. If it requires some decision from me, stop

Work on exactly one issue in this run. An issue is eligible only if it is open and has no unresolved dependencies. Close the GitHub issue after committing the fix; do not claim completion until both actions succeed. Do not include unrelated pre-existing changes in the commit.

Return a JSON object with status, issue_number, and summary:
- completed: the implementation is committed and the issue is closed.
- needs_decision: you need a decision from me; stop work and state the specific decision in summary.
- no_issue: no eligible open issue exists.
- blocked: you cannot finish for another reason; explain it in summary.
Do not guess when a decision is needed. Stop as soon as you know you need one.
PROMPT
)

for ((iteration = 1; iteration <= 10; iteration++)); do
  result_file="$scratch/result-$iteration.json"
  head_before=$(git rev-parse HEAD)
  printf '\n=== Codex issue run %d/10 ===\n' "$iteration"

  if ! codex exec \
    --cd "$repo_root" \
    --dangerously-bypass-approvals-and-sandbox \
    --output-schema "$scratch/result.schema.json" \
    --output-last-message "$result_file" \
    "$prompt"; then
    printf 'Codex failed on run %d; stopping.\n' "$iteration" >&2
    exit 1
  fi

  if ! result=$(python3 - "$result_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as result_file:
    result = json.load(result_file)
status = result.get("status")
if status not in {"completed", "needs_decision", "no_issue", "blocked"}:
    raise SystemExit("Invalid or missing Codex status")
issue_number = result.get("issue_number")
if status == "completed" and (
    isinstance(issue_number, bool)
    or not isinstance(issue_number, int)
    or issue_number < 1
):
    raise SystemExit("Completed result has no valid issue number")
print(f"{status}:{issue_number if status == 'completed' else ''}")
PY
  ); then
    printf 'Codex returned an invalid result on run %d; stopping.\n' "$iteration" >&2
    exit 1
  fi

  status=${result%%:*}
  issue_number=${result#*:}
  case "$status" in
    completed)
      if [[ $(git rev-parse HEAD) == "$head_before" ]]; then
        printf 'Codex reported completion without a new commit; stopping.\n' >&2
        exit 1
      fi
      if [[ $(gh issue view "$issue_number" --json state --jq '.state') != CLOSED ]]; then
        printf 'Issue #%s is not confirmed closed; stopping.\n' "$issue_number" >&2
        exit 1
      fi
      printf 'Issue #%s committed and closed; continuing.\n' "$issue_number"
      ;;
    needs_decision)
      printf 'Your decision is needed. Stopping after run %d.\n' "$iteration"
      cat "$result_file"
      printf '\n'
      exit 2
      ;;
    no_issue)
      printf 'No eligible open issue remains.\n'
      exit 0
      ;;
    blocked)
      printf 'Codex could not finish the issue. Stopping after run %d.\n' "$iteration" >&2
      cat "$result_file" >&2
      printf '\n' >&2
      exit 3
      ;;
  esac
done

printf 'Reached the 10-run limit.\n'
