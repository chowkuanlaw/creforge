"""Throughput benchmark: python benchmarks/bench_generate.py --subjects 1000000 --workers 8"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from pathlib import Path

import creforge as cf


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", type=int, default=200_000)
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--chunk-size", type=int, default=50_000)
    ap.add_argument("--keep", type=Path, help="Write here instead of a temp dir")
    args = ap.parse_args()

    out = args.keep or Path(tempfile.mkdtemp(prefix="creforge-bench-"))
    cfg = cf.Config.from_profile("baseline", subjects=args.subjects, months=args.months,
                                 seed=1, chunk_size=args.chunk_size)
    t0 = time.perf_counter()
    manifest = cf.write_dataset(cfg, out, workers=args.workers)
    secs = time.perf_counter() - t0
    rows = manifest["row_counts"]["account_month"]
    size = sum(p.stat().st_size for p in out.rglob("*.parquet"))
    print(f"subjects={args.subjects:,} months={args.months} workers={args.workers}")
    print(f"account_month rows={rows:,}  time={secs:.1f}s  rows/s={rows / secs:,.0f}  "
          f"parquet={size / 1e9:.2f} GB")
    if not args.keep:
        shutil.rmtree(out)


if __name__ == "__main__":
    main()
