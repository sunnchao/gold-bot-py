#!/usr/bin/env python3
"""生成 replay 对拍金标准(load/generate golden fixtures)。

只读访问源仓库 gold-bot,将 tests/replay/testdata 中的回放夹具复制到
backend/tests/fixtures/replay/,并(可选)通过源仓库 Node 栈验证夹具保真。

用法:
    python scripts/generate_golden.py                       # 默认源 ~/Downloads/Development/gold-bot
    python scripts/generate_golden.py --source /path/to/gold-bot
    python scripts/generate_golden.py --verify              # 复制后在源仓库跑 runReplay 对拍
    python scripts/generate_golden.py --out backend/tests/fixtures/replay
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_SOURCE = Path.home() / "Downloads" / "Development" / "gold-bot"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures" / "replay"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_fixtures(source_root: Path, out_root: Path) -> list[dict[str, str]]:
    testdata = source_root / "tests" / "replay" / "testdata"
    if not testdata.is_dir():
        raise SystemExit(f"[error] 源夹具目录不存在: {testdata}")

    entries = sorted(testdata.glob("*_snapshot.json"))
    if not entries:
        raise SystemExit(f"[error] 未找到 *_snapshot.json 夹具: {testdata}")

    manifest: list[dict[str, str]] = []
    out_root.mkdir(parents=True, exist_ok=True)
    for snapshot in entries:
        case = snapshot.name[: -len("_snapshot.json")]
        expected = snapshot.with_name(f"{case}_expected.json")
        case_dir = out_root / case
        case_dir.mkdir(parents=True, exist_ok=True)
        input_dst = case_dir / "input.json"
        expected_dst = case_dir / "expected.json"
        shutil.copyfile(snapshot, input_dst)
        if expected.is_file():
            shutil.copyfile(expected, expected_dst)
        manifest.append(
            {
                "case": case,
                "input": str(input_dst),
                "input_sha256": sha256(input_dst),
                "expected": str(expected_dst) if expected_dst.exists() else "",
                "expected_sha256": sha256(expected_dst) if expected_dst.exists() else "",
            }
        )
        print(f"[ok] {case}: input.json + expected.json")
    write_manifest(out_root, manifest)
    return manifest


def write_manifest(out_root: Path, manifest: list[dict[str, str]]) -> None:
    lines = [
        "# Replay 夹具清单(自动生成,勿手改)",
        "",
        "由 `scripts/generate_golden.py` 从源仓库 `tests/replay/testdata` 复制冻结。",
        "sha256 用于完整性校验;改动夹具必须重新运行本脚本并复核对拍。",
        "",
        "| case | input | input_sha256 | expected | expected_sha256 |",
        "|---|---|---|---|---|",
    ]
    for m in manifest:
        lines.append(
            f"| {m['case']} | `{Path(m['input']).relative_to(out_root)}` | {m['input_sha256']} | "
            f"`{Path(m['expected']).relative_to(out_root)}` | {m['expected_sha256']} |"
        )
    (out_root / "MANIFEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_with_node(source_root: Path, out_root: Path) -> None:
    """在源仓库运行 runReplay,signal/logs/position_commands 与 expected 对拍。"""
    runner = out_root.parent / "scripts" / "_golden_verify.mjs"
    runner.parent.mkdir(parents=True, exist_ok=True)
    trading_core = source_root / "packages" / "trading-core" / "src" / "index.ts"
    if not trading_core.is_file():
        raise SystemExit(f"[error] trading-core 源码缺失: {trading_core}")

    import_ = f"file://{trading_core}"
    _runner_body = f"""
import {{ runReplay }} from {json.dumps(import_)};
import {{ readFileSync, writeFileSync }} from 'node:fs';
import {{ join }} from 'node:path';
const outRoot = {json.dumps(str(out_root))};
const results = [];
for (const caseDir of (await import('node:fs')).readdirSync(outRoot, {{ withFileTypes: true }})) {{
  if (!caseDir.isDirectory()) continue;
  const dir = join(outRoot, caseDir.name);
  const input = JSON.parse(readFileSync(join(dir, 'input.json'), 'utf8'));
  const expected = JSON.parse(readFileSync(join(dir, 'expected.json'), 'utf8'));
  const got = runReplay(input);
  const ok = JSON.stringify(got.signal) === JSON.stringify(expected.signal)
    && JSON.stringify(got.logs) === JSON.stringify(expected.logs)
    && JSON.stringify(got.position_commands) === JSON.stringify(expected.position_commands);
  results.push({{ case: caseDir.name, ok, canProduce: got.canProduceLiveCommands }});
  console.log(`[verify] ${{caseDir.name}}: ${{ok ? 'PASS' : 'FAIL'}}`);
}}
writeFileSync(join(outRoot, 'VERIFY.json'), JSON.stringify(results, null, 2));
process.exit(results.every(r => r.ok) ? 0 : 1);
"""
    runner.write_text(_runner_body, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["pnpm", "exec", "tsx", str(runner)],
            cwd=source_root,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        raise SystemExit("[error] pnpm 不可用;请先在源仓库安装依赖") from None
    finally:
        runner.unlink(missing_ok=True)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        raise SystemExit("[error] Node 对拍失败(canProduceLiveCommands 或信号/日志/命令不一致)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--verify", action="store_true", help="复制后在源仓库跑 Node 对拍验证")
    args = parser.parse_args()

    if not (args.source / "tests" / "replay" / "testdata").is_dir():
        raise SystemExit(f"[error] 源仓库无效: {args.source}")
    manifest = copy_fixtures(args.source, args.out)
    print(f"[manifest] {args.out / 'MANIFEST.md'} ({len(manifest)} cases)")
    if args.verify:
        verify_with_node(args.source, args.out)
    print("[done]")


if __name__ == "__main__":
    main()
