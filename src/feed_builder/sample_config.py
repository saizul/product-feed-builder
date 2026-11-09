"""The annotated starter config printed by `feed-builder init-config`."""

SAMPLE_CONFIG = """\
# feed-builder mapping configuration (all keys optional)
currency: BDT                       # ISO 4217; Shopify/WooCommerce can supply it from the API
store_url: https://shop.example.com # used to turn relative links into absolute URLs
timezone: "+06:00"                  # offset used when printing sale windows

feed:                               # RSS channel header for the Google XML feed
  title: Example Shop
  link: https://shop.example.com
  description: Product feed

defaults:                           # used when the source has no value
  condition: new
  brand: Example Brand
  # google_product_category: "Apparel & Accessories > Clothing"

availability_map:                   # extra source values -> in_stock|out_of_stock|preorder|backorder
  "available now": in_stock
  "ships in 2 weeks": backorder

# Field mapping. A lone {placeholder} keeps the raw value; text around it is interpolated.
# Filters: upper lower title strip strip_html truncate:N default:X prefix:X suffix:X join:SEP replace:A:B
fields:
  title: "{title|truncate:150}"
google:
  fields:
    id: "{id}"
meta:
  fields:
    id: "shop_{id}"

custom_labels:
  0:
    default: regular
    rules:
      - value: sale
        when: {field: on_sale, op: eq, value: true}
  1:
    rules:
      - value: premium
        when: {field: price, op: gte, value: 5000}

filters:
  exclude_out_of_stock: true
  exclude_categories: [Uncategorized, Gift Cards]
  # include_categories: [Shirts]
  # min_price: 100
  exclude_if:
    - {field: title, op: regex, value: "^(test|sample)"}

limits:
  on_exceed: truncate               # or: error

strip_html: true
sale:
  drop_expired: true
gtin:
  on_invalid: drop                  # drop | error | keep
identifier_exists:
  auto: true                        # google: emit identifier_exists=no when no GTIN or brand+MPN
images:
  check: false                      # same as --check-images (HEAD requests)
  timeout: 10
  severity: error                   # error | warning

shipping:                           # optional flat shipping rows
  - {country: BD, service: Standard, price: "60 BDT"}

csv:                                # only for --source csv
  delimiter: ","
  columns:                          # canonical field -> your CSV header
    title: Product Name
    price: Regular Price
"""
