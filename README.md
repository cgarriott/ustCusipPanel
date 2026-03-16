# ustCusipPanel

**U.S. Treasury CUSIP Panel Data Generator**

A Python package that creates from public sources a complete, CUSIP-date level panel dataset of US Treasury bond metadata.

## Features

- **Complete**: Business date completion (weekends removed but not holidays in this version)
- **Tenor classifications**: Automatic classification of bills (weeks), notes (years), and bonds (years)
- **Vintage tracking**: Ordinal rankings by first issue date within date-tenor groups
    - 0 for on-the-run, 1 for first off-the-run, 2 for second off the run; -1 for when-issued
- **Cumulative issuance**: Track total issued amounts over time
- **Auction markers**: Identify openings, re-openings, and unscheduled re-openings
- **Caching**: Smart local caching with partial date range merging — only missing ranges are fetched
- **Incremental updates**: `updateUstCusipPanel()` updates an existing panel by fetching only new auction data

## Installation

```bash
git clone https://github.com/cgarriott/ustCusipPanel.git
cd ustCusipPanel
pip install -e .
```

## Quick Start

```python
import ustCusipPanel

# Generate panel with default parameters (1990-01-01 to today)
df = ustCusipPanel.ustCusipPanel()

# Custom date range
df = ustCusipPanel.ustCusipPanel(
    startDate="2020-01-01",
    endDate="2023-12-31"
)

# Suppress summary statistics
df = ustCusipPanel.ustCusipPanel(silent=True)

# Force fresh download (ignore cache)
df = ustCusipPanel.ustCusipPanel(forceDownload=True)

# Incrementally update a saved panel parquet file
ustCusipPanel.updateUstCusipPanel("treasury_panel.parquet")

# Or update a DataFrame in memory and get the result
df_updated = ustCusipPanel.updateUstCusipPanel(df)
```

## Usage Examples

### Get On-The-Run Securities (Vintage 0)

```python
import polars as pl

df = ustCusipPanel.ustCusipPanel()

# Filter for on-the-run 10-year notes
otr_10y = df.filter(
    (pl.col('tenor') == 10) & 
    (pl.col('vintage') == 0)
)
```

### Analyze Auction Activity

```python
# Get all auction dates for 5-year notes
auctions_5y = df.filter(
    (pl.col('tenor') == 5) & 
    (pl.col('auction').is_not_null())
)

# Count auctions by type
auction_summary = auctions_5y.group_by('auction').agg(
    pl.count().alias('count')
)
```

### Track Issuance Over Time

```python
# Get cumulative issuance for a specific CUSIP
cusip_history = df.filter(
    pl.col('cusip') == '912828Z29'
).select(['date', 'totalIssued', 'auction'])
```

### Compare Tenors

```python
# Average number of active securities by tenor
tenor_summary = df.group_by(['date', 'tenor']).agg(
    pl.col('cusip').n_unique().alias('n_securities')
).group_by('tenor').agg(
    pl.col('n_securities').mean().alias('avg_securities')
)
```

## Output schema

The returned Polars DataFrame contains the following columns:

| Column | Type | Description |
|--------|------|-------------|
| `date` | Date | Business date (excludes weekends) |
| `cusip` | String | Security identifier |
| `tenor` | Int64 | Tenor in weeks (bills) or years (notes/bonds) |
| `vintage` | Int64 | Ordinal ranking by firstIssueDate (0 = on-the-run) |
| `maturityDate` | Date | Security maturity date |
| `coupon` | Float64 | Interest rate (0.0 for Bills, which are zero-coupon) |
| `firstIssueDate` | Date | Original issue date |
| `issuanceType` | String | "Opening" or "Re-opening" (None if no issuance) |
| `auctionDate` | Date | Date of the most recent auction |
| `unscheduledReopeningDate` | Date | Date of most recent unscheduled reopening (if any) |
| `totalIssued` | Float64 | Cumulative issuance in dollars |
| `announcementDate` | Date | Announcement date of most recent auction |
| `inflation_index_security` | Boolean | True for TIPS |
| `floating_rate` | Boolean | True for FRNs |
| `security_type` | String | "Bill", "Note", or "Bond" |

