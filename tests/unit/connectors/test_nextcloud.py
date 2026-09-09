"""Nextcloud connector against a mocked WebDAV + OCS share API."""

import httpx

from headofcontext.read.connectors import NextcloudConnector

BASE = "http://nc.test"

PROPFIND = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
 <d:response><d:href>/remote.php/dav/files/svc/ACME/</d:href><d:propstat><d:prop>
   <oc:fileid>10</oc:fileid><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/remote.php/dav/files/svc/ACME/rh/</d:href><d:propstat><d:prop>
   <oc:fileid>11</oc:fileid><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/remote.php/dav/files/svc/ACME/rh/acme-0001.md</d:href><d:propstat><d:prop>
   <oc:fileid>12</oc:fileid><d:resourcetype/><d:getcontenttype>text/markdown</d:getcontenttype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/remote.php/dav/files/svc/ACME/rh/acme-0002.md</d:href><d:propstat><d:prop>
   <oc:fileid>13</oc:fileid><d:resourcetype/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/remote.php/dav/files/svc/ACME/restricted/</d:href><d:propstat><d:prop>
   <oc:fileid>14</oc:fileid><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/remote.php/dav/files/svc/ACME/restricted/rh/</d:href><d:propstat><d:prop>
   <oc:fileid>15</oc:fileid><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/remote.php/dav/files/svc/ACME/restricted/rh/acme-0003.md</d:href><d:propstat><d:prop>
   <oc:fileid>16</oc:fileid><d:resourcetype/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/remote.php/dav/files/svc/ACME/rh/%C3%A9t%C3%A9%20notes.md</d:href><d:propstat><d:prop>
   <oc:fileid>17</oc:fileid><d:resourcetype/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
</d:multistatus>"""

SHARES = {
    "ocs": {
        "meta": {"status": "ok", "statuscode": 200},
        "data": [
            {
                "id": "1",
                "share_type": 1,
                "share_with": "rh",
                "path": "/ACME/rh",
                "permissions": 1,
                "item_type": "folder",
            },
            {
                "id": "2",
                "share_type": 0,
                "share_with": "samir.vincent",
                "path": "/ACME/rh",
                "permissions": 15,
                "item_type": "folder",
            },
            {
                "id": "3",
                "share_type": 0,
                "share_with": "ines.leroy",
                "path": "/ACME/rh/acme-0002.md",
                "permissions": 1,
                "item_type": "file",
            },
            {
                "id": "4",
                "share_type": 0,
                "share_with": "carol",
                "path": "/ACME/restricted/rh/acme-0003.md",
                "permissions": 15,
                "item_type": "file",
            },
            {
                "id": "5",
                "share_type": 3,
                "share_with": None,
                "path": "/ACME/rh/acme-0001.md",
                "permissions": 1,
                "item_type": "file",
            },
            {
                "id": "6",
                "share_type": 0,
                "share_with": "bad user",
                "path": "/ACME/rh",
                "permissions": 1,
                "item_type": "folder",
            },
            {
                "id": "7",
                "share_type": 1,
                "share_with": "it",
                "path": "/Other",
                "permissions": 1,
                "item_type": "folder",
            },
        ],
    }
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.method == "PROPFIND":
        assert request.headers.get("Depth") == "infinity"
        assert request.url.path == "/remote.php/dav/files/svc/ACME"
        return httpx.Response(
            207, content=PROPFIND.encode(), headers={"content-type": "application/xml"}
        )
    if request.url.path.endswith("/shares"):
        assert request.headers.get("OCS-APIRequest") == "true"
        return httpx.Response(200, json=SHARES)
    return httpx.Response(404)


async def test_snapshot() -> None:
    connector = NextcloudConnector(
        BASE, user="svc", password="secret", root="/ACME", transport=httpx.MockTransport(handler)
    )
    snapshot = await connector.snapshot()
    t = snapshot.tuples
    assert ("group:rh#member", "viewer", "source:nextcloud-rh") in t
    assert ("user:samir.vincent", "editor", "source:nextcloud-rh") in t
    assert ("source:nextcloud-rh", "parent", "document:acme-0001") in t
    assert ("source:nextcloud-rh", "parent", "document:acme-0002") in t
    assert ("user:ines.leroy", "viewer", "document:acme-0002") in t
    assert ("source:nextcloud-restricted", "parent", "document:acme-0003") in t
    assert ("user:carol", "editor", "document:acme-0003") in t
    assert not any(u.startswith("user:bad") for u, _, _ in t)  # invalid uid dropped
    assert not any(
        o == "source:nextcloud-restricted" and r in ("viewer", "editor") for _, r, o in t
    )
    assert not any("Other" in o for _, _, o in t)  # shares outside the root are ignored
    assert not any("été" in o or "%C3" in o for _, _, o in t)  # unsafe stem dropped, not sanitized
    ids = {d.id for d in snapshot.documents}
    assert ids == {"document:acme-0001", "document:acme-0002", "document:acme-0003"}


async def test_backend_id_strategy() -> None:
    connector = NextcloudConnector(
        BASE,
        user="svc",
        password="secret",
        root="/ACME",
        id_strategy="backend",
        transport=httpx.MockTransport(handler),
    )
    snapshot = await connector.snapshot()
    assert ("source:nextcloud-rh", "parent", "document:nc-12") in snapshot.tuples
    assert ("user:ines.leroy", "viewer", "document:nc-13") in snapshot.tuples


async def test_http_failure_raises() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    connector = NextcloudConnector(
        BASE, user="svc", password="x", root="/ACME", transport=httpx.MockTransport(down)
    )
    try:
        await connector.snapshot()
    except Exception as exc:
        assert "nextcloud" in str(exc).lower()
    else:
        raise AssertionError("expected a failure")
