# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `feed-builder diff OLD NEW` compares two generated feeds (Google XML or Meta CSV) and reports added, removed and changed items with per-field old and new values. Supports `--format json`, `--ignore`, `--limit`, `--output` and `--exit-code`.

## [0.1.0] - 2026-11-09

### Added
- `feed-builder build` command producing Google Merchant Center RSS 2.0 XML and Meta catalog CSV feeds.
- Read-only WooCommerce REST API source (products and variations, pagination) and Shopify Admin API source (variants, `Link` header pagination), with credentials from environment variables.
- CSV source with column aliases and overrides.
- YAML field mapping with a template language and filters, defaults, availability mapping, custom labels and filtering rules.
- Price formatting with currency and minor units, sale price with effective-date intervals, expired-sale handling.
- GTIN check-digit validation, required-field validation, title/description limits, HTML stripping and URL checks.
- Optional image reachability checks.
- Validation report in JSON, CSV or text; `--strict` exit code.
- Incremental output with state file, delta feeds and removed-id lists.
- `init-config` and `gtin` helper commands.
- Test suite with mocked HTTP, ruff configuration and GitHub Actions CI for Python 3.10-3.13.
