"""Entry point for ``python -m paperless_stamp``."""

from __future__ import annotations

import logging
import sys

from paperless_stamp.worker import WorkerConfig, run_worker


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    try:
        config = WorkerConfig.from_env()
    except ValueError as exc:
        logging.getLogger(__name__).error("Configuration error: %s", exc)
        sys.exit(1)

    run_worker(config)


if __name__ == "__main__":
    main()