## Tenor Classifications

### Bills (measured in weeks)
- 1, 2, 4, 8, 13, 17, 22, 26, 52-week bills

### Notes and bonds (measured in years)
- 2, 3, 4, 5, 7, 10-year notes
- 20, 30-year bonds

Special securities like TIPS (Treasury Inflation-Protected Securities) and FRNs (Floating Rate Notes) are also classified appropriately.

## Caching

Data is automatically cached in platform-specific directories:

- **Linux**: `~/.local/share/ustCusipPanel/`
- **macOS**: `~/Library/Application Support/ustCusipPanel/`
- **Windows**: `%LOCALAPPDATA%\ustCusipPanel\`

Cache files:
- `auctions.csv`: Downloaded auction data
- `auctions.txt`: Date range metadata

The cache is automatically used and updated intelligently based on date range overlap. Six scenarios are handled:

| Scenario | Behavior |
|----------|----------|
| Exact match | Use cache as-is |
| Subset (requested ⊂ cache) | Filter cached data |
| Superset (cache ⊂ requested) | Fetch both ends, merge |
| Left extension | Fetch earlier range, merge |
| Right extension | Fetch later range, merge |
| No overlap | Full download |

Use `forceDownload=True` to bypass the cache entirely.

## API Reference

### `ustCusipPanel(startDate, endDate, silent, forceDownload)`

Main function to generate the CUSIP panel.

**Parameters:**

- `startDate` (str, default="1990-01-01"): Starting date in YYYY-MM-DD format
- `endDate` (str, optional): Ending date in YYYY-MM-DD format (defaults to today)
- `silent` (bool, default=False): Suppress summary statistics
- `forceDownload` (bool, default=False): Ignore cache and download fresh data

**Returns:**

- `pl.DataFrame`: Complete CUSIP-date panel

---

### `updateUstCusipPanel(data, silent)`

Incrementally update an existing CUSIP panel with new auction data.

**Parameters:**

- `data` (str, Path, or pl.DataFrame): Path to a parquet file produced by `ustCusipPanel()`, or a DataFrame with that output
- `silent` (bool, default=False): Suppress progress messages

**Returns:**

- Updated `pl.DataFrame` if `data` was a DataFrame; `None` if `data` was a file path (file is updated in place)

**Behavior:**

- Checks for rows with missing coupons in non-TIPS, non-FRN Notes/Bonds to determine the update start date (e.g., for recently auctioned securities whose coupon wasn't yet set)
- Falls back to the day after the latest date in the data if no missing coupons are found
- Fetches only new auction data from the API, merges with the existing cache, and regenerates the full panel

## Requirements

- Python ≥ 3.8
- polars ≥ 0.20.0
- requests ≥ 2.25.0
- platformdirs ≥ 3.0.0

## Data Source

Data is sourced from the [U.S. Treasury Fiscal Data API](https://fiscaldata.treasury.gov/), specifically the [Auction Query endpoint](https://fiscaldata.treasury.gov/datasets/treasury-auctions-query-auctions/treasury-auctions-query).

## Contributing

Contributions are welcome! This project is in the public domain (Unlicense).

## License

This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or distribute this software, either in source code form or as a compiled binary, for any purpose, commercial or non-commercial, and by any means.

See [LICENSE](LICENSE) for full details.

## Author

Corey Garriott

## Acknowledgments

- Data provided by the U.S. Department of the Treasury
- Built with [Polars](https://pola.rs/)

## Support

For issues, questions, or suggestions, please [open an issue](https://github.com/cgarriott/ustCusipPanel/issues) on GitHub.
