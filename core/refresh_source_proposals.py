from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from . import service, source_proposals
except ImportError:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core import service, source_proposals  # type: ignore[no-redef]

ACTIVE_STATUSES = {"draft", "refresh_queued", "ready_for_review", "failed"}


def refresh_active_source_proposals(
    *,
    queued_only: bool = False,
    limit: int | None = None,
    site_id: int = 2,
    max_pages: int = 3000,
    crawl_delay: float = 0.1,
    fetch_delay: float = 0.0,
    batch_size: int = 64,
    prune_missing: bool = True,
    prune_missing_after: int = 1,
) -> list[dict]:
    service.create_db_and_tables()
    proposals = source_proposals.list_source_proposals(limit=1000)
    if queued_only:
        proposals = [item for item in proposals if item.get("status") == "refresh_queued"]
    else:
        proposals = [item for item in proposals if str(item.get("status") or "") in ACTIVE_STATUSES]
    if limit is not None and limit >= 0:
        proposals = proposals[:limit]

    results: list[dict] = []
    for proposal in proposals:
        proposal_id = int(proposal["id"])
        print(f"Refreshing source proposal id={proposal_id} name={proposal.get('name')} status={proposal.get('status')}", flush=True)
        try:
            summary = source_proposals.refresh_source_proposal(
                proposal_id,
                site_id=site_id,
                max_pages=max_pages,
                crawl_delay=crawl_delay,
                fetch_delay=fetch_delay,
                batch_size=batch_size,
                prune_missing=prune_missing,
                prune_missing_after=prune_missing_after,
            )
            results.append({"proposal_id": proposal_id, "ok": True, "summary": summary})
        except Exception as exc:
            print(f"Source proposal refresh failed id={proposal_id}: {exc}", flush=True)
            results.append({"proposal_id": proposal_id, "ok": False, "error": str(exc)})
    print(f"Source proposal refresh complete count={len(results)}", flush=True)
    return results


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh source proposal sandbox databases")
    parser.add_argument("--queued-only", action="store_true", help="Refresh only proposals explicitly queued from the admin UI")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--site-id", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=3000)
    parser.add_argument("--crawl-delay", type=float, default=0.1)
    parser.add_argument("--fetch-delay", type=float, default=0.0)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--no-prune-missing", action="store_true")
    parser.add_argument("--prune-missing-after", type=int, default=1)
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    refresh_active_source_proposals(
        queued_only=args.queued_only,
        limit=args.limit,
        site_id=args.site_id,
        max_pages=args.max_pages,
        crawl_delay=args.crawl_delay,
        fetch_delay=args.fetch_delay,
        batch_size=args.batch_size,
        prune_missing=not args.no_prune_missing,
        prune_missing_after=args.prune_missing_after,
    )
