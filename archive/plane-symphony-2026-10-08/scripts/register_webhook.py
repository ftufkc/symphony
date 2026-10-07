"""Register the workspace webhook pointing at this service's receiver.

Plane sends webhooks from inside Docker, so the URL uses host.docker.internal.
Prints the generated secret_key — put it in .env as WEBHOOK_SECRET.

Usage: python scripts/register_webhook.py
"""

import json

from plane_symphony.config import load_env_file, load_settings
from plane_symphony.plane_client import PlaneClient


def main() -> None:
    env = load_env_file(".env")
    settings = load_settings(env.get("WORKFLOW_PATH", "WORKFLOW.md"), env=env)
    client = PlaneClient(settings.plane_base_url, settings.plane_token, settings.workspace_slug)
    url = f"http://host.docker.internal:{settings.webhook_port}/webhook"
    body = {"url": url, "issue": True, "issue_comment": True}
    resp = client.raw_request("POST", f"workspaces/{settings.workspace_slug}/webhooks/", body=body)
    print(json.dumps(resp, indent=2, ensure_ascii=False))
    if isinstance(resp, dict) and resp.get("secret_key"):
        print("\n>>> put this in .env:  WEBHOOK_SECRET=" + resp["secret_key"])


if __name__ == "__main__":
    main()
