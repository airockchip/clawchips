from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from .config import DEFAULT_CONFIG_PATH, get_settings


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the RKClawServer gateway")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        metavar="PATH",
        help="path to the gateway TOML configuration (default: gateway.toml)",
    )
    args = parser.parse_args(argv)

    settings = get_settings(args.config)

    # Import after parsing so ``--help`` does not initialize the application
    # stack or require a configuration file.
    from .app import create_app

    application = create_app(settings=settings, config_path=args.config)
    uvicorn.run(application, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
