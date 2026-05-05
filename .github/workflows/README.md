# GitHub Workflows — Artifact Zero

This folder defines what GitHub Actions does on each branch event. These files are active code, not documentation. Editing them changes how deploys behave.

---

## The five workflows

| File | Trigger | What it does |
|---|---|---|
| `test.yml` | push to `dev/*`, PR to `quality` or `main` | Runs pytest. Gate check. |
| `pre-deploy-test.yml` | PR to `main` | Heavy gate before prod: syntax checks, template/static existence, Docker build, container health check. |
| `deploy-dev.yml` | push to `develop` | Builds dev image, deploys to `artifact-zero-dev` service. |
| `deploy-quality.yml` | push to `quality` | Builds quality image, deploys to `artifact-zero-quality` service. |
| `deploy.yml` | push to `main` | Builds prod image, deploys to `artifact-zero-service`. Has SSM injection, auto-rollback, SES notifications. |

---

## Promotion flow

```
feature branch (dev/*)  ─PR──►  develop  ─PR──►  quality  ─PR──►  main
                                   │                │              │
                                   ▼                ▼              ▼
                             deploy-dev      deploy-quality    deploy
                                   │                │              │
                                   ▼                ▼              ▼
                             dev.artifact0  quality.artifact0  artifact0.com
```

PRs use **Rebase and merge** (set in repo settings) so commit hashes carry through cleanly across branches. Do not use merge commits or squash — they create branch drift that makes "X behind / Y ahead" counts misleading over time.

---

## ECS task definition architecture

This section describes a deliberate decision that has confused AI threads in the past. Read before changing anything.

### What's deployed

| Service | Task definition | Purpose |
|---|---|---|
| `artifact-zero-service` | `artifact-zero-task:N` | Production |
| `artifact-zero-quality` | `artifact-zero-task:M` | Quality / staging |
| `artifact-zero-dev` | `artifact-zero-dev-task:K` | Development |

**Quality and main share the `artifact-zero-task` family on purpose.** They run different revisions in that family — quality on one revision, main on another. The image tag on each revision is different (`quality-<sha>` vs `<sha>`).

### Why the shared family

Quality must be as close to main as possible. Same family means same task definition shape — same CPU, memory, env var names, secret references, port mappings. The only difference is the image revision. That makes a quality deploy a true dress rehearsal for main.

If quality used a separate task def family, drift would creep in. An env var added to quality but not main, a memory bump to main not back-ported to quality. The differences are exactly where bugs hide. Shared family eliminates that class of bug.

### The orphan `artifact-zero-quality-task` family

This task def family exists but is not used by any service. It is leftover from an earlier setup attempt before the shared-family pattern was adopted. It will be deleted in a follow-up hotfix once p0067 is validated in production. Until then, it stays for safe-fallback purposes.

**Do not use this family. Do not delete this family yet. Do not "fix" `deploy-quality.yml` to point at it.**

### Why dev does not share

`artifact-zero-dev-task` is a separate family. Dev does not need to mirror main exactly — its purpose is fast iteration, not dress rehearsal. Different env var set (fewer vars, no Stripe/OpenAI/etc.), and that's intentional. Leave dev alone.

---

## Decisions and AI hazards

These are corrections to advice that AI threads have given in the past. Future assistants should read this section before suggesting changes.

### "Quality should have its own task def family for isolation"

No. See above. Shared family is the dress-rehearsal pattern. Separate families introduce drift.

### "Quality should have its own RDS instance"

Worth doing eventually, but not part of workflow file changes. Currently all three environments share one RDS. A migration plan for splitting is outside the scope of this folder. Do not propose this as part of a workflow PR.

### "The `artifact-zero-quality-task` family should be deleted immediately"

Not until p0067 validates in production. The orphan family is harmless and removing it before the new workflow proves out adds risk for zero benefit. Hotfix after validation will delete it.

### "Combine all deploy workflows into one parameterized workflow"

No. The three deploy workflows differ in safety properties (only main has SSM injection and auto-rollback in some cases; only quality and main get SES notifications; dev runs a different smoke test). Parameterizing creates conditional logic that is harder to read and easier to break. Three explicit files is the pattern.

### "Add `quality` back to `test.yml`'s push trigger"

No. `deploy-quality.yml` already runs pytest before deploying. Adding it to `test.yml`'s push trigger duplicates the work. The gate is `pull_request` to quality (in `test.yml`) plus the test job inside `deploy-quality.yml` post-merge.

---

## Required GitHub secrets

| Secret | Used by | Purpose |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | all deploy-* | AWS auth |
| `AWS_SECRET_ACCESS_KEY` | all deploy-* | AWS auth |
| `NOTIFY_EMAIL_TO` | deploy.yml, deploy-quality.yml | SES recipient |

DATABASE_URL is not a GitHub secret. It is in AWS SSM Parameter Store at `/artifact-zero/DATABASE_URL` and injected at deploy time by `deploy.yml` and `deploy-quality.yml`.

---

## What does NOT belong in this folder

- Credentials, secrets, tokens, keys, passwords
- Task definition JSON files (containing real env vars)
- `.env` files
- Anything matching the gate's CREDENTIAL_PATTERNS (see `az_gate.py`)

The gate will reject any push containing these.

---

## Changes go through the gate

All changes to files in this folder go through the standard push process: bundle, gate, bat, PR to develop. No direct edits to the repo. No bypassing the gate. See `HOTFIX_PROTOCOL.md` and the AZ Bundle Spec for details.

---

## History

- **p0067** (this push) — Aligned `deploy-quality.yml` with the shared task def family. Added health check, auto-rollback (filtered to quality-tagged revisions), SES notifications, SSM injection. Removed duplicate `quality` push trigger from `test.yml`. Created this README.
