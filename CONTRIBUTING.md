# Contributing

Thanks for helping improve product-feed-builder.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check .
pytest
```

CI runs both on Python 3.10, 3.11, 3.12 and 3.13.

## Where things live

- `sources/` converts a store or file into canonical `Product` objects.
- `channels.py` holds the per-channel specs (field order, required fields, limits).
- `pipeline.py` maps, formats and validates products into records.
- `writers.py` serialises records (Google XML, Meta CSV).

## Guidelines

- **Feed specs change.** When you change a limit or required field, link the official Google or Meta documentation in your pull request.
- Sources must stay **read-only**: only `GET` requests, no write endpoints, no secrets on the command line or in logs.
- Tests must not use the network. Use `tests/fakes.py`; extend it rather than adding a new HTTP mocking dependency.
- Use synthetic data only (`example.com`, invented products). Never commit real catalogs, keys or tokens.
- New validation rules need both a passing and a failing test, and a stable `code` string.

## Reporting bugs

Open an issue with the command you ran, a minimal anonymised input (a few CSV rows or an API response) and the report output.
