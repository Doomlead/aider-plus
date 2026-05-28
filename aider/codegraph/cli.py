from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from aider.codegraph.core import CodeGraph
from aider.codegraph.watcher import CodeGraphWatcher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aider graph", description="Native Aider Plus Code Intelligence Graph"
    )
    parser.add_argument("--repo", default=".", help="Repository path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    index = sub.add_parser("index")
    index.add_argument("--force", action="store_true")
    sub.add_parser("sync")
    sub.add_parser("status")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    context = sub.add_parser("context")
    context.add_argument("query")
    context.add_argument("--limit", type=int, default=8)
    for name in ("callers", "callees"):
        p = sub.add_parser(name)
        p.add_argument("target")
        p.add_argument("--limit", type=int, default=25)
    impact = sub.add_parser("impact")
    impact.add_argument("target")
    impact.add_argument("--depth", type=int, default=2)
    affected = sub.add_parser("affected")
    affected.add_argument("files", nargs="*")
    affected.add_argument("--depth", type=int, default=2)
    node = sub.add_parser("node")
    node.add_argument("name")
    watch = sub.add_parser("watch")
    watch.add_argument("--once", action="store_true")
    watch.add_argument("--interval", type=float, default=1.0)
    return parser


def handle_graph_cli(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(
        list(argv)[1:] if argv and argv[0] == "graph" else list(argv)
    )
    graph = CodeGraph(Path(args.repo))
    if args.command == "init":
        result = graph.index(force=False)
    elif args.command == "index":
        result = graph.index(force=args.force)
    elif args.command == "sync":
        result = graph.sync()
    elif args.command == "status":
        result = graph.status()
    elif args.command == "search":
        result = graph.search(args.query, limit=args.limit)
    elif args.command == "context":
        result = graph.context(args.query, limit=args.limit)
    elif args.command == "callers":
        result = graph.callers(args.target, limit=args.limit)
    elif args.command == "callees":
        result = graph.callees(args.target, limit=args.limit)
    elif args.command == "impact":
        result = graph.impact(args.target, depth=args.depth)
    elif args.command == "affected":
        result = graph.affected_tests(args.files or None, depth=args.depth)
    elif args.command == "node":
        result = graph.node(args.name)
    elif args.command == "watch":
        watcher = CodeGraphWatcher(graph, interval=args.interval)
        watcher.run(once=args.once, on_sync=lambda data: print(CodeGraph.dumps(data)))
        return 0
    else:  # pragma: no cover
        parser.error(f"unknown command {args.command}")
    print(CodeGraph.dumps(result))
    return 0
