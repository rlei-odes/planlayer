"""The ``planlayer`` command."""

import argparse
from datetime import date

import uvicorn

from planlayer import access, devdata, publish, rounds, sync
from planlayer.adapters.source import PostgresSource
from planlayer.config import Settings
from planlayer.store import migrate
from planlayer.store.db import create_app_engine
from planlayer.store.output import create_output_engine


def report(p: sync.SyncPlan) -> None:
    print(f"datasets: {', '.join(p.datasets) or 'none mapped'}")
    for label, items in (
        ("problems", p.problems),
        ("new", p.new),
        ("changed", p.changed),
        ("no longer delivered", p.gone),
        ("matches to confirm", [m.key for m in p.matches]),
    ):
        print(f"{label}: {len(items)}" + (f" ({', '.join(items[:10])})" if items else ""))
    if p.actual_cells is not None:
        print(f"actual cells: {p.actual_cells}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="planlayer")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate_cmd = commands.add_parser("migrate", help="upgrade the app store schema")
    migrate_cmd.add_argument("--revision", default="head")

    sync_cmd = commands.add_parser("sync", help="sync master data and actuals from the source")
    sync_cmd.add_argument("--entity", help="only this entity's actuals")
    sync_cmd.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    sync_cmd.add_argument(
        "--confirm-matches",
        action="store_true",
        help="let the source take over manual members with the same codes",
    )

    commands.add_parser(
        "seed-dev-mappings",
        help="map the datasets to the test warehouse's views (not in production)",
    )

    version_cmd = commands.add_parser(
        "create-version", help="create a plan version and its planning round (status setup)"
    )
    version_cmd.add_argument("code")
    version_cmd.add_argument("--name", required=True)
    version_cmd.add_argument("--fiscal-year", type=int, required=True)
    version_cmd.add_argument("--kind", choices=["budget", "forecast"], default="budget")
    version_cmd.add_argument("--deadline", type=date.fromisoformat, help="YYYY-MM-DD")
    version_cmd.add_argument("--copy-setup-from", metavar="CODE", help="an earlier round")

    commands.add_parser("republish", help="rebuild the output from every round's last publish run")

    commands.add_parser(
        "seed-dev-users", help="create the development users of the test data (not in production)"
    )

    plans_cmd = commands.add_parser(
        "seed-dev-plans",
        help="plan values for some cost centers, as their owners began planning (development)",
    )
    plans_cmd.add_argument("--round", default="B2026", help="the planning round (default B2026)")
    types_cmd = commands.add_parser(
        "api-types", help="write the frontend's TypeScript types from the API schema"
    )
    types_cmd.add_argument("file", help="e.g. ../frontend/src/schema.ts")

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
                actor = access.system_user(engine)
                if args.dry_run:
                    report(sync.plan(engine, source, entity=args.entity))
                    return
                result = sync.run(
                    engine,
                    source,
                    actor,
                    entity=args.entity,
                    confirm=[m.key for m in sync.plan(engine, source).matches]
                    if args.confirm_matches
                    else (),
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
        case "seed-dev-mappings":
            settings = Settings()  # type: ignore[call-arg]
            if settings.environment == "production":
                raise SystemExit("seed-dev-mappings is refused in production")
            engine = create_app_engine(settings)
            try:
                system = access.system_user(engine)
                for m in sync.default_mappings(settings.source_schema):
                    sync.save_mapping(engine, system, m)
            finally:
                engine.dispose()
            print(f"mapped {', '.join(sync.DATASETS)} to the views in {settings.source_schema}")
        case "republish":
            settings = Settings()  # type: ignore[call-arg]
            engine, output = create_app_engine(settings), create_output_engine(settings)
            try:
                codes = publish.republish_all(
                    engine, output, settings.output_schema, access.system_user(engine)
                )
            finally:
                engine.dispose()
                output.dispose()
            print(f"republished {', '.join(codes) or 'nothing (no publish runs yet)'}")
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
        case "seed-dev-plans":
            settings = Settings()  # type: ignore[call-arg]
            if settings.environment == "production":
                raise SystemExit("seed-dev-plans is refused in production")
            engine = create_app_engine(settings)
            try:
                seeded = devdata.seed_plans(engine, args.round)
            finally:
                engine.dispose()
            for code, owner, status in seeded:
                print(f"{code}: {status} ({owner})")
            print(f"seeded plan values for {len(seeded)} cost center(s) in {args.round}")
        case "api-types":
            from planlayer.api.typescript import app_schema, typescript

            with open(args.file, "w", encoding="utf-8") as f:
                f.write(typescript(app_schema()))
        case "serve":
            uvicorn.run(
                "planlayer.api.app:create_app",
                factory=True,
                host=args.host,
                port=args.port,
                reload=args.reload,
            )
