# product-feed-builder

Build **Google Merchant Center** (RSS 2.0 XML with the `g:` namespace) and **Meta (Facebook/Instagram) catalog** (CSV) product feeds from a WooCommerce store, a Shopify store, or a plain CSV file, with validation that tells you exactly which products would be rejected and why.

Built by [Saizul Amin](https://saizul.com), e-commerce web developer and SEO specialist.

> **Verify against the official specs.** Feed requirements change. The field names, limits and required fields in this tool follow the public Google and Meta specifications at the time of writing. Before relying on a feed in production, check the current docs: [Google Merchant Center product data specification](https://support.google.com/merchants/answer/7052112) and [Meta catalog data specifications](https://www.facebook.com/business/help/120325381656392). Field specs live in `src/feed_builder/channels.py` if you need to adjust them.

## Features

- **Sources**: WooCommerce REST API (read-only, consumer key/secret from environment), Shopify Admin API (token from environment, read-only), or CSV with flexible column names.
- **Channels**: Google Merchant Center XML and Meta catalog CSV, built from one canonical product model.
- **Field mapping in YAML**: per-channel templates such as `"{brand} {title|truncate:120}"` with filters, defaults and nested attribute access.
- **Variants**: WooCommerce variations and Shopify variants become individual items sharing an `item_group_id`, each with its own price, stock, image and link.
- **Prices**: formatted with ISO 4217 currency and the right number of decimals (`1299.00 BDT`, `18.50 USD`, `1500 JPY`); accepts `1,299.00`, `1.299,00`, `৳ 1299` and Bengali digits.
- **Sale prices** with `sale_price_effective_date` intervals in your timezone; expired or inconsistent sales are dropped with a warning.
- **Availability mapping** to each channel's vocabulary (`in_stock` for Google, `in stock` for Meta, `backorder` to `available for order`), extendable in config.
- **GTIN validation** (8/12/13/14 digits, GS1 check digit) with a configurable policy (drop, error or keep) and automatic `identifier_exists=no` for Google.
- **Required-field validation** per channel (plus apparel rules for Google) with an error report in JSON, CSV or text; products with errors are kept out of the feed.
- **Limits**: title and description length limits per channel (truncate at a word boundary or reject), HTML stripped, control characters removed, relative URLs made absolute, additional images capped.
- **Optional image checks**: `--check-images` sends HEAD requests (with a ranged GET fallback) and refuses private/local addresses.
- **Custom labels** (`custom_label_0..4`) from rules, and **filtering rules**: exclude out-of-stock, include/exclude categories, ids, price bounds and arbitrary conditions.
- **Incremental output**: state file with per-item hashes, delta feeds containing only new/changed items, a list of removed ids, and feed files that are only rewritten when their content changes.

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/saizul/product-feed-builder.git
cd product-feed-builder
python -m venv .venv && source .venv/bin/activate
pip install .
```

## Quick start (CSV, no credentials)

```bash
feed-builder build --source csv --input examples/products.csv --config examples/config.yaml --out feeds
```

This writes `feeds/google-merchant-feed.xml`, `feeds/meta-catalog-feed.csv` and `feeds/report.json`. The data in `examples/` is synthetic. Pre-generated results are in [`examples/output`](examples/output).

Generate an annotated starter config with `feed-builder init-config --output feed.yaml`.

## Sources

### WooCommerce

1. In WordPress go to **WooCommerce > Settings > Advanced > REST API > Add key**, choose a user and set permissions to **Read**.
2. Export the credentials (never pass secrets on the command line):

```bash
export WC_STORE_URL="https://shop.example.com"
export WC_CONSUMER_KEY="ck_xxx"
export WC_CONSUMER_SECRET="cs_xxx"
feed-builder build --source woocommerce --config feed.yaml --out feeds
```

Variable products are expanded into variations; `--max-products 20` is handy for a first trial. Credentials are only sent over HTTPS. Only `GET` requests are ever made.

### Shopify

1. In the Shopify admin create a **custom app** (Settings > Apps and sales channels > Develop apps) and grant only the `read_products` Admin API scope.
2. Install the app and copy the Admin API access token.

```bash
export SHOPIFY_SHOP="your-store.myshopify.com"
export SHOPIFY_ADMIN_TOKEN="shpat_xxx"
feed-builder build --source shopify --store-url https://shop.example.com --config feed.yaml --out feeds
```

`--store-url` is the public storefront used for product links. The API version defaults to `2024-10`; override it with `shopify.api_version` in the config or `SHOPIFY_API_VERSION` (check Shopify's current supported versions). A variant with `compare_at_price` above its price becomes `price` plus `sale_price`.

### CSV

Column names are matched case-insensitively against common aliases (`sku`/`id`, `name`/`title`, `url`/`link`, `image`, `regular_price`/`price`, `ean`/`gtin`, ...). Override them in the config:

```yaml
csv:
  columns:
    id: SKU
    title: Product Name
    price: Regular Price
```

Unmapped columns are available in templates as `{extra.column_name}`.

## Configuration

See [`examples/config.yaml`](examples/config.yaml) and the output of `feed-builder init-config`. Top-level keys:

| Key | Purpose |
| --- | --- |
| `currency`, `store_url`, `timezone` | Currency code (BDT, USD, ...), base URL for relative links, offset for sale windows (`+06:00`) |
| `defaults` | Fallbacks for `condition`, `brand`, `google_product_category`, `gender`, `age_group`, `color`, ... |
| `availability_map` | Extra source values mapped to `in_stock`, `out_of_stock`, `preorder`, `backorder` |
| `fields` / `google.fields` / `meta.fields` | Feed field to template mapping (common, then per channel) |
| `custom_labels` | Rules producing `custom_label_0..4` |
| `filters` | `exclude_out_of_stock`, `include_categories`, `exclude_categories`, `exclude_ids`, `min_price`, `max_price`, `exclude_if` |
| `limits.on_exceed` | `truncate` (default) or `error` when titles/descriptions exceed channel limits |
| `gtin.on_invalid` | `drop` (default), `error` or `keep` |
| `sale.drop_expired` | Drop sale prices whose window already ended (default true) |
| `images` | `check`, `timeout`, `severity` for image URL checks |
| `shipping` | Optional flat shipping rows added to every item |

Template filters: `upper lower title strip strip_html truncate:N default:X prefix:X suffix:X join:SEP replace:A:B`. A lone `{field}` keeps the raw value (lists stay lists); text around placeholders is interpolated.

Template fields: `id title description link image_link additional_images price sale_price sale_dates on_sale effective_price currency availability stock_quantity condition brand gtin mpn sku item_group_id google_product_category product_type categories category tags attributes.<name> weight extra.<name> custom_label_0..4`.

Conditions (filters and custom labels) use `{field, op, value}` with ops `eq ne gt gte lt lte in not_in contains intersects regex empty not_empty`, combinable with `all`, `any` and `not`.

## Validation and reports

Every issue has a severity, code, field and message:

- **error**: the product is left out of the feed (use `--include-invalid` to keep it). Examples: `missing_required`, `price_invalid`, `availability_invalid`, `url_invalid`, `image_placeholder`, `duplicate_id`, `too_long`, `image_unreachable`.
- **warning**: the feed is still produced. Examples: `gtin_invalid` (dropped), `sale_expired`, `truncated`, `title_all_caps`, `enum_invalid`.

The report goes to `<out>/report.json` by default, or to `--report path.csv|.json|.txt`. `--strict` makes the exit code 1 when any error exists, which is useful in cron jobs and CI.

## Incremental output

```bash
feed-builder build --source woocommerce --config feed.yaml --out feeds --incremental
```

On each run the tool compares every item against `feeds/.feed-state.json` and writes, next to the full feeds, `*.delta.xml` / `*.delta.csv` with only new and changed items, plus `removed-<channel>.txt` listing ids that disappeared. Full feeds are rewritten only when their bytes change, so file timestamps stay stable for fetchers that rely on them.

## Setting up the feeds

Host the generated files at a stable HTTPS URL (a static path on your web server is enough) and refresh them with cron or a CI job.

### Google Merchant Center

1. Sign in at Merchant Center, complete business information and verify/claim your website.
2. Go to **Products > Data sources > Add product source**, choose the country and language, then **Add products from a file**.
3. Pick **Scheduled fetch**, enter the HTTPS URL of `google-merchant-feed.xml`, set the fetch time and (if protected) credentials. File type is XML.
4. Wait for processing, then open the data source's diagnostics and fix any item-level issues reported there.
5. Add shipping and tax settings in Merchant Center (or via the `shipping` config rows) so listings can be approved.

### Meta Commerce Manager

1. In Commerce Manager create (or open) a **catalog** and make sure your Business portfolio owns it.
2. Go to **Catalog > Data sources > Add items > Data feed**.
3. Choose **Scheduled feed**, enter the HTTPS URL of `meta-catalog-feed.csv`, set the currency, frequency and credentials if needed. For a one-off test pick **Upload files** instead.
4. After the first upload, review **Diagnostics** for item errors and warnings.
5. If you use `--incremental`, check your data source's update options to see whether the delta file can be used as an update-only feed.

Menu names change over time; follow the current official guides if the labels above differ.

## Safety

- Source clients expose **GET only**; there is no code path that writes to your store.
- API secrets come from environment variables only and are never logged or written to reports.
- Credentials are refused over plain HTTP (except `localhost`).
- Image checks refuse private and loopback addresses.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

Tests never touch the network: HTTP is mocked with the fake session in `tests/fakes.py`. See [CONTRIBUTING.md](CONTRIBUTING.md). Released under the [MIT License](LICENSE).
