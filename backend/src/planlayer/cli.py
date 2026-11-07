"""The ``planlayer`` command: ``migrate`` and ``serve``."""

import argparse

import uvicorn

from planlayer.config import Settings
from planlayer.store import migrate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="planlayer")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate_cmd = commands.add_parser("migrate", help="upgrade the app store schema")
    migrate_cmd.add_argument("--revision", default="head")

    serve_cmd = commands.add_parser("serve", help="run the web server")
    # All interfaces: the server runs inside a container.
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8000)
    serve_cmd.add_argument("--reload", action="store_true", help="development only")

    args = parser.parse_args(argv)
    match args.command:
        case "migrate":
            migrate.upgrade(Settings(), args.revision)  # type: ignore[call-arg]
        case "serve":
            uvicorn.run(
                "planlayer.api.app:create_app",
                factory=True,
                host=args.host,
                port=args.port,
                reload=args.reload,
            )
