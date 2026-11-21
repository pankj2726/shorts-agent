# Publishing approval

Nothing publishes without a valid approval record. This is structural, not a
configuration flag: `upload_short()` verifies an approval row for that video and
that exact file before doing anything else, and reads no environment variable
that could switch the requirement off.

**The default state is that nothing can be published.** No approvers are
configured, so no approval can be granted, so every upload is refused. That is
intentional until a real approval mechanism exists (P4). It does not stop the
pipeline from researching, scripting, rendering or passing QA — only from
publishing.

`python scripts/preflight.py` reports the current state under "Publishing
approval", including which variables are unset.

## Why it is built this way

Approval used to be a branch in the pipeline:

```python
if env_bool("APPROVAL_REQUIRED", True):
    _wait_for_approval(...)
upload_short(...)
```

Setting `APPROVAL_REQUIRED=false` fell straight through to the YouTube upload.
The only thing behind it was `PAUSE_PUBLISHING`, which defaults to `false`. An
environment variable — writable by anyone with repository variable access,
visible to every workflow — was the entire approval mechanism.

Both variables still exist and both are now irrelevant to the decision. They are
defence in depth behind the approval record, never instead of it.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SHORTS_APPROVER_ALLOWLIST` | *(unset)* | Comma-separated identities allowed to approve. Unset means nobody may approve |
| `SHORTS_APPROVAL_TOKEN` | *(unset)* | Shared token that must accompany a grant. Unset means no grant can be made |
| `SHORTS_APPROVAL_TTL_S` | `86400` | Approval lifetime in seconds. Clamped to a minimum of 60 and a maximum of 604800 (7 days) |

Both `SHORTS_APPROVER_ALLOWLIST` and `SHORTS_APPROVAL_TOKEN` are required.
Neither is sufficient alone, so a leaked token without an allowlist entry
grants nothing, and an allowlist without a token grants nothing.

Set them in GitHub under **Settings → Secrets and variables → Actions**: the
token as a **Secret**, the allowlist as a **Variable**.

The TTL clamp is deliberate. A very large value would turn a short-lived
authorisation for one render into a standing licence to publish, which is the
thing this design is meant to prevent.

## Granting an approval

```bash
export SHORTS_APPROVAL_TOKEN='...'          # never pass on the command line
python scripts/approve_video.py --video-id 12 --by operator@example.com
```

The script looks up the video's `render_path`, prints the title, path and
SHA-256, then records the decision. It exits non-zero on refusal and prints the
reason, so it is usable as a workflow step without masking a denial.

The token is read from the environment rather than `argv` on purpose — command
lines land in shell history and process listings. It is never printed.

Related commands:

```bash
python scripts/approve_video.py --video-id 12 --reject --by operator@example.com
python scripts/approve_video.py --video-id 12 --revoke
```

A rejection is recorded but does not authorise anything. `--revoke` withdraws
outstanding approvals and is deliberately **not** gated on the allowlist, so an
operator can always stop a publish even if the token has rotated away from them.

## What an approval requires

Every one of these must hold, and each has its own refusal reason so a refusal
says which condition failed:

| Condition | Refusal |
|---|---|
| A row exists in `approvals` for that `video_id` | `no approval record for this video` |
| The latest decision is `approved` | `latest decision ... is not approved` |
| It has not been revoked | `approval was revoked` |
| It has not expired | `approval has expired` |
| Its own timestamps are consistent | `internally inconsistent` |
| Its SHA-256 matches the file to be published | `does not match the file to be published` |
| The file is readable and hashes | `no content hash supplied` |

Two consequences worth knowing:

- **Content binding.** Approving one render does not authorise a later
  re-render. If the file changes, it must be approved again.
- **Approval precedes the idempotency shortcut.** An already-uploaded video
  whose approval has since lapsed is refused rather than returning its cached
  YouTube ID, because that return would let the caller mark the run `done` and
  report success for a video nobody currently approves.

## The pipeline cannot approve itself

Nothing under `agent/` calls `approvals.grant()`. `scripts/approve_video.py` is
the only entry point, and it runs outside the pipeline. This is the property
that makes the safeguard meaningful — a pipeline that could grant its own
approval would only be one bug away from publishing unattended.

## Storage

One table, `approvals`, in both the SQLite and PostgreSQL dialects:

`id`, `video_id`, `decision`, `approved_by`, `channel`, `content_hash`,
`granted_at`, `expires_at`, `revoked_at`, `created_at`

Created with `CREATE TABLE IF NOT EXISTS` plus one index on `video_id`. The
migration is additive — nothing is dropped or altered — so it is reversible by
dropping the table.

Rows are append-only. Superseding a decision inserts a new row rather than
updating the old one, so the history of who approved what, and when, survives.

## What this is not

This is a fail-closed stopgap, not the approval system. There is still no
authenticated web or Android approval surface, no notification channel that
works (Telegram delivery is permanently disabled), and no approval expiry
reminder. The `channel` column records how a decision arrived and currently only
ever holds `cli`; P4 is expected to populate it with real channels.

Until P4 ships, the correct posture is that the pipeline runs and publishing
stays shut.
