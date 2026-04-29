import argparse
import logging

import uvicorn

from .api import create_app_from_config

_LOG = logging.getLogger("model_hub_py.cli")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="root log level; use DEBUG to trace rknn-smi, scheduler, and session polling",
    )
    args = parser.parse_args()

    if args.command == "serve":
        logging.basicConfig(
            level=getattr(logging, args.log_level),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        _LOG.info(
            "Starting model_hub_py HTTP server: host=%s port=%d config=%s",
            args.host,
            args.port,
            args.config,
        )
        app = create_app_from_config(args.config)
        try:
            uvicorn.run(app, host=args.host, port=args.port)
        finally:
            _LOG.info(
                "model_hub_py HTTP server process exiting (host=%s port=%d)",
                args.host,
                args.port,
            )
