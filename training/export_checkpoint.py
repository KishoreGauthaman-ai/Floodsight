"""Package the best model weights with metadata (data version, metrics,
date) for release publishing. Weights are never committed to git."""

from __future__ import annotations


def export(checkpoint: str) -> None:
    raise NotImplementedError("Checkpoint export runs in Phase 7.")


if __name__ == "__main__":
    raise SystemExit("Not implemented yet (Phase 7).")
