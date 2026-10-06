#!/usr/bin/env bash
# PostToolUse/Write|Edit: syntax-check + ruff-lint edited Python files; exit 2 feeds the error back to Claude.
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // ""')
case "$f" in *.py) ;; *) exit 0 ;; esac
[ -f "$f" ] || exit 0
if ! out=$(python3 -m py_compile "$f" 2>&1); then
  echo "Syntax error in $f:" >&2; echo "$out" >&2; exit 2
fi
if command -v ruff >/dev/null && ! out=$(ruff check --no-cache --quiet --output-format concise "$f" 2>&1); then
  echo "ruff found issues in $f:" >&2; echo "$out" >&2; exit 2
fi
exit 0
