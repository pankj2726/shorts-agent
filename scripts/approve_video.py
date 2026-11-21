#!/usr/bin/env python3
"""
approve_video.py — operator entry point for granting or withdrawing approval.

Deliberately lives outside the pipeline. The pipeline cannot approve its own
output: nothing under agent/ calls approvals.grant(). Granting requires an
identity on SHORTS_APPROVER_ALLOWLIST and the matching SHORTS_APPROVAL_TOKEN,
both of which are unset by default, so out of the box no approval can be
created and therefore nothing can publish.

    python scripts/approve_video.py --video-id 12 --by operator@example.com
    python scripts/approve_video.py --video-id 12 --reject --by operator@example.com
    python scripts/approve_video.py --video-id 12 --revoke

Exits non-zero when approval is refused, so this is usable as a step in a
workflow without masking a denial.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.modules import approvals, db


def _render_path(video_id: int) -> tuple[str | None, str]:
    """Look up the rendered file for a video, and its title for the prompt."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT render_path, title FROM videos WHERE id=?", (video_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None, f"video_id={video_id} does not exist in the state store"
    path = row["render_path"]
    if not path:
        return None, f"video_id={video_id} has no render_path recorded"
    return str(path), str(row["title"] or "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video-id", type=int, required=True)
    ap.add_argument("--by", default="", help="approver identity; must be on the allowlist")
    ap.add_argument("--channel", default="cli")
    ap.add_argument("--reject", action="store_true", help="record a rejection instead")
    ap.add_argument("--revoke", action="store_true", help="withdraw outstanding approvals")
    args = ap.parse_args()

    if args.revoke:
        n = approvals.revoke(args.video_id, by=args.by)
        print(f"revoked {n} approval(s) for video_id={args.video_id}")
        return 0

    if not args.by:
        print("error: --by is required (an identity from SHORTS_APPROVER_ALLOWLIST)",
              file=sys.stderr)
        return 2

    path, title = _render_path(args.video_id)
    if path is None:
        print(f"error: {title}", file=sys.stderr)
        return 2

    # Read the token from the environment rather than argv: a command line
    # lands in shell history and process listings.
    token = os.environ.get("SHORTS_APPROVAL_TOKEN", "")

    print(f"video_id : {args.video_id}")
    print(f"title    : {title}")
    print(f"file     : {path}")

    digest = approvals.file_hash(path)
    if not digest:
        print(f"error: cannot read {path}; refusing to approve an unreadable file",
              file=sys.stderr)
        return 2
    print(f"sha256   : {digest}")

    decision = approvals.REJECTED if args.reject else approvals.APPROVED
    ok, reason = approvals.grant(
        video_id=args.video_id,
        approved_by=args.by,
        token=token,
        content_hash=digest,
        channel=args.channel,
        decision=decision,
    )

    if not ok:
        print(f"refused: {reason}", file=sys.stderr)
        return 1

    print(f"{decision} recorded for video_id={args.video_id} — {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
