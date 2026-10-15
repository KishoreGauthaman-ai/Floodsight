"""Kuro Siwo webdataset inspection and event-wise split preparation.

Inspects the REAL shards on disk (AGENT.md 8.3: never assume layout; the
structure was verified against the downloaded webdataset):
each sample is ``NNNNNN.{flood_vv,flood_vh,sec1_vv,sec1_vh,sec2_vv,sec2_vh,
dem,mask,valid_mask}.npy`` (224x224 float32) plus ``NNNNNN.info.json`` with
the scene sources. The event identity for splitting is the MASTER scene
dataset name (patches sharing a master scene belong to the same flood
event), so held-out events are truly unseen.

Outputs ``data/training/splits_manifest.json`` with, per selected sample:
event id, shard, member key, flood fraction. Splits are by whole events.
"""

from __future__ import annotations

import argparse
import io
import json
import tarfile
from collections import defaultdict
from pathlib import Path

import numpy as np

from floodsight.config import REPO_ROOT

SHARDS = {
    "train": REPO_ROOT / "data" / "training" / "kurosiwo" / "train_GRD_shard-00000.tar",
    "test": REPO_ROOT / "data" / "training" / "kurosiwo" / "test_GRD_shard-00000.tar",
}
MANIFEST_PATH = REPO_ROOT / "data" / "training" / "splits_manifest.json"


def sample_event(info: dict) -> str:
    """Master scene dataset name = the event identity for event-wise splits."""
    for d in info.get("datasets", {}).values():
        if d.get("master"):
            return d.get("name", "unknown")
    return "unknown"


def scan_shard(shard_path: Path) -> list[dict]:
    """One pass over a shard: event id + flood fraction per sample."""
    samples = []
    current: dict = {}
    with tarfile.open(shard_path) as tf:
        for member in tf:
            if not (member.name.endswith(".npy") or member.name.endswith(".json")):
                continue
            stem = member.name.split(".")[0]
            if member.name.endswith("info.json"):
                info = json.load(tf.extractfile(member))
                current[stem] = {"event": sample_event(info)}
            elif member.name.endswith("mask.npy"):
                mask = np.load(io.BytesIO(tf.extractfile(member).read()))
                current.setdefault(stem, {})["flood_fraction"] = float(
                    (mask == 2).mean()
                )
    for stem, meta in sorted(current.items()):
        meta["sample"] = stem
        samples.append(meta)
    return samples


def build_manifest(max_train_events: int = 19, max_val_events: int = 5,
                   max_test_events: int = 6, per_event_cap: int = 120) -> dict:
    """Scan shards and split BY EVENT; write the manifest."""
    train_samples = scan_shard(SHARDS["train"])
    test_samples = scan_shard(SHARDS["test"])

    by_event: dict[str, dict] = defaultdict(lambda: {"train": [], "test": []})
    for s in train_samples:
        by_event[s["event"]]["train"].append(s)
    for s in test_samples:
        by_event[s["event"]]["test"].append(s)

    train_events = sorted(e for e in by_event if by_event[e]["train"])
    test_events = sorted(e for e in by_event if by_event[e]["test"])
    overlap = set(train_events) & set(test_events)
    if overlap:
        raise SystemExit(f"EVENT LEAK: {overlap} appear in both shards")

    # deterministic order; take the requested number of events per split
    chosen_train = train_events[:max_train_events]
    chosen_val = train_events[max_train_events:max_train_events + max_val_events]
    chosen_test = test_events[:max_test_events]

    def rows(events, split, shard_key):
        out = []
        for e in events:
            samples = by_event[e][shard_key]
            # flood pixels are rare: order flood-positive first, cap per event
            samples = sorted(
                samples, key=lambda s: -(s.get("flood_fraction") or 0.0)
            )
            for s in samples[:per_event_cap]:
                out.append({
                    "event": e, "split": split, "shard": shard_key,
                    "sample": s["sample"],
                    "flood_fraction": s.get("flood_fraction"),
                })
        return out

    manifest = {
        "source": "Kuro Siwo webdataset (HuggingFace orion-ai-lab/Kuro-Siwo-Webdataset), "
                  "GRD shards, subset per DECISIONS D35",
        "channels": ["flood_vv", "flood_vh", "sec1_vv", "sec1_vh",
                     "dem", "slope(computed)", "diff_vv(post-pre)"],
        "classes": {"0": "land", "1": "permanent_water", "2": "flood_water"},
        "events": {
            "train": chosen_train, "validation": chosen_val, "test": chosen_test,
        },
        "samples": (
            rows(chosen_train, "train", "train")
            + rows(chosen_val, "validation", "train")
            + rows(chosen_test, "test", "test")
        ),
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"manifest: {len(chosen_train)} train / {len(chosen_val)} val / "
          f"{len(chosen_test)} test events; "
          f"{len(manifest['samples'])} samples -> {MANIFEST_PATH}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-train-events", type=int, default=19)
    parser.add_argument("--max-val-events", type=int, default=5)
    parser.add_argument("--max-test-events", type=int, default=6)
    parser.add_argument("--per-event-cap", type=int, default=120)
    args = parser.parse_args()
    build_manifest(args.max_train_events, args.max_val_events,
                   args.max_test_events, args.per_event_cap)


if __name__ == "__main__":
    main()
