"""Drive the HTTP service and report latency percentiles (ADR 0015).

    uv run python scripts/load_test.py --api http://localhost:8000 --token <biscuit> \\
        --agent-token <oidc access token> --tool tool:mail.send -n 2000 -c 50 --p95-ms 50

Exit status is 1 when the p95 exceeds ``--p95-ms`` or any request failed. Tokens come from
``hoc token issue`` (biscuit) and the IdP (agent client credentials); nothing is logged but
timings.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class Run:
    path: str
    payload: dict[str, object]
    queue: asyncio.Queue[int]
    timings: list[float] = field(default_factory=list)
    errors: list[int] = field(default_factory=list)


async def worker(client: httpx.AsyncClient, run: Run) -> None:
    while True:
        try:
            run.queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        started = time.perf_counter()
        try:
            response = await client.post(run.path, json=run.payload)
            if response.status_code != 200:
                run.errors.append(response.status_code)
        except httpx.HTTPError:
            run.errors.append(0)
        run.timings.append((time.perf_counter() - started) * 1000)


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))
    return ordered[index]


async def run(args: argparse.Namespace) -> int:
    if args.filter_items:
        path = "/v1/read/filter"
        payload: dict[str, object] = {
            "token": args.token,
            "items": [{"id": f"document:acme-{n:04d}"} for n in range(1, args.filter_items + 1)],
        }
    else:
        path = "/v1/actions/gate"
        payload = {"token": args.token, "tool": args.tool, "args": {"load": True}}
    queue: asyncio.Queue[int] = asyncio.Queue()
    for n in range(args.requests):
        queue.put_nowait(n)
    load = Run(path, payload, queue)
    async with httpx.AsyncClient(
        base_url=args.api,
        headers={"Authorization": f"Bearer {args.agent_token}"},
        timeout=30,
        limits=httpx.Limits(max_connections=args.concurrency),
    ) as client:
        started = time.perf_counter()
        await asyncio.gather(*(worker(client, load) for _ in range(args.concurrency)))
        elapsed = time.perf_counter() - started
    timings, errors = load.timings, load.errors
    p50, p95, p99 = (percentile(timings, p) for p in (50, 95, 99))
    print(f"{path}: {len(timings)} requests, {args.concurrency} concurrent, {elapsed:.1f}s")
    print(f"rps={len(timings) / elapsed:.0f} p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")
    print(f"mean={statistics.fmean(timings):.1f}ms max={max(timings):.1f}ms errors={len(errors)}")
    if errors:
        print(f"error statuses: {sorted(set(errors))}", file=sys.stderr)
        return 1
    if p95 > args.p95_ms:
        print(f"p95 {p95:.1f}ms exceeds {args.p95_ms}ms", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--token", required=True, help="biscuit for the user/agent chain")
    parser.add_argument("--agent-token", required=True, help="OIDC access token of the agent")
    parser.add_argument("--tool", default="tool:mail.send")
    parser.add_argument("--filter-items", type=int, default=0, help="drive /read/filter instead")
    parser.add_argument("-n", "--requests", type=int, default=1000)
    parser.add_argument("-c", "--concurrency", type=int, default=20)
    parser.add_argument("--p95-ms", type=float, default=100.0)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
