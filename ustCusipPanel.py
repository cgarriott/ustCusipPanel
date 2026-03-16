"""
ustCusipPanel - U.S. Treasury CUSIP Panel Data Generator
=========================================================

This module fetches and processes Treasury auction data from the U.S. Treasury's
Fiscal Data API to create a complete CUSIP-date panel with the following features:

- Business date completion (no missing dates), ideal for merging
- Tenor and vintage classifications
- Cumulative issuance tracking
- Auction markers and reopening indicators
- Maturity date, coupon, and CUSIP information

The data is cached locally for efficient subsequent access.

Main Function
-------------
ustCusipPanel(startDate, endDate, silent, forceDownload) -> pl.DataFrame

Dependencies
------------
- polars: High-performance DataFrame library (required)
- requests: HTTP library for API calls (required)
- platformdirs: Cross-platform user directories (required)

Author: Corey Garriott
License: Unlicense (Public Domain)
"""

import sys
from pathlib import Path
from datetime import date, timedelta
from typing import Optional, Union

# Check for Polars dependency before anything else
try:
    import polars as pl
except ImportError:
    print("\n" + "=" * 70)
    print("ERROR: Polars is required but not installed")
    print("=" * 70)
    print("\nPolars offers significant advantages over Pandas:")
    print("  • Faster data processing (written in Rust)")
    print("  • Better memory efficiency")
    print("  • Lazy evaluation support")
    print("  • More intuitive API")
    print("\nInstall Polars using:")
    print("  conda install -c conda-forge polars")
    print("  # OR")
    print("  pip install polars")
    print("=" * 70 + "\n")
    sys.exit(1)

# Import other dependencies
try:
    import requests
except ImportError:
    print("\nERROR: 'requests' library is required. Install with: pip install requests")
    sys.exit(1)

try:
    from platformdirs import user_data_dir
except ImportError:
    print("\nERROR: 'platformdirs' library is required. Install with: pip install platformdirs")
    sys.exit(1)


# API Configuration
API_BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
REQUIRED_FIELDS = [
    'cusip',
    'security_type',
    'issue_date',
    'original_issue_date',
    'maturity_date',
    'int_rate',
    'total_accepted',
    'reopening',
    'inflation_index_security',
    'floating_rate',
    'announcemt_date',
    'announcemtd_cusip',
    'auction_date'
]


