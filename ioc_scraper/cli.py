from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
import logging
from pathlib import Path
import sys

from .application import build_services, load_catalog, resolve_data_dir
from .menu import prompt_menu
from .persistence.migration import migrate_legacy
from .pipeline import CollectOptions
from .registry import ALLOWED_STRATEGIES
from .validation import (
    format_validation_line,
    validate_sources,
    write_validation_csv,
)


class CollectorArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        if getattr(parsed, "command", None) == "collect":
            if parsed.lookback is None and parsed.since is None:
                parsed.lookback = "3m"
        return parsed


def _add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        nargs="+",
        type=int,
        metavar="ID",
        help="limit to one or more source ids from data/source.csv",
    )
    parser.add_argument(
        "--strategy",
        nargs="+",
        choices=sorted(ALLOWED_STRATEGIES),
        metavar="NAME",
        help="limit to sources with these strategies, for example wechat",
    )


def format_source_line(index: int, total: int, source) -> str:
    return (
        f"[{index}/{total}] id={source.id} strategy={source.strategy} "
        f"name={source.name} url={source.url}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = CollectorArgumentParser(prog="ioc-scraper")
    commands = parser.add_subparsers(dest="command", required=False)
    collect = commands.add_parser("collect", help="discover reports and extract IoCs")
    cutoff = collect.add_mutually_exclusive_group()
    cutoff.add_argument("--lookback", metavar="MONTHS")
    cutoff.add_argument("--since", metavar="YYYY-MM-DD")
    _add_selection_args(collect)
    collect.add_argument("--max-pages", type=int)
    collect.add_argument("--max-reports", type=int)
    collect.add_argument("--refresh", action="store_true")
    collect.add_argument("--dry-run", action="store_true")
    output = collect.add_mutually_exclusive_group()
    output.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="add report detail; use -vv for extraction counts",
    )
    output.add_argument(
        "--quiet", action="store_true", help="suppress progress lines"
    )
    collect.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    validate = commands.add_parser(
        "validate-sources",
        help="check source discovery without extracting or persisting IoCs",
    )
    _add_selection_args(validate)
    validate.add_argument("--max-pages", type=int)
    validate.add_argument("--max-reports", type=int)
    validate.add_argument("--output", type=Path)
    validate.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    listed = commands.add_parser(
        "list-sources",
        help="print selectable sources without fetching or extracting",
    )
    _add_selection_args(listed)
    listed.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    dashboard = commands.add_parser(
        "dashboard",
        help="run the local analyst dashboard",
    )
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8080)
    dashboard.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def _project_data_dir() -> Path:
    return resolve_data_dir()


def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


def _selected_ids(args) -> set[int] | None:
    return set(args.source or []) or None


def _selected_strategies(args) -> set[str] | None:
    return set(args.strategy or []) or None


def _apply_limits(sources, max_pages: int | None, max_reports: int | None):
    if max_pages is None and max_reports is None:
        return sources
    return [
        replace(
            source,
            max_pages=max_pages if max_pages is not None else source.max_pages,
            max_reports=(
                max_reports if max_reports is not None else source.max_reports
            ),
        )
        for source in sources
    ]


def _serve_dashboard(host: str, port: int) -> int:
    from .dashboard.app import serve

    return serve(host=host, port=port)


def main(argv: Sequence[str] | None = None) -> int:
    requested = list(argv) if argv is not None else sys.argv[1:]
    if not requested:
        if not _stdin_is_interactive():
            build_parser().print_help(sys.stderr)
            return 2
        requested = prompt_menu()
        if requested is None:
            return 0
    parser = build_parser()
    args = parser.parse_args(requested)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    if args.command == "dashboard":
        try:
            return _serve_dashboard(args.host, args.port)
        except ImportError:
            print(
                "Dashboard extra is not installed. Run: pip install -e '.[dashboard]'",
                file=sys.stderr,
            )
            return 2
    max_pages = getattr(args, "max_pages", None)
    max_reports = getattr(args, "max_reports", None)
    if max_pages is not None and max_pages <= 0:
        parser.error("--max-pages must be positive")
    if max_reports is not None and max_reports <= 0:
        parser.error("--max-reports must be positive")
    data_dir = _project_data_dir()
    try:
        selected = _selected_ids(args)
        strategies = _selected_strategies(args)
        sources = load_catalog(data_dir, selected, strategies)
        if args.command == "list-sources":
            for index, source in enumerate(sources, start=1):
                print(format_source_line(index, len(sources), source))
            return 0
        sources = _apply_limits(sources, max_pages, max_reports)
        if args.command == "validate-sources":
            services = build_services(
                data_dir,
                sources=sources,
                with_pipeline=False,
            )
            rows = validate_sources(sources, services.discovery)
            for index, row in enumerate(rows, start=1):
                print(format_validation_line(index, len(rows), row))
            if args.output is not None:
                write_validation_csv(args.output, rows)
                print(f"Validation CSV: {args.output}")
            return 0
        if not args.dry_run and not (
            (data_dir / "reports.csv").exists()
            and (data_dir / "indicators.csv").exists()
        ):
            migrate_legacy(data_dir)
        services = build_services(
            data_dir,
            sources=sources,
            progress=print,
            verbosity=-1 if args.quiet else args.verbose,
        )
        summary = services.pipeline.collect(
            CollectOptions(
                lookback=args.lookback,
                since=args.since,
                selected_source_ids=frozenset(source.id for source in sources)
                if selected or strategies
                else frozenset(),
                max_pages=max_pages,
                max_reports=max_reports,
                refresh=args.refresh,
                dry_run=args.dry_run,
            )
        )
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    except Exception as exc:
        logging.error("collection could not start or persist results: %s", exc)
        return 1
    status_text = ", ".join(
        f"{key}={value}" for key, value in sorted(summary.source_status_counts.items())
    ) or "none"
    print(
        "Collection summary: "
        f"sources={summary.attempted_sources}; statuses={status_text}; "
        f"reports={summary.discovered_reports}; old_skipped={summary.skipped_old_reports}; "
        f"undated={summary.included_undated_reports}; indicators_added={summary.added_indicators}; "
        f"observed={summary.observed_indicators}; review={summary.review_indicators}; "
        f"duplicates={summary.duplicate_indicators}; failures={summary.failures}; "
        f"run={summary.run_id}"
    )
    return 0
