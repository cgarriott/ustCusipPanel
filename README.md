# ustCusipPanel

**U.S. Treasury CUSIP Panel Data Generator**

A Python package that creates from public sources a complete, CUSIP-date level panel dataset of US Treasury bond metadata.

## Features

- 📊 **Complete panel data**: Business date completion with no missing dates (weekends removed but not holidays)
- 🏷️ **Tenor classifications**: Automatic classification of bills (weeks), notes (years), and bonds (years)
- 📈 **Vintage tracking**: Ordinal rankings by first issue date within date-tenor groups (-1 for when-issued)
- 💰 **Cumulative issuance**: Track total issued amounts over time
- 🔄 **Auction markers**: Identify openings, re-openings, and unscheduled re-openings
- 💾 **Smart caching**: Local caching to minimize API calls

## Installation

### From PyPI (when published)

```bash
pip install ustCusipPanel
```

### From Source

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
| `coupon` | Float64 | Interest rate |
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

The cache is automatically used when the requested date range matches. Use `forceDownload=True` to bypass the cache.

## Why Polars?

This package uses [Polars](https://pola.rs/) instead of Pandas for several advantages:

- **⚡ Performance**: Written in Rust, significantly faster than Pandas
- **💾 Memory Efficiency**: Better memory management for large datasets
- **🔄 Lazy Evaluation**: Query optimization before execution
- **🎯 Intuitive API**: Clean and expressive syntax

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