def _getCacheDirectory() -> Path:
    """
    Get the appropriate cache directory for storing auction data.
    
    Uses platformdirs to determine the correct location based on OS:
    - Linux: ~/.local/share/ustCusipPanel/
    - macOS: ~/Library/Application Support/ustCusipPanel/
    - Windows: C:\\Users\\<username>\\AppData\\Local\\ustCusipPanel\\
    
    Returns
    -------
    Path
        Path object pointing to the cache directory
    """
    cache_dir = Path(user_data_dir("ustCusipPanel", "ustCusipPanel"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _fetchAuctionData(startDate: str, endDate: str) -> pl.DataFrame:
    """
    Fetch Treasury auction data from the Fiscal Data API with pagination.
    
    Parameters
    ----------
    startDate : str
        Starting date for data retrieval in YYYY-MM-DD format
    endDate : str
        Ending date for data retrieval in YYYY-MM-DD format
    
    Returns
    -------
    pl.DataFrame
        Polars DataFrame containing raw auction data
    """
    allData = []
    page = 1
    pageSize = 10000  # API maximum page size
    
    print(f"\nFetching auction data from {startDate} to {endDate}...")
    
    while True:
        # Build filter string with date range
        filterStr = f'auction_date:gte:{startDate},auction_date:lte:{endDate}'
        
        params = {
            'fields': ','.join(REQUIRED_FIELDS),
            'filter': filterStr,
            'format': 'json',
            'page[number]': page,
            'page[size]': pageSize
        }
        
        response = requests.get(API_BASE_URL, params=params)
        
        if response.status_code != 200:
            raise Exception(f"API request failed on page {page}: HTTP {response.status_code}")
        
        data = response.json()
        
        if 'data' not in data or len(data['data']) == 0:
            break
        
        allData.extend(data['data'])
        print(f"  Retrieved page {page}: {len(data['data'])} records")
        
        # Check if we've gotten all data
        if len(data['data']) < pageSize:
            break
        
        page += 1
    
    # Create Polars DataFrame
    df = pl.DataFrame(allData)
    print(f"  Total records retrieved: {len(df)}")
    
    return df


def _classifyTenor(df: pl.DataFrame) -> pl.DataFrame:
    """
    Classify securities by tenor based on original term to maturity.
    
    This function assigns tenor values to Treasury securities based on their
    original term to maturity:
    - Bills: Measured in weeks (e.g., 1, 2, 4, 8, 13, 17, 22, 26, 52)
    - Notes/Bonds: Measured in years (e.g., 2, 3, 4, 5, 7, 10, 20, 30)
    
    Special handling for unscheduled reopenings: When a security was announced
    with one CUSIP but reopened under a different CUSIP at auction, the tenor
    is calculated from the issue date rather than the original issue date.
    
    Parameters
    ----------
    df : pl.DataFrame
        DataFrame with issue_date, maturity_date, and reopening information
    
    Returns
    -------
    pl.DataFrame
        DataFrame with added 'tenor' column (Int64) and 'unscheduledReopeningDate'
    """
    # Sort by CUSIP and issue_date to identify first issuance
    df = df.sort(["cusip", "issue_date"])
    
    # Get the earliest issue_date and maturity_date for each CUSIP
    earliestDates = df.group_by("cusip").agg([
        pl.col("issue_date").first().alias("earliestIssueDate"),
        pl.col("maturity_date").first().alias("earliestMaturityDate")
    ])
    
    # Join back and calculate term to maturity in days
    df = df.join(earliestDates, on="cusip", how="left")
    
    df = df.with_columns(
        (pl.col("earliestMaturityDate") - pl.col("earliestIssueDate"))
        .dt.total_days()
        .alias("termToMaturityDays")
    )
    
    # Special case: For reopenings with announced CUSIP, recalculate term to maturity
    # using issue_date instead of earliest_issue_date (unscheduled reopenings)
    df = df.with_columns([
        pl.when(
            (pl.col("announcemtd_cusip") != "null") & 
            (pl.col("reopening") == "Yes")
        )
        .then(
            (pl.col("earliestMaturityDate") - pl.col("issue_date"))
            .dt.total_days()
        )
        .otherwise(pl.col("termToMaturityDays"))
        .alias("termToMaturityDays"),
        
        # Mark unscheduled reopenings with the issue date
        pl.when(
            (pl.col("announcemtd_cusip") != "null") & 
            (pl.col("reopening") == "Yes")
        )
        .then(pl.col("issue_date"))
        .otherwise(None)
        .alias("unscheduledReopeningDate")
    ])
    
    # Assign tenor classification
    df = df.with_columns(
        pl.when((pl.col("termToMaturityDays") >= 6) & 
                (pl.col("termToMaturityDays") <= 8))
          .then(pl.lit(1))  # 1-week bills
        .when((pl.col("termToMaturityDays") >= 13) & 
                (pl.col("termToMaturityDays") <= 15))
          .then(pl.lit(2))  # 2-week bills
        .when((pl.col("termToMaturityDays") >= 26) & 
                (pl.col("termToMaturityDays") <= 30))
          .then(pl.lit(4))  # 4-week bills
        .when((pl.col("termToMaturityDays") >= 53) & 
                (pl.col("termToMaturityDays") <= 59))
          .then(pl.lit(8))  # 8-week bills
        .when((pl.col("termToMaturityDays") >= 86) & 
                (pl.col("termToMaturityDays") <= 96))
          .then(pl.lit(13))  # 13-week bills
        .when((pl.col("termToMaturityDays") >= 114) & 
                (pl.col("termToMaturityDays") <= 124))
          .then(pl.lit(17))  # 17-week bills
        .when((pl.col("termToMaturityDays") >= 149) & 
                (pl.col("termToMaturityDays") <= 159))
          .then(pl.lit(22))  # 22-week bills
        .when((pl.col("termToMaturityDays") >= 176) & 
                (pl.col("termToMaturityDays") <= 188))
          .then(pl.lit(26))  # 26-week bills
        .when((pl.col("termToMaturityDays") >= 357) & 
                (pl.col("termToMaturityDays") <= 371))
          .then(pl.lit(52))  # 52-week bills
        .when((pl.col("termToMaturityDays") >= (2*365.25 - 93)) & 
                (pl.col("termToMaturityDays") <= (2*365.25 + 93)))
          .then(pl.lit(2))  # 2-year notes
        .when((pl.col("termToMaturityDays") >= (3*365.25 - 93)) & 
              (pl.col("termToMaturityDays") <= (3*365.25 + 93)))
          .then(pl.lit(3))  # 3-year notes
        .when((pl.col("termToMaturityDays") >= (4*365.25 - 93)) & 
              (pl.col("termToMaturityDays") <= (4*365.25 + 93)))
          .then(pl.lit(4))  # 4-year notes
        .when((pl.col("termToMaturityDays") >= (5*365.25 - 180)) & 
              (pl.col("termToMaturityDays") <= (5*365.25 + 180)))
          .then(pl.lit(5))  # 5-year notes
        .when((pl.col("termToMaturityDays") >= (7*365.25 - 180)) & 
              (pl.col("termToMaturityDays") <= (7*365.25 + 180)))
          .then(pl.lit(7))  # 7-year notes
        .when((pl.col("termToMaturityDays") >= (10*365.25 - 240)) & 
              (pl.col("termToMaturityDays") <= (10*365.25 + 240)))
          .then(pl.lit(10))  # 10-year notes
        .when((pl.col("termToMaturityDays") >= (20*365.25 - 540)) & 
              (pl.col("termToMaturityDays") <= (20*365.25 + 540)))
          .then(pl.lit(20))  # 20-year bonds
        .when((pl.col("termToMaturityDays") >= (30*365.25 - 720)) & 
              (pl.col("termToMaturityDays") <= (30*365.25 + 720)))
          .then(pl.lit(30))  # 30-year bonds
        .otherwise(None)
        .cast(pl.Int64)
        .alias("tenor")
    )
    
    # Drop intermediate calculation columns
    df = df.drop(["termToMaturityDays", "earliestIssueDate", "earliestMaturityDate"])
    
    return df


def _processRawAuctionData(rawDf: pl.DataFrame) -> pl.DataFrame:
    """
    Process raw auction data from API: type conversions, tenor classification, etc.

    Parameters
    ----------
    rawDf : pl.DataFrame
        Raw auction data from _fetchAuctionData()

    Returns
    -------
    pl.DataFrame
        Processed auction data ready for caching or panel creation
    """
    # Replace "null" strings with None for all relevant columns (before type conversions)
    auctionsDf = rawDf.with_columns([
        pl.when(pl.col("int_rate") == "null")
          .then(None)
          .otherwise(pl.col("int_rate"))
          .alias("int_rate"),
        pl.when(pl.col("announcemtd_cusip") == "null")
          .then(None)
          .otherwise(pl.col("announcemtd_cusip"))
          .alias("announcemtd_cusip"),
        pl.when(pl.col("original_issue_date") == "null")
          .then(None)
          .otherwise(pl.col("original_issue_date"))
          .alias("original_issue_date"),
        pl.when(pl.col("total_accepted") == "null")
          .then(None)
          .otherwise(pl.col("total_accepted"))
          .alias("total_accepted"),
        pl.when(pl.col("issue_date") == "null")
          .then(None)
          .otherwise(pl.col("issue_date"))
          .alias("issue_date"),
        pl.when(pl.col("maturity_date") == "null")
          .then(None)
          .otherwise(pl.col("maturity_date"))
          .alias("maturity_date"),
        pl.when(pl.col("auction_date") == "null")
          .then(None)
          .otherwise(pl.col("auction_date"))
          .alias("auction_date"),
        pl.when(pl.col("announcemt_date") == "null")
          .then(None)
          .otherwise(pl.col("announcemt_date"))
          .alias("announcemt_date")
    ])

    # Convert all date columns to Date type (from API string format)
    auctionsDf = auctionsDf.with_columns([
        pl.col("issue_date").str.to_date("%Y-%m-%d"),
        pl.col("original_issue_date").str.to_date("%Y-%m-%d"),
        pl.col("maturity_date").str.to_date("%Y-%m-%d"),
        pl.col("auction_date").str.to_date("%Y-%m-%d"),
        pl.col("announcemt_date").str.to_date("%Y-%m-%d")
    ])

    # Classify tenor (creates unscheduledReopeningDate as Date type)
    auctionsDf = _classifyTenor(auctionsDf)

    # Set coupon to zero for Bills (zero-coupon securities)
    auctionsDf = auctionsDf.with_columns(
        pl.when(pl.col("security_type") == "Bill")
          .then(pl.lit("0"))
          .otherwise(pl.col("int_rate"))
          .alias("int_rate")
    )

    # Convert inflation_index_security and floating_rate to Boolean
    auctionsDf = auctionsDf.with_columns([
        (pl.col("inflation_index_security") == "Yes").alias("inflation_index_security"),
        (pl.col("floating_rate") == "Yes").alias("floating_rate")
    ])

    # Convert numeric columns to proper types
    auctionsDf = auctionsDf.with_columns([
        pl.col("int_rate").cast(pl.Float64, strict=False),
        pl.col("total_accepted").cast(pl.Float64, strict=False),
        pl.col("tenor").cast(pl.Int32, strict=False)
    ])

    # Transform reopening column to auction with natural labeling
    auctionsDf = auctionsDf.with_columns(
        pl.when(pl.col("reopening") == "No")
          .then(pl.lit("Opening"))
          .when(pl.col("reopening") == "Yes")
          .then(pl.lit("Re-opening"))
          .otherwise(pl.lit(None))
          .alias("issuanceType")
    ).drop("reopening")

    # Replace any remaining "null" strings with None before saving
    for col in auctionsDf.columns:
        if auctionsDf.schema[col] == pl.Utf8:
            auctionsDf = auctionsDf.with_columns(
                pl.when(pl.col(col) == "null")
                  .then(None)
                  .otherwise(pl.col(col))
                  .alias(col)
            )

    return auctionsDf


def _loadOrDownloadData(startDate: str, endDate: str, forceDownload: bool) -> pl.DataFrame:
    """
    Load cached auction data or download fresh data from the API.
    
    This function manages data caching to avoid unnecessary API calls.
    It checks if cached data exists and matches the requested date range.
    
    Parameters
    ----------
    startDate : str
        Starting date in YYYY-MM-DD format
    endDate : str
        Ending date in YYYY-MM-DD format
    forceDownload : bool
        If True, ignore cache and download fresh data
    
    Returns
    -------
    pl.DataFrame
        Processed auction data with tenor classifications
    """
    cacheDir = _getCacheDirectory()
    csvFile = cacheDir / "auctions.csv"
    txtFile = cacheDir / "auctions.txt"

    # Handle forceDownload flag - bypass cache entirely
    if forceDownload:
        print("\nforceDownload=True, ignoring cache...")
        rawDf = _fetchAuctionData(startDate, endDate)
        auctionsDf = _processRawAuctionData(rawDf)

        # Save to cache
        auctionsDf.write_csv(csvFile)
        print(f"\nData cached to: {csvFile}")

        # Save date range
        with open(txtFile, 'w') as f:
            f.write(f"{startDate},{endDate}")
        print(f"Date range saved to: {txtFile}")

        return auctionsDf

    # Define schema for reading cached CSV
    cacheSchema = {
        "issue_date": pl.Date,
        "original_issue_date": pl.Date,
        "maturity_date": pl.Date,
        "announcemt_date": pl.Date,
        "auction_date": pl.Date,
        "unscheduledReopeningDate": pl.Date,
        "int_rate": pl.Float64,
        "total_accepted": pl.Float64,
        "tenor": pl.Int32,
        "inflation_index_security": pl.Boolean,
        "floating_rate": pl.Boolean
    }

    # Smart caching: Analyze date range overlap
    if csvFile.exists() and txtFile.exists():
        print(f"\nFound cached data in: {cacheDir}")

        # Parse cached date range
        with open(txtFile, 'r') as f:
            cachedRange = f.read().strip()
            cachedStartStr, cachedEndStr = cachedRange.split(',')
            cachedStart = date.fromisoformat(cachedStartStr)
            cachedEnd = date.fromisoformat(cachedEndStr)

        # Convert requested dates to date objects
        requestedStart = date.fromisoformat(startDate)
        requestedEnd = date.fromisoformat(endDate)

        # Analyze overlap between requested and cached ranges
        overlap = _analyzeDateRangeOverlap(
            requestedStart, requestedEnd,
            cachedStart, cachedEnd
        )

        # SCENARIO 1: Exact match - use cache as-is
        if overlap['scenario'] == 'exact':
            print(f"Cached data matches requested range ({startDate} to {endDate})")
            print("Loading data from cache...")
            return pl.read_csv(
                csvFile,
                null_values=["null"],
                schema_overrides=cacheSchema
            )

        # SCENARIO 2: Subset - filter cached data
        elif overlap['scenario'] == 'subset':
            print(f"Requested range ({startDate} to {endDate}) is within cache ({cachedStartStr} to {cachedEndStr})")
            print("Filtering cached data...")
            cachedData = pl.read_csv(
                csvFile,
                null_values=["null"],
                schema_overrides=cacheSchema
            )
            return cachedData.filter(
                (pl.col("auction_date") >= pl.lit(startDate).str.to_date("%Y-%m-%d")) &
                (pl.col("auction_date") <= pl.lit(endDate).str.to_date("%Y-%m-%d"))
            )

        # SCENARIO 3-5: Extension/overlap - fetch missing ranges and merge
        elif overlap['use_cache']:
            print(f"Requested range ({startDate} to {endDate}) extends beyond cache ({cachedStartStr} to {cachedEndStr})")
            print(f"Will fetch {len(overlap['fetch_ranges'])} missing range(s) and merge with cache")

            # Load cached data
            cachedData = pl.read_csv(
                csvFile,
                null_values=["null"],
                schema_overrides=cacheSchema
            )

            # Fetch missing ranges
            allNewData = []
            for fetchStart, fetchEnd in overlap['fetch_ranges']:
                print(f"\nFetching missing range: {fetchStart} to {fetchEnd}")
                rawDf = _fetchAuctionData(fetchStart, fetchEnd)
                if rawDf.height > 0:
                    processedDf = _processRawAuctionData(rawDf)
                    allNewData.append(processedDf)

            # Merge cached data with new data
            if allNewData:
                newData = pl.concat(allNewData) if len(allNewData) > 1 else allNewData[0]
                auctionsDf = pl.concat([cachedData, newData])
                # Deduplicate in case of boundary overlaps
                auctionsDf = _deduplicateAuctions(auctionsDf)
            else:
                auctionsDf = cachedData

            # Update cache with expanded range
            newCacheStart = min(requestedStart, cachedStart)
            newCacheEnd = max(requestedEnd, cachedEnd)

        # SCENARIO 6: No overlap - full download
        else:
            print(f"No overlap with cache ({cachedStartStr} to {cachedEndStr})")
            print(f"Downloading full range ({startDate} to {endDate})")
            rawDf = _fetchAuctionData(startDate, endDate)
            auctionsDf = _processRawAuctionData(rawDf)

            # New cache range is just the requested range
            newCacheStart = requestedStart
            newCacheEnd = requestedEnd

    else:
        # No cache exists - full download
        print("\nNo cache found")
        print(f"Downloading full range ({startDate} to {endDate})")
        rawDf = _fetchAuctionData(startDate, endDate)
        auctionsDf = _processRawAuctionData(rawDf)

        # New cache range is the requested range
        newCacheStart = date.fromisoformat(startDate)
        newCacheEnd = date.fromisoformat(endDate)

    # Save to cache
    auctionsDf.write_csv(csvFile)
    print(f"\nCache updated: {csvFile}")

    # Save expanded date range
    with open(txtFile, 'w') as f:
        f.write(f"{newCacheStart.strftime('%Y-%m-%d')},{newCacheEnd.strftime('%Y-%m-%d')}")
    print(f"Cache range: {newCacheStart.strftime('%Y-%m-%d')} to {newCacheEnd.strftime('%Y-%m-%d')}")
    
    return auctionsDf


def _analyzeDateRangeOverlap(
    requestedStart: date,
    requestedEnd: date,
    cachedStart: date,
    cachedEnd: date
) -> dict:
    """
    Analyze the relationship between requested and cached date ranges.

    Determines which caching strategy to use based on how the requested
    date range overlaps with the cached date range.

    Parameters
    ----------
    requestedStart : date
        Start date of requested range
    requestedEnd : date
        End date of requested range
    cachedStart : date
        Start date of cached range
    cachedEnd : date
        End date of cached range

    Returns
    -------
    dict
        Dictionary with keys:
        - scenario: str ('exact', 'subset', 'left_extend', 'right_extend', 'superset', 'no_overlap')
        - use_cache: bool (whether cached data can be used)
        - fetch_ranges: List[Tuple[str, str]] (date ranges to fetch from API)
        - filter_cache: bool (whether to filter cached data to requested range)
    """
    # Scenario 1: Exact match
    if requestedStart == cachedStart and requestedEnd == cachedEnd:
        return {
            'scenario': 'exact',
            'use_cache': True,
            'fetch_ranges': [],
            'filter_cache': False
        }

    # Scenario 2: Subset (requested fully within cache)
    if cachedStart <= requestedStart and requestedEnd <= cachedEnd:
        return {
            'scenario': 'subset',
            'use_cache': True,
            'fetch_ranges': [],
            'filter_cache': True
        }

    # Scenario 3: Superset (cache fully within requested)
    if requestedStart < cachedStart and requestedEnd > cachedEnd:
        fetch_ranges = []
        # Fetch earlier data
        fetch_ranges.append((
            requestedStart.strftime('%Y-%m-%d'),
            (cachedStart - timedelta(days=1)).strftime('%Y-%m-%d')
        ))
        # Fetch later data
        fetch_ranges.append((
            (cachedEnd + timedelta(days=1)).strftime('%Y-%m-%d'),
            requestedEnd.strftime('%Y-%m-%d')
        ))
        return {
            'scenario': 'superset',
            'use_cache': True,
            'fetch_ranges': fetch_ranges,
            'filter_cache': False
        }

    # Scenario 4: Left extension (extends before cache)
    if requestedStart < cachedStart and requestedEnd >= cachedStart and requestedEnd <= cachedEnd:
        fetch_ranges = [(
            requestedStart.strftime('%Y-%m-%d'),
            (cachedStart - timedelta(days=1)).strftime('%Y-%m-%d')
        )]
        return {
            'scenario': 'left_extend',
            'use_cache': True,
            'fetch_ranges': fetch_ranges,
            'filter_cache': False
        }

    # Scenario 5: Right extension (extends after cache)
    if requestedStart >= cachedStart and requestedStart <= cachedEnd and requestedEnd > cachedEnd:
        fetch_ranges = [(
            (cachedEnd + timedelta(days=1)).strftime('%Y-%m-%d'),
            requestedEnd.strftime('%Y-%m-%d')
        )]
        return {
            'scenario': 'right_extend',
            'use_cache': True,
            'fetch_ranges': fetch_ranges,
            'filter_cache': False
        }

    # Scenario 6: No overlap (completely disjoint)
    return {
        'scenario': 'no_overlap',
        'use_cache': False,
        'fetch_ranges': [(
            requestedStart.strftime('%Y-%m-%d'),
            requestedEnd.strftime('%Y-%m-%d')
        )],
        'filter_cache': False
    }


def _deduplicateAuctions(df: pl.DataFrame) -> pl.DataFrame:
    """
    Remove duplicate auctions based on auction_date and cusip.

    This handles boundary overlaps when merging new data with cache.
    Keeps the first occurrence after sorting by auction_date, cusip, and issue_date.

    Parameters
    ----------
    df : pl.DataFrame
        DataFrame potentially containing duplicate auctions

    Returns
    -------
    pl.DataFrame
        DataFrame with duplicates removed
    """
    return df.sort(['auction_date', 'cusip', 'issue_date']).unique(
        subset=['auction_date', 'cusip'],
        keep='first'
    )


def _createCusipPanel(auctionsDf: pl.DataFrame) -> pl.DataFrame:
    """
    Create a complete CUSIP-date panel from auction data.
    
    This function:
    1. Computes firstIssueDate for each CUSIP
    2. Creates complete date ranges (time series completion)
    3. Forward/backward fills CUSIP characteristics
    4. Calculates cumulative issuance
    5. Computes vintage rankings
    6. Filters out weekends
    
    Parameters
    ----------
    auctionsDf : pl.DataFrame
        Raw auction data with tenor classifications
    
    Returns
    -------
    pl.DataFrame
        Complete CUSIP-date panel with all features
    """
    # 1. Get the earliest issue_date for each CUSIP (for firstIssueDate column)
    auctionsDf = auctionsDf.with_columns(
        pl.col("issue_date").min().over("cusip").alias("firstIssueDate")
    )
    
    # 2. Time series completion (fill in missing dates for each CUSIP)
    # Get the date range boundaries for each CUSIP
    cusipDateRanges = auctionsDf.group_by("cusip").agg([
        pl.col("announcemt_date").min().alias("start_date"),
        pl.col("maturity_date").first().alias("end_date")
    ])
    
    # Create complete date range for each CUSIP
    allCusipDates = []
    today = date.today()
    
    for row in cusipDateRanges.iter_rows(named=True):
        # Create date range from first announcement to maturity (bounded by today)
        endDate = min(row["end_date"], today)
        dateRange = pl.date_range(
            pl.lit(row["start_date"]),
            pl.lit(endDate),
            interval="1d",
            eager=True
        )
        
        # Create dataframe for this CUSIP with all dates
        cusipDates = pl.DataFrame({
            "cusip": [row["cusip"]] * len(dateRange),
            "date": dateRange
        })
        
        allCusipDates.append(cusipDates)
    
    # Concatenate all CUSIP date ranges
    completeDates = pl.concat(allCusipDates)
    
    # Prepare auction data for joining
    auctionData = auctionsDf.select([
        pl.col("cusip"),
        pl.col("issue_date").alias("date"),
        pl.col("total_accepted").alias("amountIssued"),
        pl.col("issuanceType"),
        pl.col("tenor"),
        pl.col("int_rate").alias("coupon"),
        pl.col("maturity_date").alias("maturityDate"),
        pl.col("announcemt_date").alias("announcementDate"),
        pl.col("auction_date").alias("auctionDate"),
        pl.col("unscheduledReopeningDate"),
        pl.col("firstIssueDate"),
        pl.col("inflation_index_security").alias("TIPS"),
        pl.col("floating_rate").alias("floatingRate"),
        pl.col("security_type").alias("securityType")
    ])
    
    # Handle bonds to be issued (future-dated)
    # For any CUSIP-date with date > today, change date to today if no today observation exists
    futureDated = auctionData.filter(pl.col("date") > today)
    if futureDated.height > 0:
        cusipsWithToday = auctionData.filter(pl.col("date") == today).select("cusip")
        
        # Split future-dated records
        futureWithoutToday = futureDated.join(cusipsWithToday, on="cusip", how="anti")
        
        # Remove future-dated records
        auctionData = auctionData.filter(pl.col("date") <= today)
        
        # For CUSIPs without a today observation, change their date to today
        if futureWithoutToday.height > 0:
            futureWithoutToday = futureWithoutToday.with_columns([
                pl.lit(today).alias("date"),
                pl.lit(None).alias("issuanceType"),
                pl.lit(None).alias("unscheduledReopeningDate"),
                pl.lit(0.0).cast(pl.Float64).alias("amountIssued")
            ])
            auctionData = pl.concat([auctionData, futureWithoutToday])
    
    # Join complete date range with auction data
    auctionsDf = completeDates.join(
        auctionData,
        on=["cusip", "date"],
        how="left"
    )
    
    # Sort by CUSIP and date to prepare for forward fill
    auctionsDf = auctionsDf.sort(["cusip", "date"])
    
    # Forward-fill static values within each CUSIP group
    auctionsDf = auctionsDf.with_columns([
        pl.col("tenor").forward_fill().over("cusip"),
        pl.col("coupon").forward_fill().over("cusip"),
        pl.col("maturityDate").forward_fill().over("cusip"),
        pl.col("announcementDate").forward_fill().over("cusip"),
        pl.col("auctionDate").forward_fill().over("cusip"),
        pl.col("unscheduledReopeningDate").forward_fill().over("cusip"),
        pl.col("firstIssueDate").forward_fill().over("cusip"),
        pl.col("TIPS").forward_fill().over("cusip"),
        pl.col("floatingRate").forward_fill().over("cusip"),
        pl.col("securityType").forward_fill().over("cusip")
    ])
    
    # Backward-fill static values
    auctionsDf = auctionsDf.with_columns([
        pl.col("tenor").backward_fill().over("cusip"),
        pl.col("coupon").backward_fill().over("cusip"),
        pl.col("maturityDate").backward_fill().over("cusip"),
        pl.col("announcementDate").backward_fill().over("cusip"),
        pl.col("auctionDate").backward_fill().over("cusip"),
        pl.col("firstIssueDate").backward_fill().over("cusip"),
        pl.col("TIPS").backward_fill().over("cusip"),
        pl.col("floatingRate").backward_fill().over("cusip"),
        pl.col("securityType").backward_fill().over("cusip")
    ])
    
    # Set amountIssued to 0 for non-issue dates and dates before first issue
    auctionsDf = auctionsDf.with_columns(
        pl.when(pl.col("date") < pl.col("firstIssueDate"))
          .then(pl.lit(0))
          .otherwise(pl.col("amountIssued").fill_null(0))
          .alias("amountIssued")
    )
    
    # 3. Calculate cumulative issuance
    auctionsDf = auctionsDf.sort(
        ["cusip", "date", "tenor", "firstIssueDate"], 
        descending=[True, False, False, True]
    )
    auctionsDf = auctionsDf.with_columns(
        pl.col("amountIssued").cum_sum().over("cusip").alias("totalIssued")
    )
    
    # 4. Calculate vintage (ordinal ranking by firstIssueDate within date-security_type-inflation_index_security-floating_rate-tenor)
    # Latest firstIssueDate gets vintage 0, next-latest gets 1, etc.
    auctionsDf = auctionsDf.with_columns(
        (pl.col("firstIssueDate")
         .rank(method="dense", descending=True)
         .over(["date", "securityType", "TIPS", "floatingRate", "tenor"]) - 1)
        .cast(pl.Int64)
        .alias("vintage")
    )
    
    # Adjust vintage for "when issued" bonds
    auctionsDf = auctionsDf.with_columns(
        (pl.col("date") < pl.col("firstIssueDate"))
        .any()
        .over(["date", "securityType", "TIPS", "floatingRate", "tenor"])
        .alias("hasWhenIssued")
    ).with_columns(
        pl.when(pl.col("hasWhenIssued"))
          .then(pl.col("vintage") - 1)
          .otherwise(pl.col("vintage"))
          .alias("vintage")
    ).drop("hasWhenIssued")
    
    # 5. Filter out weekends (Saturday=6, Sunday=7 in weekday())
    auctionsDf = auctionsDf.filter(
        pl.col("date").dt.weekday() < 6
    )
    
    # Sort final output
    auctionsDf = auctionsDf.sort(
        ["floatingRate", "TIPS", "date", "securityType", "tenor", "vintage"], 
        descending=[False, False, True, True, False, False]
    )
    
    # Reorder columns for consistent output
    auctionsDf = auctionsDf.select([
        'date', 'cusip', 'securityType', 'tenor', 'vintage',
        'coupon', 'maturityDate', 'TIPS',
        'floatingRate', 'firstIssueDate',
        'issuanceType', 'auctionDate', 'unscheduledReopeningDate',
        'amountIssued', 'totalIssued',
        'announcementDate'
    ])
    
    return auctionsDf


def _printSummary(df: pl.DataFrame) -> None:
    """
    Print summary statistics about the CUSIP panel.
    
    Parameters
    ----------
    df : pl.DataFrame
        Complete CUSIP panel data
    """
    print(f"\n{'=' * 70}")
    print("Data Statistics Summary")
    print(f"{'=' * 70}")
    print(f"Total observations: {len(df):,}")
    print(f"Unique CUSIPs: {df.select(pl.col('cusip').n_unique()).item():,}")
    print(f"Date range: {df.select(pl.col('date').min()).item()} to {df.select(pl.col('date').max()).item()}")
    
    # Bill statistics (by week tenors)
    print(f"\n{'=' * 70}")
    print("Bill Statistics (by tenor in weeks)")
    print(f"{'=' * 70}")
    billTenors = sorted(
        df.filter(pl.col('securityType') == 'Bill')
        .select(pl.col('tenor').unique())
        .drop_nulls()
        .to_series()
        .to_list()
    )
    for tenor in billTenors:
        billData = df.filter(
            (pl.col('securityType') == 'Bill') & 
            (pl.col('tenor') == tenor)
        )
        if billData.height > 0:
            uniqueCusips = billData.select(pl.col('cusip').n_unique()).item()
            avgVintages = billData.group_by('date').agg(
                pl.col('vintage').n_unique().alias('nVintages')
            ).select(pl.col('nVintages').mean()).item()
            print(f"{tenor}-week: {uniqueCusips:,} unique CUSIPs, {int(round(avgVintages))} avg daily vintages")
    
    # Note/Bond statistics (by year tenors)
    print(f"\n{'=' * 70}")
    print("Note/Bond Statistics (by tenor in years)")
    print(f"{'=' * 70}")
    noteBondTenors = sorted(
        df.filter(pl.col('securityType') != 'Bill')
        .select(pl.col('tenor').unique())
        .drop_nulls()
        .to_series()
        .to_list()
    )
    for tenor in noteBondTenors:
        noteBondData = df.filter(
            (pl.col('securityType') != 'Bill') & 
            (pl.col('tenor') == tenor)
        )
        if noteBondData.height > 0:
            uniqueCusips = noteBondData.select(pl.col('cusip').n_unique()).item()
            avgVintages = noteBondData.group_by('date').agg(
                pl.col('vintage').n_unique().alias('nVintages')
            ).select(pl.col('nVintages').mean()).item()
            print(f"{tenor}-year: {uniqueCusips:,} unique CUSIPs, {int(round(avgVintages))} avg daily vintages")
    
    print(f"{'=' * 70}\n")


def ustCusipPanel(
    startDate: str = "1990-01-01",
    endDate: Optional[str] = None,
    silent: bool = False,
    forceDownload: bool = False
) -> pl.DataFrame:
    """
    Download and process Treasury auction data into a CUSIP-date panel.
    
    This is the main public function that fetches Treasury auction data from the
    U.S. Treasury's Fiscal Data API and transforms it into a complete panel dataset
    with business date completion, tenor classifications, vintage rankings, and
    cumulative issuance tracking.
    
    The data is automatically cached locally to avoid repeated API calls. By default,
    the function uses cached data if it matches the requested date range.
    
    Parameters
    ----------
    startDate : str, default="1990-01-01"
        Starting date for data retrieval in YYYY-MM-DD format.
        Default is "1990-01-01" which captures the modern Treasury auction system.
    endDate : str, optional
        Ending date for data retrieval in YYYY-MM-DD format.
        If None (default), uses today's date.
    silent : bool, default=False
        If True, suppresses summary statistics output.
        If False (default), prints detailed statistics about the panel.
    forceDownload : bool, default=False
        If True, ignores cached data and downloads fresh data from the API.
        If False (default), uses cached data when the date range matches.
    
    Returns
    -------
    pl.DataFrame
        Complete CUSIP-date panel with the following columns:
        - date: Business date (excludes weekends)
        - cusip: Security identifier
        - tenor: Security tenor classification (weeks for bills, years for notes/bonds)
        - vintage: Ordinal ranking by firstIssueDate within date-tenor group
        - maturityDate: Security maturity date
        - coupon: Interest rate
        - firstIssueDate: Original issue date of the security
        - auction: Auction type ("Opening" or "Re-opening", None if no auction)
        - unscheduledReopeningDate: Date if security had unscheduled reopening
        - totalIssued: Cumulative issuance amount (in millions)
        - announcementDate: Date when security was announced
        - inflation_index_security: Boolean for TIPS
        - floating_rate: Boolean for FRNs
        - security_type: "Bill", "Note", or "Bond"
    
    Examples
    --------
    >>> # Basic usage with default date range (1990-01-01 to today)
    >>> df = ustCusipPanel()
    
    >>> # Specify custom date range
    >>> df = ustCusipPanel(startDate="2020-01-01", endDate="2023-12-31")
    
    >>> # Suppress summary statistics
    >>> df = ustCusipPanel(silent=True)
    
    >>> # Force fresh download (ignore cache)
    >>> df = ustCusipPanel(forceDownload=True)
    
    >>> # Access specific tenors
    >>> df_10y = df.filter(pl.col('tenor') == 10)
    
    >>> # Get on-the-run securities (vintage 0)
    >>> df_otr = df.filter(pl.col('vintage') == 0)
    
    Notes
    -----
    - Data is cached in a platform-specific user data directory:
      * Linux: ~/.local/share/ustCusipPanel/
      * macOS: ~/Library/Application Support/ustCusipPanel/
      * Windows: %LOCALAPPDATA%\\ustCusipPanel\\
    - Weekends are automatically excluded from the panel
    - The panel includes "when issued" securities (vintage = -1)
    - Tenor classifications handle bills, notes, bonds, TIPS, and FRNs
    
    See Also
    --------
    Polars documentation: https://pola-rs.github.io/polars/
    Treasury Fiscal Data API: https://fiscaldata.treasury.gov/
    """
    # Default end_date to today if not provided
    if endDate is None:
        endDate = date.today().strftime("%Y-%m-%d")
    
    # Load or download the auction data
    auctionsDf = _loadOrDownloadData(startDate, endDate, forceDownload)
    
    # Create the complete CUSIP panel
    panelDf = _createCusipPanel(auctionsDf)
    
    # Print summary statistics unless silenced
    if not silent:
        _printSummary(panelDf)
    
    return panelDf


def _updateCache(mergedAuctions: pl.DataFrame, startDate: str, endDate: str) -> None:
    """
    Update the cache files with merged auction data.

    Parameters
    ----------
    mergedAuctions : pl.DataFrame
        Processed auction data to save to cache
    startDate : str
        Starting date of the cached data in YYYY-MM-DD format
    endDate : str
        Ending date of the cached data in YYYY-MM-DD format
    """
    cacheDir = _getCacheDirectory()
    csvFile = cacheDir / "auctions.csv"
    txtFile = cacheDir / "auctions.txt"

    # Save auction data to cache
    mergedAuctions.write_csv(csvFile)
    print(f"Cache updated: {csvFile}")

    # Save date range metadata
    with open(txtFile, 'w') as f:
        f.write(f"{startDate},{endDate}")
    print(f"Cache date range: {startDate} to {endDate}")


def updateUstCusipPanel(
    data: Union[str, Path, pl.DataFrame],
    silent: bool = False
) -> Optional[pl.DataFrame]:
    """
    Update existing CUSIP panel data incrementally.

    This function intelligently updates panel data by only fetching new auction
    data from the API, avoiding re-downloading data that already exists in cache.

    The update process:
    1. Determines the update start date by checking for missing coupons in
       non-TIPS/non-FRN Notes/Bonds, or from the day after the latest date
    2. Fetches only new data from the API (from update start date to today)
    3. Merges new data with existing cache
    4. Regenerates the complete panel with updated data
    5. Updates both the cache and the input data source

    Parameters
    ----------
    data : str, Path, or pl.DataFrame
        Either a path to a parquet file containing panel output from ustCusipPanel(),
        or a Polars DataFrame with panel output from ustCusipPanel()
    silent : bool, default=False
        If True, suppress progress messages and summary statistics

    Returns
    -------
    pl.DataFrame or None
        If data is a DataFrame: returns the updated DataFrame
        If data is a path: updates the parquet file in place and returns None

    Examples
    --------
    >>> # Update a parquet file
    >>> updateUstCusipPanel("treasury_panel.parquet")

    >>> # Update a DataFrame and get the result
    >>> df = ustCusipPanel(startDate="2020-01-01", endDate="2025-12-31")
    >>> df_updated = updateUstCusipPanel(df, silent=True)

    Notes
    -----
    - The global cache (~/.local/share/ustCusipPanel/) is always updated
    - For parquet files, only forward updates from the file's max date
    - Missing coupons in Notes/Bonds trigger a refresh from that date
    - Already up-to-date data returns unchanged
    """
    # Determine if input is a file path or DataFrame
    isFilePath = isinstance(data, (str, Path))

    if isFilePath:
        filePath = Path(data)
        if not filePath.exists():
            raise FileNotFoundError(f"Parquet file not found: {filePath}")

        # Load panel data from parquet
        if not silent:
            print(f"\nLoading panel data from: {filePath}")
        panelDf = pl.read_parquet(filePath)
    else:
        # Input is already a DataFrame
        panelDf = data

    # Validate that this looks like panel data
    required_cols = ['date', 'cusip', 'coupon', 'securityType', 'TIPS', 'floatingRate']
    missing_cols = [col for col in required_cols if col not in panelDf.columns]
    if missing_cols:
        raise ValueError(f"Input data is missing required columns: {missing_cols}")

    # Step 1: Detect update start date
    # Filter for Notes/Bonds that are not TIPS and not floating rate
    regularNotesBonds = panelDf.filter(
        (pl.col("securityType") != "Bill") &
        (pl.col("TIPS") == False) &
        (pl.col("floatingRate") == False)
    )

    # Check for null coupons
    missingCoupons = regularNotesBonds.filter(pl.col("coupon").is_null())

    if missingCoupons.height > 0:
        # Found missing coupons - start update from the earliest such date
        updateStartDate = missingCoupons.select(pl.col("date").min()).item()
        if not silent:
            print(f"\nFound {missingCoupons.height} rows with missing coupons in Notes/Bonds")
            print(f"Update will refresh data from: {updateStartDate}")
    else:
        # No missing coupons - update from day after latest date
        maxDate = panelDf.select(pl.col("date").max()).item()
        updateStartDate = maxDate + timedelta(days=1)
        if not silent:
            print(f"\nNo missing coupons found")
            print(f"Latest date in data: {maxDate}")
            print(f"Update will fetch new data from: {updateStartDate}")

    # Check if already up to date
    today = date.today()
    if updateStartDate > today:
        if not silent:
            print(f"Data is already up to date (through {today})")
        return panelDf if not isFilePath else None

    updateEndDate = today.strftime("%Y-%m-%d")
    updateStartDateStr = updateStartDate.strftime("%Y-%m-%d")

    # Step 2: Load existing cache and merge with new data
    cacheDir = _getCacheDirectory()
    csvFile = cacheDir / "auctions.csv"
    txtFile = cacheDir / "auctions.txt"

    # Load existing cache if it exists
    if csvFile.exists():
        if not silent:
            print(f"\nLoading existing cache from: {cacheDir}")

        cachedAuctions = pl.read_csv(
            csvFile,
            null_values=["null"],
            schema_overrides={
                "issue_date": pl.Date,
                "original_issue_date": pl.Date,
                "maturity_date": pl.Date,
                "announcemt_date": pl.Date,
                "auction_date": pl.Date,
                "unscheduledReopeningDate": pl.Date,
                "int_rate": pl.Float64,
                "total_accepted": pl.Float64,
                "tenor": pl.Int32,
                "inflation_index_security": pl.Boolean,
                "floating_rate": pl.Boolean
            }
        )

        # Get original cache start date
        if txtFile.exists():
            with open(txtFile, 'r') as f:
                cachedRange = f.read().strip()
                originalStartDate = cachedRange.split(',')[0]
        else:
            originalStartDate = cachedAuctions.select(pl.col("auction_date").min()).item().strftime("%Y-%m-%d")

        # Filter cached auctions to remove overlap
        cachedAuctions = cachedAuctions.filter(
            pl.col("auction_date") < pl.lit(updateStartDate).str.to_date("%Y-%m-%d")
        )

        if not silent:
            print(f"Retained {cachedAuctions.height} cached auction records before {updateStartDate}")
    else:
        if not silent:
            print("\nNo existing cache found, will create new cache")
        cachedAuctions = None
        # Use a reasonable default start date (or could use min date from panel)
        originalStartDate = "1990-01-01"

    # Fetch new data from API
    if not silent:
        print(f"\nFetching new auction data from {updateStartDateStr} to {updateEndDate}...")

    newRawDf = _fetchAuctionData(updateStartDateStr, updateEndDate)

    if newRawDf.height == 0:
        if not silent:
            print("No new auction data found")
        return panelDf if not isFilePath else None

    # Process the new raw data
    newProcessedDf = _processRawAuctionData(newRawDf)

    # Merge with cached data
    if cachedAuctions is not None:
        mergedAuctions = pl.concat([cachedAuctions, newProcessedDf])
    else:
        mergedAuctions = newProcessedDf

    if not silent:
        print(f"Total auction records after merge: {mergedAuctions.height}")

    # Update cache
    _updateCache(mergedAuctions, originalStartDate, updateEndDate)

    # Step 3: Regenerate complete panel
    if not silent:
        print("\nRegenerating CUSIP panel with updated data...")

    updatedPanelDf = _createCusipPanel(mergedAuctions)

    # Print summary statistics unless silenced
    if not silent:
        _printSummary(updatedPanelDf)

    # Step 4: Return or save based on input type
    if isFilePath:
        # Save updated panel back to parquet file
        updatedPanelDf.write_parquet(filePath)
        if not silent:
            print(f"\nUpdated panel saved to: {filePath}")
        return None
    else:
        # Return updated DataFrame
        return updatedPanelDf


if __name__ == "__main__":
    # Example usage when run as a script
    print("=" * 70)
    print("ustCusipPanel - Treasury CUSIP Panel Generator")
    print("=" * 70)
    print("\nGenerating panel with default parameters...")
    print("(startDate='1990-01-01', endDate=today, silent=False)")
    
    df = ustCusipPanel()
    
    print(f"\nPanel shape: {df.shape[0]:,} rows × {df.shape[1]} columns")
    print("\nFirst few rows:")
    print(df.head())
    
    print("\nLast few rows:")
    print(df.tail())
