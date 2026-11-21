"""The ``planlayer`` command."""

import argparse
import getpass
import logging
import os
from datetime import date
from pathlib import Path

import uvicorn

from planlayer import access, backup, devdata, publish, rounds, sync
from planlayer.adapters.source import PostgresSource
from planlayer.config import Settings
from planlayer.login import local
from planlayer.login import settings as login_settings
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
        window = f"fiscal years {p.window.first}-{p.window.last}" if p.window else "every year"
        print(f"actual cells: {p.actual_cells} ({window})")
        for y in p.years:
            print(f"  {y.fiscal_year}: {y.stored} stored, {y.delivered} delivered")


def _years(value: str) -> tuple[int, int]:
    first, _, last = value.partition("-")
    try:
        return int(first), int(last or first)
    except ValueError:
        raise argparse.ArgumentTypeError("a year or a range, e.g. 2025-2027") from None


def _require_demo(settings: Settings, command: str) -> None:
    """Test data only goes into a demo or a development installation (ADR 0030)."""
    if not (settings.demo or settings.development):
        raise SystemExit(f"{command} needs PLANLAYER_DEMO=true or a development environment")


def main(argv: list[str] | None = None) -> None:
    # Planlayer's own messages (e.g. sync steps and their times) next to the server's.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="planlayer")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate_cmd = commands.add_parser("migrate", help="upgrade the app store schema")
    migrate_cmd.add_argument("--revision", default="head")
    migrate_cmd.add_argument(
        "--without-backup",
        action="store_true",
        help="upgrade an existing app store without taking a backup first",
    )

    commands.add_parser("backup", help="back up the app store into PLANLAYER_BACKUP_DIR")
    restore_cmd = commands.add_parser(
        "restore", help="load a backup into the empty app schema, then republish the output"
    )
    restore_cmd.add_argument("file", type=Path, help="a planlayer-….dump file")

    sync_cmd = commands.add_parser("sync", help="sync master data and actuals from the source")
    sync_cmd.add_argument("--entity", help="only this entity's actuals")
    sync_cmd.add_argument(
        "--years",
        type=_years,
        help="fiscal years of actuals to replace, e.g. 2025-2027 or 2026 (default: the years the "
        "setup and open rounds compare with)",
    )
    sync_cmd.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    sync_cmd.add_argument(
        "--confirm-matches",
        action="store_true",
        help="let the source take over manual members with the same codes",
    )

    commands.add_parser(
        "seed-dev-mappings",
        help="map the datasets to the test warehouse's views (demo or development)",
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
        "seed-dev-users", help="create the development users of the test data (demo or development)"
    )

    plans_cmd = commands.add_parser(
        "seed-dev-plans",
        help="plan values for some cost centers, as their owners began planning (development)",
    )
    plans_cmd.add_argument("--round", default="B2026", help="the planning round (default B2026)")
    forwarding_cmd = commands.add_parser(
        "seed-dev-forwarding",
        help="the test company's cost forwardings in a round (development)",
    )
    forwarding_cmd.add_argument("--round", required=True, help="the planning round")
    workforce_cmd = commands.add_parser(
        "seed-dev-workforce",
        help="positions from HR, replacements and new positions in a round (development)",
    )
    workforce_cmd.add_argument("--round", required=True, help="the planning round")
    capex_cmd = commands.add_parser(
        "seed-dev-capex",
        help="investment types and the test company's investments in a round (development)",
    )
    capex_cmd.add_argument("--round", default="B2026", help="the planning round (default B2026)")
    notes_cmd = commands.add_parser(
        "seed-dev-notes",
        help="notes on the seeded sheets and investments of a round (development)",
    )
    notes_cmd.add_argument("--round", default="B2026", help="the planning round (default B2026)")
    commands.add_parser(
        "seed-demo",
        help="a demo from the Nordwerk test warehouse, into an empty app store (demo only)",
    )
    commands.add_parser(
        "setup-code",
        help="a new setup code for the first admin account, while no admin can log in",
    )
    password_cmd = commands.add_parser(
        "set-password",
        help="a local password for a user (from PLANLAYER_PASSWORD or a prompt): the way back in",
    )
    password_cmd.add_argument("login")
    commands.add_parser(
        "seed-dev-item-lists",
        help="item lists on the test company's lines, by several owners (development)",
    )
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
            settings = Settings()  # type: ignore[call-arg]
            # An upgrade is rolled back by the previous image and this backup (ADR 0031).
            if (at := migrate.behind(settings)) is not None and not args.without_backup:
                if settings.backup_dir is None and settings.development:
                    print(f"no backup before upgrading from {at}: PLANLAYER_BACKUP_DIR is unset")
                else:
                    engine = create_app_engine(settings)
                    try:
                        written = backup.backup(settings, engine)
                    except backup.BackupError as e:
                        raise SystemExit(
                            f"no backup, so no upgrade from {at}: {e} "
                            "(or planlayer migrate --without-backup)"
                        ) from None
                    finally:
                        engine.dispose()
                    print(f"backup before upgrading from {at}: {written}")
            migrate.upgrade(settings, args.revision)
        case "backup":
            settings = Settings()  # type: ignore[call-arg]
            engine = create_app_engine(settings)
            try:
                print(backup.backup(settings, engine))
            except backup.BackupError as e:
                raise SystemExit(f"backup failed: {e}") from None
            finally:
                engine.dispose()
        case "restore":
            settings = Settings()  # type: ignore[call-arg]
            engine, output = create_app_engine(settings), create_output_engine(settings)
            try:
                backup.restore(settings, engine, args.file)
                # Migrations bring an older backup up to this version before the republish.
                migrate.upgrade(settings)
                codes = publish.republish_all(
                    engine, output, settings.output_schema, access.system_user(engine)
                )
            except backup.BackupError as e:
                raise SystemExit(f"restore failed: {e}") from None
            finally:
                engine.dispose()
                output.dispose()
            print(f"restored {args.file.name}; republished {', '.join(codes) or 'nothing'}")
        case "sync":
            settings = Settings()  # type: ignore[call-arg]
            engine, source = create_app_engine(settings), PostgresSource(settings.source_url)
            try:
                actor = access.system_user(engine)
                if args.dry_run:
                    report(sync.plan(engine, source, entity=args.entity, years=args.years))
                    return
                result = sync.run(
                    engine,
                    source,
                    actor,
                    entity=args.entity,
                    confirm=[m.key for m in sync.plan(engine, source).matches]
                    if args.confirm_matches
                    else (),
                    years=args.years,
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
            _require_demo(settings, "seed-dev-mappings")
            engine = create_app_engine(settings)
            try:
                system = access.system_user(engine)
                for m in sync.default_mappings(settings.source_schema):
                    sync.save_mapping(engine, system, m)
                # The test warehouse delivers salaries with the staff list (ADR 0025).
                sync.set_pay_from_source(engine, system, True)
            finally:
                engine.dispose()
            print(
                f"mapped {', '.join(sync.DATASETS)} to the views in {settings.source_schema}, "
                "salaries from the source"
            )
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
            _require_demo(settings, "seed-dev-users")
            engine = create_app_engine(settings)
            try:
                created = devdata.seed(engine)
            finally:
                engine.dispose()
            print(f"created {len(created)} user(s): {', '.join(created) or '-'}")
        case "seed-dev-plans":
            settings = Settings()  # type: ignore[call-arg]
            _require_demo(settings, "seed-dev-plans")
            engine = create_app_engine(settings)
            try:
                seeded = devdata.seed_plans(engine, args.round)
            finally:
                engine.dispose()
            for code, owner, status in seeded:
                print(f"{code}: {status} ({owner})")
            print(f"seeded plan values for {len(seeded)} cost center(s) in {args.round}")
        case "seed-dev-forwarding":
            settings = Settings()  # type: ignore[call-arg]
            _require_demo(settings, "seed-dev-forwarding")
            engine = create_app_engine(settings)
            try:
                made = devdata.seed_forwarding(engine, args.round)
            finally:
                engine.dispose()
            print(f"created {len(made)} forwarding(s) in {args.round}: {', '.join(made) or '-'}")
        case "seed-dev-capex":
            settings = Settings()  # type: ignore[call-arg]
            _require_demo(settings, "seed-dev-capex")
            engine = create_app_engine(settings)
            try:
                projects, lines = devdata.seed_capex(engine, args.round)
            finally:
                engine.dispose()
            print(f"created {projects} large project(s) and {lines} investment(s) in {args.round}")
        case "seed-dev-notes":
            settings = Settings()  # type: ignore[call-arg]
            _require_demo(settings, "seed-dev-notes")
            engine = create_app_engine(settings)
            try:
                threads = devdata.seed_notes(engine, args.round)
            finally:
                engine.dispose()
            print(f"created {threads} note thread(s) in {args.round}")
        case "seed-dev-item-lists":
            settings = Settings()  # type: ignore[call-arg]
            _require_demo(settings, "seed-dev-item-lists")
            engine = create_app_engine(settings)
            try:
                lists = devdata.seed_item_lists(engine)
            finally:
                engine.dispose()
            print(f"created {lists} item list(s)")
        case "seed-dev-workforce":
            settings = Settings()  # type: ignore[call-arg]
            _require_demo(settings, "seed-dev-workforce")
            engine = create_app_engine(settings)
            try:
                taken, replaced, new = devdata.seed_workforce(engine, args.round)
            finally:
                engine.dispose()
            print(
                f"{args.round}: {taken} position(s) from HR, {replaced} replacement(s), "
                f"{new} new position(s)"
            )
        case "seed-demo":
            settings = Settings()  # type: ignore[call-arg]
            if not settings.demo:
                raise SystemExit("seed-demo needs PLANLAYER_DEMO=true")
            engine, source = create_app_engine(settings), PostgresSource(settings.source_url)
            try:
                for line in devdata.seed_demo(engine, source, settings.source_schema):
                    print(line)
            except (ValueError, sync.SyncError) as e:
                raise SystemExit(str(e)) from None
            finally:
                engine.dispose()
                source.dispose()
        case "setup-code":
            settings = Settings()  # type: ignore[call-arg]
            engine = create_app_engine(settings)
            try:
                issued = local.ensure_code(engine, login_settings.load(engine)[0], renew=True)
            finally:
                engine.dispose()
            if issued is None:
                raise SystemExit("an admin can log in already; no setup code is needed")
            print(issued)
        case "set-password":
            settings = Settings()  # type: ignore[call-arg]
            password = os.environ.get("PLANLAYER_PASSWORD") or getpass.getpass("Password: ")
            engine = create_app_engine(settings)
            try:
                local.set_password(engine, args.login, password)
            except (ValueError, access.UserError) as e:
                raise SystemExit(str(e)) from None
            finally:
                engine.dispose()
            print(f"password set for {args.login.strip().lower()}")
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
