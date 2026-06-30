"""
Synthetic sample data generator for the AI Export Intelligence Pipeline.

Running this script writes ``data/sample/leads.csv`` with 20 synthetic export
lead records plus a header row.  The data is fully fictional: company names are
invented and every email uses the reserved ``.example`` domain so no real
mailbox can ever be contacted.

The output is intentionally crafted to exercise the pipeline's data paths:

* 18 rows are schema-valid (one of which is an exact business-identity
  duplicate, to exercise idempotency);
* 1 row is missing ``contact_email`` (schema-invalid);
* 1 row is missing ``product_category`` (schema-invalid).

The script is deterministic and self-contained: standard library only, no
random values, no network calls, no API keys and no database connection.  It
can be run from the repository root with::

    python data/sample/generate_sample_data.py

and it creates or overwrites ``data/sample/leads.csv`` in place.
"""

from __future__ import annotations

import csv
from pathlib import Path

# Stable column order for the generated CSV.  Mirrors the columns understood by
# ``RawLeadSchema`` / ``csv_ingestion.py`` (required: company_name,
# contact_email, product_category; optional: contact_phone, annual_revenue,
# target_market).
FIELDNAMES = (
    "company_name",
    "contact_email",
    "contact_phone",
    "product_category",
    "annual_revenue",
    "target_market",
)

# Path to the CSV this script generates, resolved relative to this file so the
# script works regardless of the current working directory.
OUTPUT_PATH = Path(__file__).resolve().parent / "leads.csv"

