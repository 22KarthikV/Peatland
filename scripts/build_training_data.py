"""Build the Galloway training data (composites, weak labels, MANIFEST.json).

Thin wrapper around ``ml.build_data``; run from the repo root:

    uv run python scripts/build_training_data.py --help
    uv run python scripts/build_training_data.py            # both Galloway regions, spring 2025
"""

from ml.build_data import main

if __name__ == "__main__":
    raise SystemExit(main())
