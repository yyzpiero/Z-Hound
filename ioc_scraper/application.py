from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path

from .discovery.antiy import AntiyDiscovery
from .discovery.browser import BrowserDiscovery
from .discovery.engine import DiscoveryEngine
from .discovery.feed import FeedDiscovery
from .discovery.html import HtmlDiscovery
from .discovery.sitemap import SitemapDiscovery
from .discovery.wechat import WeChatDiscovery
from .discovery.wordpress import WordPressDiscovery
from .extraction.enrichment import AliasCatalog
from .fetcher import BrowserFetcher, HttpFetcher
from .models import SourceRecord
from .persistence.csv_repository import CsvRepository
from .pipeline import CollectorPipeline
from .registry import load_sources


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_ALIAS_PATH = PACKAGE_DIR / "data" / "actor_aliases.csv"
DATA_DIR_ENV = "IOC_SCRAPER_DATA_DIR"


def resolve_data_dir(
    *,
    environ: Mapping[str, str] | None = None,
    package_dir: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    """Return the operator data directory.

    Editable checkouts keep ``<repo>/data``. A non-editable install (Docker
    ``pip install .``) has no pyproject next to the package, so use
    ``$IOC_SCRAPER_DATA_DIR`` or ``<cwd>/data`` (Compose mounts ``./data:/app/data``).
    """
    source = os.environ if environ is None else environ
    explicit = str(source.get(DATA_DIR_ENV, "")).strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    package = Path(package_dir) if package_dir is not None else PACKAGE_DIR
    repo = package.parent
    if (repo / "pyproject.toml").is_file() and (repo / "ioc_scraper").is_dir():
        return (repo / "data").resolve()
    workdir = Path(cwd) if cwd is not None else Path.cwd()
    return (workdir / "data").resolve()


@dataclass(frozen=True)
class ApplicationServices:
    data_dir: Path
    sources: list[SourceRecord]
    alias_catalog: AliasCatalog | None
    repository: CsvRepository | None
    http_fetcher: HttpFetcher
    browser_fetcher: BrowserFetcher
    discovery: DiscoveryEngine
    pipeline: CollectorPipeline | None


def load_catalog(
    data_dir: Path,
    selected_ids: set[int] | None = None,
    selected_strategies: set[str] | None = None,
) -> list[SourceRecord]:
    return load_sources(
        Path(data_dir) / "source.csv",
        selected_ids,
        selected_strategies=selected_strategies,
    )


def build_discovery(http_fetcher, browser_fetcher) -> DiscoveryEngine:
    strategies = [
        FeedDiscovery(),
        SitemapDiscovery(),
        WordPressDiscovery(),
        HtmlDiscovery(),
        WeChatDiscovery(),
        AntiyDiscovery(),
        BrowserDiscovery(browser_fetcher),
    ]
    return DiscoveryEngine(strategies, session=http_fetcher)


def build_services(
    data_dir: Path,
    *,
    selected_ids: set[int] | None = None,
    selected_strategies: set[str] | None = None,
    sources: Sequence[SourceRecord] | None = None,
    proxy: str | None = None,
    progress: Callable[[str], None] | None = None,
    verbosity: int = 0,
    now: Callable[[], datetime] | None = None,
    alias_path: Path | None = None,
    http_fetcher: HttpFetcher | None = None,
    browser_fetcher: BrowserFetcher | None = None,
    with_pipeline: bool = True,
) -> ApplicationServices:
    data_dir = Path(data_dir)
    loaded = (
        list(sources)
        if sources is not None
        else load_catalog(data_dir, selected_ids, selected_strategies)
    )
    proxy_value = default_proxy() if proxy is None else proxy
    http = http_fetcher or HttpFetcher()
    browser = browser_fetcher or BrowserFetcher(proxy=proxy_value)
    discovery = build_discovery(http, browser)
    repository = None
    pipeline = None
    alias_catalog = None
    if with_pipeline:
        repository = CsvRepository(data_dir, now=now) if now is not None else CsvRepository(data_dir)
        alias_catalog = AliasCatalog.from_csv(alias_path or DEFAULT_ALIAS_PATH)
        pipeline = CollectorPipeline(
            registry=loaded,
            discovery=discovery,
            fetcher=http,
            browser_fetcher=browser,
            repository=repository,
            cache_dir=data_dir / "report_cache",
            alias_catalog=alias_catalog,
            progress=progress,
            verbosity=verbosity,
        )
    return ApplicationServices(
        data_dir=data_dir,
        sources=loaded,
        alias_catalog=alias_catalog,
        repository=repository,
        http_fetcher=http,
        browser_fetcher=browser,
        discovery=discovery,
        pipeline=pipeline,
    )


def default_proxy() -> str:
    return os.getenv("IOC_SCRAPER_PROXY", "")
