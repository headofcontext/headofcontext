"""Load the ACME company into Nextcloud: groups, users, folders, documents and shares.

    docker compose up -d nextcloud
    ./scripts/nextcloud-dev-setup.sh          # lifts the 20 shares / 10 min rate limit (dev only)
    uv run python scripts/load_nextcloud.py [--url http://localhost:8090] [--admin admin:admin]

Idempotent: existing users, groups, files and shares are left alone. Shares mirror the fixture
ACLs exactly (folder shares to groups/users for sources, file shares for direct ACLs).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "fixtures" / "acme" / "generated"
PASSWORD = "Acme-fixtures-2026!"  # noqa: S105 — dev fixtures; Nextcloud enforces a password policy
READ = 1
EDIT = 15  # read + update + create + delete
SHARE_USER, SHARE_GROUP = 0, 1


def load(name: str) -> Any:
    return json.loads((GENERATED / name).read_text(encoding="utf-8"))


class Nextcloud:
    def __init__(self, url: str, admin: str, password: str) -> None:
        self.admin = admin
        self.http = httpx.Client(
            base_url=url, auth=(admin, password), headers={"OCS-APIRequest": "true"}, timeout=60
        )

    def ocs(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        params = {**kwargs.pop("params", {}), "format": "json"}
        for attempt in range(8):
            response = self.http.request(method, path, params=params, **kwargs)
            if response.status_code != 429:
                break
            # Nextcloud rate limiting: honour Retry-After, then try again.
            time.sleep(float(response.headers.get("Retry-After", 2 * (attempt + 1))))
        try:
            body = response.json()
            meta = body["ocs"]["meta"]
            return int(meta["statuscode"]), body["ocs"]["data"]
        except (ValueError, KeyError):
            return response.status_code, None

    # -- identities ---------------------------------------------------------------------------

    def ensure_group(self, gid: str) -> None:
        code, _ = self.ocs("POST", "/ocs/v1.php/cloud/groups", data={"groupid": gid})
        if code not in (100, 102):  # 102 = already exists
            raise RuntimeError(f"group {gid}: {code}")

    def ensure_user(self, user: dict[str, Any]) -> None:
        code, _ = self.ocs(
            "POST",
            "/ocs/v1.php/cloud/users",
            data={
                "userid": user["username"],
                "password": PASSWORD,
                "displayName": f"{user['first_name']} {user['last_name']}",
                "email": user["email"],
            },
        )
        if code not in (100, 102):  # 102: already exists
            raise RuntimeError(f"user {user['username']}: OCS status {code}")
        for group in user["groups"]:
            gid = group.split(":", 1)[1]
            code, _ = self.ocs(
                "POST", f"/ocs/v1.php/cloud/users/{user['username']}/groups", data={"groupid": gid}
            )
            if code not in (100, 102):
                raise RuntimeError(f"user {user['username']} -> {gid}: {code}")

    # -- files --------------------------------------------------------------------------------

    def mkdirs(self, path: str) -> None:
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            self.http.request("MKCOL", f"/remote.php/dav/files/{self.admin}/" + "/".join(parts[:i]))

    def put(self, path: str, content: str) -> None:
        self.http.put(f"/remote.php/dav/files/{self.admin}{path}", content=content.encode("utf-8"))

    # -- shares -------------------------------------------------------------------------------

    def existing_shares(self) -> set[tuple[str, int, str]]:
        code, data = self.ocs("GET", "/ocs/v2.php/apps/files_sharing/api/v1/shares")
        if code != 200 or not isinstance(data, list):
            return set()
        return {(s["path"], int(s["share_type"]), s["share_with"]) for s in data}

    def share(
        self,
        path: str,
        share_type: int,
        share_with: str,
        permissions: int,
        existing: set[tuple[str, int, str]],
    ) -> None:
        if (path, share_type, share_with) in existing:
            return
        code, _ = self.ocs(
            "POST",
            "/ocs/v2.php/apps/files_sharing/api/v1/shares",
            data={
                "path": path,
                "shareType": share_type,
                "shareWith": share_with,
                "permissions": permissions,
            },
        )
        if code not in (100, 200):
            raise RuntimeError(f"share {path} -> {share_with}: {code}")
        existing.add((path, share_type, share_with))


def principal(ref: str) -> tuple[int, str]:
    kind, _, name = ref.partition(":")
    return (SHARE_GROUP if kind == "group" else SHARE_USER), name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8090")
    parser.add_argument("--admin", default="admin:admin")
    args = parser.parse_args()
    admin, password = args.admin.split(":", 1)
    nc = Nextcloud(args.url, admin, password)

    users, sources, documents = load("users.json"), load("sources.json"), load("documents.json")
    by_source = {s["id"]: s for s in sources}

    for dept in sorted({g.split(":", 1)[1] for u in users for g in u["groups"]}):
        nc.ensure_group(dept)
    for user in users:
        nc.ensure_user(user)
    print(f"users/groups ok ({len(users)} users)")

    for source in sources:
        nc.mkdirs(source["path"])
    for doc in documents:
        source = by_source.get(doc["source"])
        folder = source["path"] if source else f"/ACME/restricted/{doc['department']}"
        nc.mkdirs(folder)
        stem = doc["id"].split(":", 1)[1]
        nc.put(f"{folder}/{stem}.md", f"# {doc['title']}\n\n{doc['body']}\n")
    print(f"files ok ({len(documents)} documents)")

    existing = nc.existing_shares()
    for source in sources:
        for ref in source["viewers"]:
            share_type, name = principal(ref)
            nc.share(source["path"], share_type, name, READ, existing)
        for ref in source["editors"]:
            share_type, name = principal(ref)
            nc.share(source["path"], share_type, name, EDIT, existing)
    for doc in documents:
        source = by_source.get(doc["source"])
        folder = source["path"] if source else f"/ACME/restricted/{doc['department']}"
        path = f"{folder}/{doc['id'].split(':', 1)[1]}.md"
        for ref in doc["viewers"]:
            share_type, name = principal(ref)
            nc.share(path, share_type, name, READ, existing)
        for ref in doc["editors"]:
            share_type, name = principal(ref)
            nc.share(path, share_type, name, EDIT, existing)
    print(f"shares ok ({len(existing)} shares)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
