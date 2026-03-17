# VERIFY — p0043

## Files changed
- app.py — operator_bp registered after gateway_bp
- operator_room.py — NEW: Flask blueprint, Claude API proxy, session storage
- templates/operator.html — NEW: Operator Room UI
- templates/gateway.html — v2Rewrite() fixed: hedge words stripped as qualifiers only, content words preserved

## What the operator room does
- GET  /operator           — full workspace UI, admin only
- POST /operator/api/chat  — Claude API proxy with operator S0 context injected
- GET  /operator/sessions  — session history from RDS

## What the v2Rewrite fix does
- BEFORE: stripped hedge words globally using \bword\b\s* — ate surrounding nouns/verbs
- AFTER:  strips hedge words only at sentence boundaries or immediately before known qualifier targets
- Preserves all nouns, verbs, content words
- Hedge substitutions (might→will etc) use non-word-char boundary to avoid partial matches

## Environment variables needed for operator room (add to ECS task definition)
- ANTHROPIC_API_KEY  — Claude API key → Secrets Manager: az/anthropic-api-key
- OPERATOR_API_KEY   — NTI enterprise key, no rate limits
- OPERATOR_TOKEN     — Admin access token (default: aztempfix2026, rotate in prod)

## Verification steps
1. GET /operator returns 200
2. Log in as admin → operator room loads with 3-column layout
3. Type a message → Claude responds → NII score appears on right panel
4. In gateway: send "what is the likelihood this works" (hedge: likelihood)
5. V2 Rewrite fires → "what is the likelihood" → "what is the probability" or stripped
6. Content words in message preserved — no nouns/verbs eaten
7. Non-hedge gates still show GATED message unchanged

## PR target
develop (never main)

## Closure
Jame closes push
