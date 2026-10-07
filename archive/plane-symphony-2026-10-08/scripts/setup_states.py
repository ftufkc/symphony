"""Ensure the canonical AI workflow states exist in each project.

Idempotent: renames a case-variant match to the canonical name (e.g.
"AI TODO" -> "AI Todo"), creates missing states, leaves others alone.

Usage:
  python scripts/setup_states.py            # all projects
  python scripts/setup_states.py NEOCSSSTUD # one project (id or identifier)
"""

import sys

from plane_symphony.config import load_env_file, load_settings
from plane_symphony.plane_client import PlaneClient

DESIRED = [
    ("AI Todo", "unstarted", "#a855f7"),
    ("AI Doing", "started", "#3b82f6"),
    ("Human Review", "started", "#f59e0b"),
    ("AI Done", "completed", "#22c55e"),
]


def ensure_states(client: PlaneClient, workspace: str, project_id: str) -> None:
    client.invalidate_states(project_id)
    existing = client.list_states(project_id, use_cache=False)
    by_lower = {s.name.strip().lower(): s for s in existing}

    # detect case-insensitive duplicates
    lower_counts = {}
    for s in existing:
        k = s.name.strip().lower()
        lower_counts[k] = lower_counts.get(k, 0) + 1
    dupes = [k for k, cnt in lower_counts.items() if cnt > 1]
    if dupes:
        raise RuntimeError(
            f"Project has duplicate states (case-insensitive): {dupes}. "
            f"Please delete duplicates manually before running setup_states."
        )

    base = f"workspaces/{workspace}/projects/{project_id}/states"
    for name, group, color in DESIRED:
        match = by_lower.get(name.lower())
        if match is None:
            client.raw_request("POST", f"{base}/", body={"name": name, "group": group, "color": color})
            print(f"  + created {name} ({group})")
        elif match.name != name:
            client.raw_request("PATCH", f"{base}/{match.id}/", body={"name": name})
            print(f"  ~ renamed {match.name!r} -> {name!r}")
        else:
            print(f"  = ok {name}")


def main() -> None:
    env = load_env_file(".env")
    settings = load_settings(env.get("WORKFLOW_PATH", "WORKFLOW.md"), env=env)
    client = PlaneClient(settings.plane_base_url, settings.plane_token, settings.workspace_slug)
    target = sys.argv[1] if len(sys.argv) > 1 else None
    for p in client.list_projects():
        if target and target not in (p.id, p.identifier):
            continue
        print(f"project {p.identifier} ({p.id}):")
        ensure_states(client, settings.workspace_slug, p.id)


if __name__ == "__main__":
    main()
