from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

try:
    from .crawl import canonicalize_internal_url
    from .fastlite_db import bootstrap_scraper_db
    from .site_config import get_crawl_url_policy
except ImportError:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.crawl import canonicalize_internal_url
    from core.fastlite_db import bootstrap_scraper_db
    from core.site_config import get_crawl_url_policy


@dataclass
class PageAction:
    kind: str  # "delete" | "update"
    page_id: int
    site_id: int
    old_url: str
    new_url: str | None = None


def _choose_keeper(rows: list[dict], canonical: str) -> dict:
    # Prefer already-canonical URLs, then freshest scrape/change time.
    return max(
        rows,
        key=lambda row: (
            row.get("url") == canonical,
            bool(row.get("last_scraped")),
            row.get("last_scraped") or "",
            bool(row.get("last_changed")),
            row.get("last_changed") or "",
            -int(row["id"]),
        ),
    )


def plan_pages_cleanup(
    db,
    *,
    site_id: int | None = None,
    drop_non_target: bool = False,
) -> tuple[list[PageAction], dict[str, int]]:
    if site_id is None:
        rows = list(db.t.pages())
    else:
        rows = list(db.t.pages.rows_where("site_id=?", [site_id]))

    roots = {int(row["id"]): str(row.get("root_url") or "") for row in db.t.sites()}
    groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    non_target_rows = 0
    for row in rows:
        sid = int(row["site_id"])
        root_url = roots.get(sid) or row["url"]
        policy = get_crawl_url_policy(sid)
        canonical = canonicalize_internal_url(
            row["url"],
            row["url"],
            root_url,
            policy=policy,
            allow_root=bool(policy.get("allow_root_seed", False)),
        )
        if not canonical:
            non_target_rows += 1
            if drop_non_target:
                groups[(sid, "__DROP__")].append(row)
            else:
                groups[(sid, f"__RAW__:{row['url']}")].append(row)
            continue
        groups[(sid, canonical)].append(row)

    actions: list[PageAction] = []
    duplicate_pages = 0
    canonical_changes = 0

    for (sid, canonical), group in groups.items():
        if canonical == "__DROP__":
            for row in group:
                actions.append(
                    PageAction(
                        kind="delete",
                        page_id=int(row["id"]),
                        site_id=sid,
                        old_url=row["url"],
                    )
                )
            duplicate_pages += len(group)
            continue

        if canonical.startswith("__RAW__:"):
            continue

        keeper = _choose_keeper(group, canonical)
        if keeper.get("url") != canonical:
            canonical_changes += 1
            actions.append(
                PageAction(
                    kind="update",
                    page_id=int(keeper["id"]),
                    site_id=sid,
                    old_url=keeper["url"],
                    new_url=canonical,
                )
            )
        for row in group:
            if int(row["id"]) == int(keeper["id"]):
                continue
            duplicate_pages += 1
            actions.append(
                PageAction(
                    kind="delete",
                    page_id=int(row["id"]),
                    site_id=sid,
                    old_url=row["url"],
                )
            )

    stats = {
        "rows_scanned": len(rows),
        "groups": len(groups),
        "non_target_rows": non_target_rows,
        "duplicate_pages": duplicate_pages,
        "canonical_changes": canonical_changes,
        "planned_deletes": sum(1 for action in actions if action.kind == "delete"),
        "planned_updates": sum(1 for action in actions if action.kind == "update"),
    }
    return actions, stats


def _delete_page_tree(db, page_id: int) -> dict[str, int]:
    counts = {"pages": 0, "extracts": 0, "chunks": 0, "embeddings": 0}
    extracts = list(db.t.extracts.rows_where("page_id=?", [page_id]))
    for extract in extracts:
        chunks = list(db.t.chunks.rows_where("extract_id=?", [extract["id"]]))
        for chunk in chunks:
            embeddings = list(db.t.embeddings.rows_where("chunk_id=?", [chunk["id"]]))
            for embedding in embeddings:
                db.t.embeddings.delete(embedding["id"])
                counts["embeddings"] += 1
            db.t.chunks.delete(chunk["id"])
            counts["chunks"] += 1
        db.t.extracts.delete(extract["id"])
        counts["extracts"] += 1
    db.t.pages.delete(page_id)
    counts["pages"] += 1
    return counts


def apply_pages_actions(db, actions: list[PageAction]) -> dict[str, int]:
    deleted = 0
    updated = 0
    skipped = 0
    cascade = {"pages": 0, "extracts": 0, "chunks": 0, "embeddings": 0}

    for action in actions:
        if action.kind != "delete":
            continue
        row = db.t.pages[action.page_id]
        if not row:
            skipped += 1
            continue
        counts = _delete_page_tree(db, action.page_id)
        for key in cascade:
            cascade[key] += counts[key]
        deleted += 1

    for action in actions:
        if action.kind != "update":
            continue
        row = db.t.pages[action.page_id]
        if not row:
            skipped += 1
            continue
        existing = list(db.t.pages.rows_where("url=?", [action.new_url], limit=1))
        if existing and int(existing[0]["id"]) != action.page_id:
            skipped += 1
            continue
        db.t.pages.update({"id": action.page_id, "url": action.new_url})
        updated += 1

    return {
        "deleted": deleted,
        "updated": updated,
        "skipped": skipped,
        **{f"cascade_{k}": v for k, v in cascade.items()},
    }


def run_cleanup(
    *,
    db_path: str | None = None,
    site_id: int | None = None,
    drop_non_target: bool = False,
    apply: bool = False,
    sample_limit: int = 20,
) -> dict[str, object]:
    db = bootstrap_scraper_db(db_path, seed=False)
    actions, stats = plan_pages_cleanup(
        db,
        site_id=site_id,
        drop_non_target=drop_non_target,
    )

    print("Pages URL cleanup plan:")
    for key, value in stats.items():
        print(f"- {key}: {value}")

    if sample_limit > 0:
        print("\nSample planned actions:")
        for action in actions[:sample_limit]:
            if action.kind == "delete":
                print(f"- delete page_id={action.page_id} site_id={action.site_id} url={action.old_url}")
            else:
                print(
                    f"- update page_id={action.page_id} site_id={action.site_id} "
                    f"{action.old_url} -> {action.new_url}"
                )
        if len(actions) > sample_limit:
            print(f"- ... and {len(actions) - sample_limit} more")

    applied = {
        "deleted": 0,
        "updated": 0,
        "skipped": 0,
        "cascade_pages": 0,
        "cascade_extracts": 0,
        "cascade_chunks": 0,
        "cascade_embeddings": 0,
    }
    if apply and actions:
        applied = apply_pages_actions(db, actions)
        print("\nApplied:")
        for key, value in applied.items():
            print(f"- {key}: {value}")
    elif apply:
        print("\nApplied: no actions needed")
    else:
        print("\nDry run only. Re-run with --apply to execute.")

    return {
        "stats": stats,
        "planned_actions": len(actions),
        "applied": applied,
        "applied_mode": bool(apply),
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-time canonical URL dedupe for pages/extracts/chunks/embeddings"
    )
    parser.add_argument("--db-path", type=str, default=None)
    parser.add_argument("--site-id", type=int, default=None)
    parser.add_argument("--drop-non-target", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=20)
    return parser


# %%
if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    result = run_cleanup(
        db_path=args.db_path,
        site_id=args.site_id,
        drop_non_target=args.drop_non_target,
        apply=args.apply,
        sample_limit=args.sample_limit,
    )
    assert "stats" in result and "planned_actions" in result
    print("Check Passed")
