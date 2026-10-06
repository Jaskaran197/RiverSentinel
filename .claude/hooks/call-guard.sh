#!/usr/bin/env bash
# PreToolUse/Bash: require user approval before anything that can place a real phone call.
cmd=$(jq -r '.tool_input.command // ""')
reason=""
n_all=$(grep -Eo 'page\.py' <<<"$cmd" | wc -l)
n_setup=$(grep -Eo 'page\.py[[:space:]]+--setup([[:space:]]*($|[;&|]))' <<<"$cmd" | wc -l)
if [ "$n_all" -gt "$n_setup" ]; then
  reason="page.py can place a real phone call (only --setup is call-free)."
elif grep -Eq 'test_twilio\.py.*--call' <<<"$cmd"; then
  reason="test_twilio.py --call rings a real phone."
elif grep -Eq '(from[[:space:]]+page[[:space:]]+import|import[[:space:]]+page\b)' <<<"$cmd"; then
  reason="Imports the pager module, which can place a real phone call."
fi
if [ -n "$reason" ]; then
  jq -n --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}'
fi
exit 0
