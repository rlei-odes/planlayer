"""The ``planlayer`` command."""

import argparse
from datetime import date

import uvicorn

from planlayer import access, devdata, rounds, sync
from planlayer.adapters.source import PostgresSource
from planlayer.config import Settings
from planlayer.store import migrate
from planlayer.store.db import create_app_engine


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="planlayer")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate_cmd = commands.add_parser("migrate", help="upgrade the app store schema")
    migrate_cmd.add_argument("--revision", default="head")

    sync_cmd = commands.add_parser("sync", help="sync master data and actuals from the source")
    sync_cmd.add_argument("--entity", help="only this entity's actuals")

    version_cmd = commands.add_parser(
        "create-version", help="create a plan version and its planning round (status setup)"
    )
    version_cmd.add_argument("code")
    version_cmd.add_argument("--name", required=True)
    version_cmd.add_argument("--fiscal-year", type=int, required=True)
    version_cmd.add_argument("--kind", choices=["budget", "forecast"], default="budget")
    version_cmd.add_argument("--deadline", type=date.fromisoformat, help="YYYY-MM-DD")
    version_cmd.add_argument("--copy-setup-from", metavar="CODE", help="an earlier round")

    commands.add_parser(
        "seed-dev-users", help="create the development users of the test data (not in production)"
    )

    serve_cmd = commands.add_parser("serve", help="run the web server")
    # All interfaces: the server runs inside a container.
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8000)
    serve_cmd.add_argument("--reload", action="store_true", help="development only")

    args = parser.parse_args(argv)
    match args.command:
        case "migrate":
            migrate.upgrade(Settings(), args.revision)  # type: ignore[call-arg]
        case "sync":
            settings = Settings()  # type: ignore[call-arg]
            engine, source = create_app_engine(settings), PostgresSource(settings.source_url)
            try:
                result = sync.run(
                    engine,
                    source,
                    sync.default_mapping(settings.source_schema),
                    actor=access.system_user(engine),
                    entity=args.entity,
                )
            except sync.SyncError as e:
                raise SystemExit(f"sync refused, nothing written: {e}") from None
            finally:
                engine.dispose()
                source.dispose()
            print(f"synced {result.members} members, {result.actual_rows} actual cells")
        case "create-version":
            settings = Settings()  # type: ignore[call-arg]
            engine = create_app_engine(settings)
            try:
                rounds.create_round(
                    engine,
                    access.system_user(engine),
                    args.code,
                    args.name,
                    args.kind,
                    args.fiscal_year,
                    deadline=args.deadline,
                    copy_from=args.copy_setup_from,
                )
            except rounds.NotFoundError as e:
                raise SystemExit(str(e)) from None
            finally:
                engine.dispose()
            print(
                f"created {args.kind} round {args.code} for fiscal year {args.fiscal_year} "
                "(status setup; open it on the status page)"
            )
        case "seed-dev-users":
            settings = Settings()  # type: ignore[call-arg]
            if settings.environment == "production":
                raise SystemExit("seed-dev-users is refused in production")
            engine = create_app_engine(settings)
            try:
                created = devdata.seed(engine)
            finally:
                engine.dispose()
            print(f"created {len(created)} user(s): {', '.join(created) or '-'}")
        case "serve":
            uvicorn.run(
                "planlayer.api.app:create_app",
                factory=True,
                host=args.host,
                port=args.port,
                reload=args.reload,
            )
