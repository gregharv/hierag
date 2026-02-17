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
class UrlAction:
    kind: str  # "delete" | "update"
    row_id: int
    site_id: int
    old_url: str
    new_url: str | None = None


def _choose_keeper(rows: list[dict], canonical: str) -> dict:
    # Prefer rows already at canonical URL, then those with a non-empty kind,
    # then oldest discovered row, then lowest id.
    return sorted(
        rows,
        key=lambda row: (
            row.get("url") != canonical,
            not bool(row.get("kind")),
            row.get("discovered_at") or "",
            row["id"],
        ),
    )[0]


def plan_cleanup(db, site_id: int | None = None) -> tuple[list[UrlAction], dict[str, int]]:
    if site_id is None:
        rows = list(db.t.discovered_urls())
    else:
        rows = list(db.t.discovered_urls.rows_where("site_id=?", [site_id]))

    roots = {int(row["id"]): str(row.get("root_url") or "") for row in db.t.sites()}
    groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    non_target_rows = 0
    for row in rows:
        sid = int(row["site_id"])
        root_url = roots.get(sid, "") or row["url"]
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
            groups[(sid, "__DROP__")].append(row)
            continue
        groups[(sid, canonical)].append(row)

    actions: list[UrlAction] = []
    keepers: dict[int, str] = {}
    duplicate_rows = 0
    canonical_changes = 0

    for (sid, canonical), group in groups.items():
        if canonical == "__DROP__":
            for row in group:
                duplicate_rows += 1
                actions.append(
                    UrlAction(
                        kind="delete",
                        row_id=int(row["id"]),
                        site_id=sid,
                        old_url=row["url"],
                    )
                )
            continue

        keeper = _choose_keeper(group, canonical)
        keepers[int(keeper["id"])] = canonical
        if keeper.get("url") != canonical:
            canonical_changes += 1
        for row in group:
            if int(row["id"]) == int(keeper["id"]):
                continue
            duplicate_rows += 1
            actions.append(
                UrlAction(
                    kind="delete",
                    row_id=int(row["id"]),
                    site_id=sid,
                    old_url=row["url"],
                )
            )

    delete_ids = {action.row_id for action in actions if action.kind == "delete"}
    surviving_rows = [row for row in rows if int(row["id"]) not in delete_ids]
    surviving_by_url = {row["url"]: int(row["id"]) for row in surviving_rows}

    for row in surviving_rows:
        row_id = int(row["id"])
        canonical = keepers.get(row_id)
        if not canonical or row["url"] == canonical:
            continue
        holder_id = surviving_by_url.get(canonical)
        if holder_id is not None and holder_id != row_id:
            # Should be rare; prefer deleting the row to preserve unique URL constraint.
            actions.append(
                UrlAction(
                    kind="delete",
                    row_id=row_id,
                    site_id=int(row["site_id"]),
                    old_url=row["url"],
                )
            )
            continue
        actions.append(
            UrlAction(
                kind="update",
                row_id=row_id,
                site_id=int(row["site_id"]),
                old_url=row["url"],
                new_url=canonical,
            )
        )
        surviving_by_url.pop(row["url"], None)
        surviving_by_url[canonical] = row_id

    stats = {
        "rows_scanned": len(rows),
        "groups": len(groups),
        "non_target_rows": non_target_rows,
        "duplicate_rows": duplicate_rows,
        "canonical_changes": canonical_changes,
        "planned_deletes": sum(1 for action in actions if action.kind == "delete"),
        "planned_updates": sum(1 for action in actions if action.kind == "update"),
    }
    return actions, stats


def apply_actions(db, actions: list[UrlAction]) -> dict[str, int]:
    deleted = 0
    updated = 0
    skipped = 0

    for action in actions:
        if action.kind != "delete":
            continue
        row = db.t.discovered_urls[action.row_id]
        if not row:
            skipped += 1
            continue
        db.t.discovered_urls.delete(action.row_id)
        deleted += 1

    for action in actions:
        if action.kind != "update":
            continue
        row = db.t.discovered_urls[action.row_id]
        if not row:
            skipped += 1
            continue
        existing = list(db.t.discovered_urls.rows_where("url=?", [action.new_url], limit=1))
        if existing and int(existing[0]["id"]) != action.row_id:
            skipped += 1
            continue
        db.t.discovered_urls.update({"id": action.row_id, "url": action.new_url})
        updated += 1

    return {"deleted": deleted, "updated": updated, "skipped": skipped}


def run_cleanup(
    *,
    db_path: str | None = None,
    site_id: int | None = None,
    apply: bool = False,
    sample_limit: int = 20,
) -> dict[str, object]:
    db = bootstrap_scraper_db(db_path, seed=False)
    actions, stats = plan_cleanup(db, site_id=site_id)

    print("Discovered URL cleanup plan:")
    for key, value in stats.items():
        print(f"- {key}: {value}")

    if sample_limit > 0:
        print("\nSample planned actions:")
        for action in actions[:sample_limit]:
            if action.kind == "delete":
                print(f"- delete id={action.row_id} site_id={action.site_id} url={action.old_url}")
            else:
                print(
                    f"- update id={action.row_id} site_id={action.site_id} "
                    f"{action.old_url} -> {action.new_url}"
                )
        if len(actions) > sample_limit:
            print(f"- ... and {len(actions) - sample_limit} more")

    applied = {"deleted": 0, "updated": 0, "skipped": 0}
    if apply and actions:
        applied = apply_actions(db, actions)
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
        description="One-time dedupe/canonicalization for discovered_urls in scraper.db"
    )
    parser.add_argument("--db-path", type=str, default=None)
    parser.add_argument("--site-id", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=20)
    return parser


# %%
if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    result = run_cleanup(
        db_path=args.db_path,
        site_id=args.site_id,
        apply=args.apply,
        sample_limit=args.sample_limit,
    )
    assert "stats" in result and "planned_actions" in result
    print("Check Passed")
