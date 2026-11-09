"""Command line interface."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from rich.console import Console
from rich.table import Table

from . import __version__
from .channels import CHANNELS
from .config import Config, load_config
from .dates import parse_dt
from .gtin import validate_gtin
from .images import check_images
from .models import FeedBuilderError, Product, SourceError
from .pipeline import BuildResult, build_feeds
from .report import write_report
from .sample_config import SAMPLE_CONFIG
from .sources import csv_source, shopify, woocommerce
from .state import compute_delta, hashes, load_state, save_state
from .writers import render_channel, write_if_changed


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="feed-builder",
        description="Build Google Merchant Center and Meta catalog feeds from WooCommerce, Shopify or CSV.",
    )
    p.add_argument("--version", action="version", version="feed-builder " + __version__)
    sub = p.add_subparsers(dest="command")

    b = sub.add_parser("build", help="read products and write feeds")
    b.add_argument("--source", required=True, choices=["woocommerce", "shopify", "csv"])
    b.add_argument("--input", help="CSV file (for --source csv)")
    b.add_argument("--store-url", help="public store URL (overrides config / WC_STORE_URL)")
    b.add_argument("--shop", help="Shopify shop domain, e.g. your-store.myshopify.com (or SHOPIFY_SHOP)")
    b.add_argument("--config", help="YAML mapping config")
    b.add_argument("--channels", default="google,meta", help="comma list: google, meta (default both)")
    b.add_argument("--out", default="feeds", help="output directory (default feeds)")
    b.add_argument("--currency", help="ISO 4217 code, overrides the config")
    b.add_argument("--check-images", action="store_true", help="HEAD-check every image URL")
    b.add_argument("--incremental", action="store_true", help="track published items and also write delta files")
    b.add_argument("--state", help="state file for --incremental (default <out>/.feed-state.json)")
    b.add_argument("--report", help="validation report path (.json, .csv or .txt; default <out>/report.json)")
    b.add_argument("--include-invalid", action="store_true", help="keep records that have errors in the feed")
    b.add_argument("--strict", action="store_true", help="exit with status 1 if any record has an error")
    b.add_argument("--max-products", type=int, help="stop after N products (useful for trials)")
    b.add_argument("--now", help="override the current time (ISO 8601), for reproducible sale-window handling")
    b.add_argument("--quiet", action="store_true")

    ic = sub.add_parser("init-config", help="print or write an annotated starter config")
    ic.add_argument("--output", help="write to a file instead of stdout")

    g = sub.add_parser("gtin", help="validate GTIN/UPC/EAN codes")
    g.add_argument("codes", nargs="+")
    return p


def _load_products(args, cfg: Config, console: Console) -> List[Product]:
    if args.source == "csv":
        if not args.input:
            raise FeedBuilderError("--input is required for --source csv")
        src = csv_source.CsvSource(args.input, cfg.csv_columns, cfg.csv_delimiter, cfg.timezone)
        products = _take(src.fetch(), args.max_products)
    elif args.source == "woocommerce":
        client, store = woocommerce.client_from_env()
        store = args.store_url or store
        cfg.store_url = cfg.store_url or store
        wsrc = woocommerce.WooCommerceSource(
            client, store,
            per_page=int(cfg.woo.get("per_page", 100)),
            include_variations=bool(cfg.woo.get("include_variations", True)),
            status=str(cfg.woo.get("status", "publish")),
        )
        if not cfg.currency:
            cfg.currency = wsrc.fetch_currency()
        products = _take(wsrc.fetch(), args.max_products)
    else:
        client, shop = shopify.client_from_env(shop=args.shop or "")
        ssrc = shopify.ShopifySource(
            client, shop, store_url=args.store_url or cfg.store_url,
            api_version=str(cfg.shopify.get("api_version") or os.environ.get("SHOPIFY_API_VERSION")
                            or shopify.DEFAULT_API_VERSION),
            status=str(cfg.shopify.get("status", "active")),
        )
        cfg.store_url = cfg.store_url or ssrc.store_url
        if not cfg.currency:
            cfg.currency = ssrc.fetch_currency()
        products = _take(ssrc.fetch(), args.max_products)
    return products


def _take(it, limit: Optional[int]) -> List[Product]:
    out = []
    for p in it:
        out.append(p)
        if limit and len(out) >= limit:
            break
    return out


def _print_summary(result: BuildResult, console: Console) -> None:
    table = Table(title="Feed summary")
    for col in ("Channel", "In feed", "Rejected", "Errors", "Warnings"):
        table.add_column(col)
    for name, ch in result.channels.items():
        table.add_row(CHANNELS[name].label, str(len(ch.records)), str(len(ch.invalid)), str(len(ch.errors)), str(len(ch.warnings)))
    console.print(table)
    console.print("products read: %d, filtered out: %d" % (result.total_input, len(result.filtered)))


def run_build(args, console: Console, image_check: Optional[Callable] = None) -> int:
    cfg = load_config(args.config)
    if args.currency:
        cfg.currency = args.currency.upper()
    if args.store_url:
        cfg.store_url = args.store_url.rstrip("/")
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    if not channels or any(c not in CHANNELS for c in channels):
        raise FeedBuilderError("--channels must be a comma list of: %s" % ", ".join(CHANNELS))
    now = datetime.now(timezone.utc)
    if args.now:
        now = parse_dt(args.now) or now
        if not parse_dt(args.now):
            raise FeedBuilderError("--now must be an ISO 8601 date/time")

    products = _load_products(args, cfg, console)
    if not products:
        raise SourceError("the source returned no products")
    if not cfg.currency and not any(p.currency for p in products):
        raise FeedBuilderError("no currency: set `currency` in the config or pass --currency (e.g. BDT)")

    checker = image_check
    if checker is None and (args.check_images or cfg.image_check):
        timeout = cfg.image_timeout
        checker = lambda urls: check_images(urls, timeout=timeout)  # noqa: E731
    result = build_feeds(products, cfg, channels, now=now, include_invalid=args.include_invalid, image_checker=checker)

    os.makedirs(args.out, exist_ok=True)
    state_path = args.state or os.path.join(args.out, ".feed-state.json")
    state = load_state(state_path) if args.incremental else {}
    new_state: Dict[str, Dict[str, str]] = dict(state)
    for name, ch in result.channels.items():
        spec = CHANNELS[name]
        path = os.path.join(args.out, spec.file_name)
        changed = write_if_changed(path, render_channel(name, ch.records, cfg))
        console.print("%s %s (%d products)" % ("wrote" if changed else "unchanged", path, len(ch.records)))
        if args.incremental:
            delta = compute_delta(state.get(name, {}), ch.records)
            upserts = set(delta.upserts)
            dpath = os.path.join(args.out, spec.delta_file_name)
            write_if_changed(dpath, render_channel(name, [r for r in ch.records if r.product_id in upserts], cfg))
            rpath = os.path.join(args.out, "removed-%s.txt" % name)
            write_if_changed(rpath, ("\n".join(delta.removed) + ("\n" if delta.removed else "")).encode("utf-8"))
            console.print("  delta: %d new, %d changed, %d unchanged, %d removed" % (
                len(delta.new), len(delta.changed), len(delta.unchanged), len(delta.removed)))
            new_state[name] = hashes(ch.records)
    if args.incremental:
        save_state(state_path, new_state)

    report_path = args.report or os.path.join(args.out, "report.json")
    write_report(result, report_path)
    console.print("report: %s" % report_path)
    _print_summary(result, console)
    errors = sum(len(ch.errors) for ch in result.channels.values())
    if args.strict and errors:
        console.print("[red]%d error(s) found (--strict)[/red]" % errors)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(stderr=True, quiet=getattr(args, "quiet", False))
    if args.command == "gtin":
        bad = 0
        for code in args.codes:
            ok, why = validate_gtin(code)
            print("%s\t%s" % (code, "valid" if ok else "invalid: " + why))
            bad += 0 if ok else 1
        return 1 if bad else 0
    if args.command == "init-config":
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE_CONFIG)
            console.print("wrote %s" % args.output)
        else:
            sys.stdout.write(SAMPLE_CONFIG)
        return 0
    if args.command == "build":
        try:
            return run_build(args, console)
        except FeedBuilderError as exc:
            console.print("[red]Error:[/red] %s" % exc)
            return 2
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