# 20 synthetic rows.  Index 17 is an exact business-identity duplicate of index
# 0 (same company_name, contact_email, product_category, target_market).  The
# final two rows are schema-invalid: one missing contact_email, one missing
# product_category.  product_category and target_market values are varied.
ROWS: tuple[dict[str, str], ...] = (
    {
        "company_name": "Aurora Industrial Coatings",
        "contact_email": "export@aurora-industrial.example",
        "contact_phone": "+90 212 555 0101",
        "product_category": "Industrial Coatings",
        "annual_revenue": "4200000",
        "target_market": "EU",
    },
    {
        "company_name": "Bosphorus Textile Exports",
        "contact_email": "sales@bosphorus-textiles.example",
        "contact_phone": "+90 212 555 0102",
        "product_category": "Textiles",
        "annual_revenue": "1875000",
        "target_market": "Middle East",
    },
    {
        "company_name": "Cedar Valley Organics",
        "contact_email": "trade@cedarvalley-organics.example",
        "contact_phone": "+1 503 555 0103",
        "product_category": "Organic Foods",
        "annual_revenue": "990000",
        "target_market": "North America",
    },
    {
        "company_name": "Delta Marine Components",
        "contact_email": "info@delta-marine.example",
        "contact_phone": "+65 6555 0104",
        "product_category": "Marine Equipment",
        "annual_revenue": "6350000",
        "target_market": "Southeast Asia",
    },
    {
        "company_name": "Everest Mining Supplies",
        "contact_email": "export@everest-mining.example",
        "contact_phone": "+27 11 555 0105",
        "product_category": "Mining Equipment",
        "annual_revenue": "12800000",
        "target_market": "Africa",
    },
    {
        "company_name": "Fjord Seafood Trading",
        "contact_email": "sales@fjord-seafood.example",
        "contact_phone": "+47 21 555 0106",
        "product_category": "Seafood",
        "annual_revenue": "2450000",
        "target_market": "EU",
    },
    {
        "company_name": "Granite Stone Works",
        "contact_email": "export@granite-stoneworks.example",
        "contact_phone": "+971 4 555 0107",
        "product_category": "Construction Materials",
        "annual_revenue": "3100000",
        "target_market": "Middle East",
    },
    {
        "company_name": "Helios Solar Systems",
        "contact_email": "trade@helios-solar.example",
        "contact_phone": "+55 11 555 0108",
        "product_category": "Renewable Energy",
        "annual_revenue": "5600000",
        "target_market": "South America",
    },
    {
        "company_name": "Indus Spice Company",
        "contact_email": "export@indus-spice.example",
        "contact_phone": "+91 22 555 0109",
        "product_category": "Spices",
        "annual_revenue": "780000",
        "target_market": "EU",
    },
    {
        "company_name": "Jade Ceramics Manufacturing",
        "contact_email": "sales@jade-ceramics.example",
        "contact_phone": "+1 415 555 0110",
        "product_category": "Ceramics",
        "annual_revenue": "1320000",
        "target_market": "North America",
    },
    {
        "company_name": "Kismet Leather Goods",
        "contact_email": "export@kismet-leather.example",
        "contact_phone": "+90 232 555 0111",
        "product_category": "Leather Goods",
        "annual_revenue": "640000",
        "target_market": "Middle East",
    },
    {
        "company_name": "Lumen Lighting Industries",
        "contact_email": "info@lumen-lighting.example",
        "contact_phone": "+65 6555 0112",
        "product_category": "Electronics",
        "annual_revenue": "8900000",
        "target_market": "Southeast Asia",
    },
    {
        "company_name": "Meridian Pharma Exports",
        "contact_email": "trade@meridian-pharma.example",
        "contact_phone": "+27 21 555 0113",
        "product_category": "Pharmaceuticals",
        "annual_revenue": "15400000",
        "target_market": "Africa",
    },
    {
        "company_name": "Nordic Timber Group",
        "contact_email": "export@nordic-timber.example",
        "contact_phone": "+46 8 555 0114",
        "product_category": "Timber",
        "annual_revenue": "2750000",
        "target_market": "EU",
    },
    {
        "company_name": "Oasis Beverage Co",
        "contact_email": "sales@oasis-beverage.example",
        "contact_phone": "+971 2 555 0115",
        "product_category": "Beverages",
        "annual_revenue": "1980000",
        "target_market": "Middle East",
    },
    {
        "company_name": "Pioneer Auto Parts",
        "contact_email": "export@pioneer-autoparts.example",
        "contact_phone": "+1 313 555 0116",
        "product_category": "Automotive Parts",
        "annual_revenue": "7200000",
        "target_market": "North America",
    },
    {
        "company_name": "Quartz Glass Works",
        "contact_email": "trade@quartz-glass.example",
        "contact_phone": "+55 21 555 0117",
        "product_category": "Glassware",
        "annual_revenue": "1150000",
        "target_market": "South America",
    },
    # Index 17 — exact business-identity duplicate of index 0 (Aurora).
    # company_name, contact_email, product_category and target_market match,
    # so this row produces the same idempotency key as the first row.
    {
        "company_name": "Aurora Industrial Coatings",
        "contact_email": "export@aurora-industrial.example",
        "contact_phone": "+90 212 555 0118",
        "product_category": "Industrial Coatings",
        "annual_revenue": "4350000",
        "target_market": "EU",
    },
    # Index 18 — schema-invalid: missing contact_email.
    {
        "company_name": "Summit Grain Traders",
        "contact_email": "",
        "contact_phone": "+1 312 555 0119",
        "product_category": "Grain",
        "annual_revenue": "3300000",
        "target_market": "Africa",
    },
    # Index 19 — schema-invalid: missing product_category.
    {
        "company_name": "Tempest Wind Turbines",
        "contact_email": "export@tempest-wind.example",
        "contact_phone": "+49 30 555 0120",
        "product_category": "",
        "annual_revenue": "9600000",
        "target_market": "EU",
    },
)


def write_sample_csv(output_path: Path = OUTPUT_PATH) -> Path:
    """Write the synthetic leads CSV to ``output_path`` and return the path.

    The file is created or overwritten with a header row followed by the 20
    fixed rows defined in :data:`ROWS`, using a stable column order.
    """
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(ROWS)
    return output_path


def main() -> None:
    """Generate the sample CSV and print a short summary."""
    output_path = write_sample_csv()

    missing_email = sum(1 for row in ROWS if not row["contact_email"])
    missing_category = sum(1 for row in ROWS if not row["product_category"])

    print(f"Wrote {len(ROWS)} data rows + header to {output_path}")
    print(f"  rows missing contact_email   : {missing_email}")
    print(f"  rows missing product_category: {missing_category}")
    print("  business-identity duplicates : 1 (row 18 duplicates row 1)")


if __name__ == "__main__":
    main()
