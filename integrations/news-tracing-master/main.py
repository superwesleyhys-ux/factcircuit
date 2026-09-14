#!/usr/bin/env python3
"""Local Astra news tracing by default; the legacy API renderer is opt-in."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _run_local(args):
    # The repository path works without installing the legacy API dependencies.
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from newsverify.cli import main as trace_main

    options = ["--tunnel", "local"]
    for name in ("model", "reasoning_effort", "timeout", "max_model_calls", "output"):
        value = getattr(args, name)
        if value is not None:
            options.extend(["--" + name.replace("_", "-"), str(value)])
    if args.input:
        return trace_main(["trace-news", str(args.input), *options])
    item = {"id": "news-1", "text": args.news}
    url = urlsplit(args.news)
    if url.scheme in {"http", "https"} and url.netloc:
        item["url"] = args.news
    payload = {"news": [item]}
    if args.depth is not None:
        payload["config"] = {"depth": args.depth}
    with tempfile.TemporaryDirectory(prefix="factcircuit-news-") as directory:
        source = Path(directory) / "input.json"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return trace_main(["trace-news", str(source), *options])


def _run_api(args):
    # Import the original renderer/client only after explicit API selection.
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from legacy_main import run_api
    asyncio.run(run_api(args.news, depth=3 if args.depth is None else args.depth,
                        model=args.model))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="新闻溯源：默认通过本地 Codex 使用 Astra，输出 FactCircuit JSON。")
    parser.add_argument("news", nargs="?", help="新闻文本或 URL；省略时从标准输入读取")
    parser.add_argument("--input", type=Path, help="FactCircuit trace-news JSON 输入")
    parser.add_argument("--output", type=Path, help="本地模式 JSON 输出文件；默认输出到标准输出")
    parser.add_argument("--tunnel", choices=("local", "api"), default="local",
                        help="默认 local；api 显式启用旧版 API 工作流")
    parser.add_argument("--model", help="本地默认 gpt-6-astra；可显式选择其他本地 Codex 模型")
    parser.add_argument("--reasoning-effort", help="本地 Codex 推理强度")
    parser.add_argument("--depth", type=int, help="文本/URL 的因果递归深度")
    parser.add_argument("--timeout", type=float, help="本地模式每次模型调用超时秒数")
    parser.add_argument("--max-model-calls", type=int, help="本地模式模型调用总预算")
    args = parser.parse_args(argv)
    if args.input and args.news:
        parser.error("news and --input are mutually exclusive")
    if args.input and args.depth is not None:
        parser.error("Set config.depth in the --input JSON file")
    if args.tunnel == "api" and any(getattr(args, k) is not None for k in
                                   ("input", "output", "reasoning_effort", "timeout", "max_model_calls")):
        parser.error("--tunnel api uses the legacy console renderer; JSON and local-budget flags require local mode")
    if not args.input and not args.news:
        if sys.stdin.isatty():
            print("请输入新闻文本或 URL，然后按 Ctrl-D：", file=sys.stderr)
        args.news = sys.stdin.read().strip()
    if not args.input and not args.news:
        parser.error("Provide news text, a URL, or --input JSON")
    try:
        return _run_local(args) if args.tunnel == "local" else _run_api(args)
    except (OSError, ValueError) as exc:
        print(f"news-tracing: {exc}", file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        if args.tunnel != "api":
            raise
        print(f"Legacy API dependency unavailable ({exc.name}); install requirements.txt for --tunnel api.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
