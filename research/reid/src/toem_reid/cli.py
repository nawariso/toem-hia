"""Command-line entry point: ``uv run toem-reid <command>``.

Commands mirror the protocol order:
  ingest    labels + images + provenance -> immutable manifest, pHash sidecar, inventory
  split     manifest -> sealed, leakage-verified split specification
  evaluate  one config on one partition (validation calibrates; test is sealed)
  reproduce re-run a validation record and compare metrics (Gate A)
  decide    apply gates A-E to a sealed TEST record
  scale     synthetic-vector index/search mechanics (never accuracy evidence)
  budget    check configs against the section 6 experiment budget
  fetch-model download a config's pinned model revision into a cache outside Git
            and verify every file's SHA-256 (the only command that downloads)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from toem_reid.decision import decide
from toem_reid.dedup import near_duplicate_pairs
from toem_reid.evaluate import compare_metrics, run_experiment, scaling_benchmark
from toem_reid.experiment import check_budget, load_config
from toem_reid.ingest import ingest
from toem_reid.manifest import load_manifest, manifest_sha256
from toem_reid.matchers import pretrained_cfg_summary, resolve_model_cache
from toem_reid.models import acquire, parse_pin
from toem_reid.splits import SplitParameters, build_diagnostic_split, build_split, verify_split


def _near_duplicates(phash: Path | None, max_distance: int) -> list[tuple[str, str]]:
    if phash is None:
        return []
    data = json.loads(phash.read_text(encoding="utf-8"))
    return near_duplicate_pairs({k: int(v, 16) for k, v in data["hashes"].items()}, max_distance)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _degenerate_partitions(spec: dict[str, Any]) -> list[str]:
    present = {(row["partition"], row["role"]) for row in spec["assignments"]}
    return [
        f"{partition} has no {role} images"
        for partition in ("validation", "test")
        for role in ("gallery", "query_known", "query_unknown")
        if (partition, role) not in present
    ]


def cmd_ingest(args: argparse.Namespace) -> int:
    result = ingest(args.labels, args.images, args.provenance, out_dir=args.out, store=args.store)
    print(json.dumps({k: str(v) for k, v in asdict(result).items()}, indent=2))
    return 0


def cmd_split(args: argparse.Namespace) -> int:
    records = load_manifest(args.manifest)
    digest = manifest_sha256(records)
    params = SplitParameters(
        seed=args.seed,
        train_fraction=args.train,
        validation_fraction=args.validation,
        unknown_fraction=args.unknown,
        query_fraction=args.query,
        burst_seconds=args.burst_seconds,
    )
    if args.diagnostic:
        spec = build_diagnostic_split(records, params, manifest_sha256=digest)
    else:
        pairs = _near_duplicates(args.phash, args.max_hamming)
        spec = build_split(records, pairs, params, manifest_sha256=digest)
        problems = verify_split(spec, records, pairs)
        if problems:
            print("split failed verification:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
    degenerate = _degenerate_partitions(spec)
    if degenerate:
        print(
            "split is unusable: " + "; ".join(degenerate) + ". A Side-ID needs at least two "
            "independent leakage groups (sessions not merged by near-duplicate or burst "
            "detection) to provide both gallery and known-query images.",
            file=sys.stderr,
        )
        return 1
    if args.out.exists():
        print(f"refusing to overwrite sealed split {args.out}", file=sys.stderr)
        return 1
    _write_json(args.out, spec)
    print(
        json.dumps(
            {"split": str(args.out), "split_sha256": spec["split_sha256"], "label": spec["label"]}
        )
    )
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    record = run_experiment(
        manifest_path=args.manifest,
        provenance_path=args.provenance,
        split_path=args.split,
        config_path=args.config,
        store=args.store,
        results_dir=args.results,
        partition=args.partition,
        seal_log=args.seal_log or args.results / "sealed-test-log.jsonl",
        near_duplicates=_near_duplicates(args.phash, args.max_hamming),
        model_cache=args.model_cache,
    )
    closed = record["metrics"]["closed_set"]
    open_set = record["metrics"]["open_set"]
    print(
        json.dumps(
            {
                "experiment_id": record["experiment_id"],
                "evidence_label": record["evidence_label"],
                "protocol_label": record["protocol_label"],
                "top1": closed["top_k"]["1"]["rate"],
                "top5": closed["top_k"]["5"]["rate"],
                "unknown_far": open_set["unknown_false_accept"]["rate"],
                "record": record["artifacts"]["record"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_reproduce(args: argparse.Namespace) -> int:
    first = json.loads(args.record.read_text(encoding="utf-8"))
    if first["partition"] != "validation":
        print(
            "reproduce validation records; sealed test re-runs go through evaluate", file=sys.stderr
        )
        return 1
    rerun = run_experiment(
        manifest_path=args.manifest,
        provenance_path=args.provenance,
        split_path=args.split,
        config_path=args.config,
        store=args.store,
        results_dir=args.results,
        partition="validation",
        seal_log=args.results / "sealed-test-log.jsonl",
        near_duplicates=_near_duplicates(args.phash, args.max_hamming),
        model_cache=args.model_cache,
    )
    diffs = compare_metrics(first["metrics"], rerun["metrics"], args.tolerance)
    print(json.dumps({"reproducible": not diffs, "differences": diffs[:20]}, indent=2))
    return 0 if not diffs else 1


def cmd_decide(args: argparse.Namespace) -> int:
    test = json.loads(args.test_record.read_text(encoding="utf-8"))
    diagnostic = (
        json.loads(args.diagnostic_record.read_text(encoding="utf-8"))
        if args.diagnostic_record
        else None
    )
    closed, open_set = test["metrics"]["closed_set"], test["metrics"]["open_set"]
    result = {
        "evidence_label": test["evidence_label"],
        "counts_toward_product_gates": test["counts_toward_product_gates"],
        "partition": test["partition"],
        "representative": test["representative"],
        "reproducible": args.reproducible,
        "leakage_errors": [],
        "provenance_complete": True,
        "sealed": test.get("sealed_test", False),
        "top1": closed["top_k"]["1"]["rate"],
        "top5": closed["top_k"]["5"]["rate"],
        "far": open_set["unknown_false_accept"]["rate"],
        "far_ci95": open_set["unknown_false_accept"]["ci95"],
        "known_top5_after_threshold": open_set["known_top_k_after_threshold"]["5"]["rate"],
        "diagnostic_top5": diagnostic["metrics"]["closed_set"]["top_k"]["5"]["rate"]
        if diagnostic
        else None,
        "dataset": test["dataset"],
    }
    outcome = decide(result)
    payload = {"input": result, "outcome": asdict(outcome), "source_record": test["experiment_id"]}
    if args.out:
        _write_json(args.out, payload)
    print(json.dumps(asdict(outcome), indent=2, ensure_ascii=False))
    return 0


def cmd_scale(args: argparse.Namespace) -> int:
    result = scaling_benchmark(
        args.dim, tuple(args.side_ids), args.images_per_side_id, args.queries, args.seed
    )
    if args.out:
        _write_json(args.out, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_fetch_model(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if config.model_artifact is None:
        raise ValueError(f"{args.config.name} has no model_artifact pin; nothing to fetch")
    resolved = acquire(
        parse_pin(config.model_artifact),
        resolve_model_cache(args.model_cache),
        allow_download=True,
    )
    print(
        json.dumps(
            {
                "config_id": config.config_id,
                "artifact": resolved.record(),
                "preprocessing": pretrained_cfg_summary(resolved.config_path),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    check_budget([load_config(p) for p in args.configs])
    print("within the Requirement 004 experiment budget")
    return 0


def _common_eval(p: argparse.ArgumentParser) -> None:
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--provenance", type=Path, required=True)
    p.add_argument("--split", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--results", type=Path, required=True)
    p.add_argument("--phash", type=Path)
    p.add_argument("--max-hamming", type=int, default=4)
    p.add_argument("--model-cache", type=Path, help="pinned model cache outside Git (families B/C)")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="toem-reid", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = root.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest")
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--images", type=Path, required=True)
    p.add_argument("--provenance", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--store", type=Path)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("split")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--phash", type=Path)
    p.add_argument("--max-hamming", type=int, default=4)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--train", type=float, default=0.0)
    p.add_argument("--validation", type=float, default=0.5)
    p.add_argument("--unknown", type=float, default=0.3)
    p.add_argument("--query", type=float, default=0.5)
    p.add_argument("--burst-seconds", type=float, default=0.0)
    p.add_argument("--diagnostic", action="store_true")
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_split)

    p = sub.add_parser("evaluate")
    _common_eval(p)
    p.add_argument("--partition", choices=("validation", "test"), required=True)
    p.add_argument("--seal-log", type=Path)
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("reproduce")
    _common_eval(p)
    p.add_argument("--record", type=Path, required=True)
    p.add_argument("--tolerance", type=float, default=1e-9)
    p.set_defaults(func=cmd_reproduce)

    p = sub.add_parser("decide")
    p.add_argument("--test-record", type=Path, required=True)
    p.add_argument("--diagnostic-record", type=Path)
    p.add_argument("--reproducible", action="store_true")
    p.add_argument("--out", type=Path)
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("scale")
    p.add_argument("--dim", type=int, default=768)
    p.add_argument("--side-ids", type=int, nargs="+", default=[50, 100, 250, 500])
    p.add_argument("--images-per-side-id", type=int, default=4)
    p.add_argument("--queries", type=int, default=200)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", type=Path)
    p.set_defaults(func=cmd_scale)

    p = sub.add_parser("budget")
    p.add_argument("configs", type=Path, nargs="+")
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("fetch-model")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--model-cache", type=Path)
    p.set_defaults(func=cmd_fetch_model)
    return root


MAX_ERROR_ITEMS = 10


def _bounded(message: str) -> str:
    """Keep CLI errors readable when a check reports hundreds of problems."""
    items = message.split("; ")
    if len(items) <= MAX_ERROR_ITEMS:
        return message
    return "; ".join(items[:MAX_ERROR_ITEMS]) + f"; ... (+{len(items) - MAX_ERROR_ITEMS} more)"


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, RuntimeError) as error:
        print(f"error: {_bounded(str(error))}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
