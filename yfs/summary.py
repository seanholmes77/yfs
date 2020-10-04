"""Contains the classes and functions for scraping a yahoo finance summary page."""

from collections import ChainMap
from concurrent.futures import as_completed, ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional

import enlighten
from pandas import DataFrame
from pendulum.date import Date
from pydantic import BaseModel as Base
from pydantic import Field
from requests_html import HTML

from .cleaner import cleaner, CommonCleaners, table_cleaner
from .lookup import fuzzy_search
from .quote import parse_quote_header_info, Quote
from .requestor import requestor


class SummaryPage(Base):
    """Data scraped from the yahoo finance summary page.

    Attributes:
        symbol (str): Ticker Symbol
        name (str): Ticker Name

        quote (Quote): Quote header section of the page.

        open (float): Open price.
        high (float): Days high.
        low (float): Days low.
        close (float): Days close price.

        change (float): Dollar change in price.
        percent_change (float): Percent change in price.

        previous_close (float): Previous days close price.

        bid_price (float): Bid price.
        bid_size (int): Bid size.

        ask_price (float): Ask price.
        ask_size (int): Ask size.

        fifty_two_week_high (float): High of the fifty two week range.
        fifty_two_week_low (float): Low of the fifty two week range.

        volume (int): Volume.
        average_volume (int): Average Volume.

        market_cap (int): Market capitalization.

        beta_five_year_monthly (float): Five year monthly prices benchmarked against the SPY.
        pe_ratio_ttm (float): Share Price divided by Earnings Per Share trailing twelve months.
        eps_ttm (float): Earnings per share trailing twelve months.

        earnings_date (Date): Estimated earnings report release date.

        forward_dividend_yield (float): Estimated dividend yield.
        forward_dividend_yield_percentage (float): Estimated divided yield percentage.
        exdividend_date (Date): Ex-Dividend Date.

        one_year_target_est (float): One year target estimation.

    Notes:
        This class inherits from the pydantic BaseModel which allows for the use
        of .json() and .dict() for serialization to json strings and dictionaries.

        .json(): Serialize to a JSON object.
        .dict(): Serialize to a dictionary.
    """

    symbol: str
    name: str  # from quote

    quote: Quote

    open: Optional[float]
    high: Optional[float] = Field(alias="days_range")
    low: Optional[float] = Field(alias="days_range")
    close: Optional[float]  # pre-cleaned from quote

    change: Optional[float]  # pre-cleaned from quote
    percent_change: Optional[float]  # pre-cleaned from quote

    previous_close: Optional[float]

    bid_price: Optional[float] = Field(alias="bid")
    bid_size: Optional[int] = Field(alias="bid")

    ask_price: Optional[float] = Field(alias="ask")
    ask_size: Optional[int] = Field(alias="ask")

    fifty_two_week_low: Optional[float] = Field(alias="fifty_two_week_range")
    fifty_two_week_high: Optional[float] = Field(alias="fifty_two_week_range")

    volume: Optional[int]
    average_volume: Optional[int] = Field(alias="avg_volume")

    market_cap: Optional[int]

    beta_five_year_monthly: Optional[float]
    pe_ratio_ttm: Optional[float]
    eps_ttm: Optional[float]

    earnings_date: Optional[Date]

    forward_dividend_yield: Optional[float]
    forward_dividend_yield_percentage: Optional[float] = Field(alias="forward_dividend_yield")
    exdividend_date: Optional[Date]

    one_year_target_est: Optional[float]

    _clean_symbol = cleaner("symbol")(CommonCleaners.clean_symbol)

    _clean_highs = cleaner("high", "fifty_two_week_high")(
        CommonCleaners.clean_first_value_split_by_dash
    )
    _clean_lows = cleaner("low", "fifty_two_week_low")(
        CommonCleaners.clean_second_value_split_by_dash
    )
    _clean_date = cleaner("earnings_date", "exdividend_date")(CommonCleaners.clean_date)

    _clean_common_values = cleaner(
        "open",
        "previous_close",
        "market_cap",
        "volume",
        "average_volume",
        "pe_ratio_ttm",
        "beta_five_year_monthly",
        "eps_ttm",
        "one_year_target_est",
    )(CommonCleaners.clean_common_values)

    _clean_forward_dividend_yield = cleaner("forward_dividend_yield")(
        CommonCleaners.clean_first_value_split_by_space
    )

    _clean_forward_dividend_yield_percentage = cleaner("forward_dividend_yield_percentage")(
        CommonCleaners.clean_second_value_split_by_space
    )

    _clean_bid_ask_price = cleaner("bid_price", "ask_price")(
        CommonCleaners.clean_first_value_split_by_x
    )

    _clean_bid_ask_volume = cleaner("bid_size", "ask_size")(
        CommonCleaners.clean_second_value_split_by_x
    )

    def __lt__(self, other) -> bool:  # noqa: ANN001
        """Compare SummaryPage objects to allow ordering by symbol."""
        if other.__class__ is self.__class__:
            return self.symbol < other.symbol

        return None


class SummaryPageGroup(Base):
    """Group of SummaryPage objects from multiple symbols.

    Attributes:
        pages (SummaryPage):

    Notes:
        This class inherits from the pydantic BaseModel which allows for the use
        of .json() and .dict() for serialization to json strings and dictionaries.

        .json(): Serialize to a JSON object.
        .dict(): Serialize to a dictionary.

    """

    pages: List[SummaryPage] = list()

    def append(self, page: SummaryPage) -> None:
        """Append a SummaryPage to the SummaryPageGroup.

        Args:
            page (SummaryPage): A SummaryPage object to add to the group.
        """
        if page.__class__ is SummaryPage:
            self.pages.append(page)
        else:
            raise AttributeError("Can only append SummaryPage objects.")

    @property
    def symbols(self: "SummaryPageGroup") -> List[str]:
        """List of symbols in the SummaryPageGroup."""
        return [symbol.symbol for symbol in self]

    def sort(self: "SummaryPageGroup") -> None:
        """Sort SummaryPage objects by symbol."""
        self.pages = sorted(self.pages)

    @property
    def dataframe(self: "SummaryPageGroup") -> Optional[DataFrame]:
        """Return a dataframe of multiple SummaryPage objects."""
        pages = self.dict().get("pages")

        if pages:
            dataframe = DataFrame.from_dict(pages)
            dataframe.set_index("symbol", inplace=True)
            dataframe.drop("quote", axis=1, inplace=True)
            dataframe.sort_index(inplace=True)

            return dataframe  # TODO: none or nan

        return None

    def __iter__(self: "SummaryPageGroup") -> Iterable:
        """Iterate over SummaryPage objects."""
        return iter(self.pages)

    def __len__(self: "SummaryPageGroup") -> int:
        """Lenght of SummaryPage objects."""
        return len(self.pages)


def parse_summary_table(html: HTML) -> Optional[Dict]:
    """Parse data from summary table HTML element."""
    quote_summary = html.find("div#quote-summary", first=True)

    if quote_summary:
        return table_cleaner(quote_summary)

    return None


class SummaryPageNotFound(AttributeError):
    """Raised when summary page data is not found."""


def get_summary_page(
    symbol: str,
    use_fuzzy_search: bool = True,
    page_not_found_ok: bool = False,
    **kwargs,  # noqa: ANN003
) -> Optional[SummaryPage]:
    """Get summary page data.

    Args:
        symbol (str): Ticker symbol
        use_fuzzy_search (bool): If True does a symbol lookup validation prior
            to requesting options page data.
        page_not_found_ok (bool): If True Returns None when page is not found.
        **kwargs: requestor kwargs (session, proxies, and timeout)

    Returns:
        SummaryPage: When data is found.
        None: No data is found and page_not_found_ok is True.

    Raises:
        SummaryPageNotFound: When a page is not found and the page_not_found_ok arg is false.
    """
    if use_fuzzy_search:
        fuzzy_response = fuzzy_search(symbol, first_ticker=True, **kwargs)

        if fuzzy_response:
            symbol = fuzzy_response.symbol

    url = f"https://finance.yahoo.com/quote/{symbol}?p={symbol}"

    response = requestor(url, **kwargs)

    if response.ok:

        html = HTML(html=response.text, url=url)

        quote_data = parse_quote_header_info(html)

        summary_page_data = parse_summary_table(html)

        if quote_data and summary_page_data:
            data = ChainMap(quote_data.dict(), summary_page_data)
            data["symbol"] = symbol
            data["quote"] = quote_data

            return SummaryPage(**data)

    if page_not_found_ok:
        return None

    raise SummaryPageNotFound(f"{symbol} summary page not found.")


def _download_summary_pages_without_threads(
    symbols: List[str],
    use_fuzzy_search: bool,
    page_not_found_ok: bool,
    progress_bar: bool,
    **kwargs,  # noqa: ANN003
) -> Optional[SummaryPageGroup]:

    summary_pages = SummaryPageGroup()

    if use_fuzzy_search:
        valid_symbols = []

        if progress_bar:
            pbar = enlighten.Counter(
                total=len(symbols), desc="Validating symbols...", unit="symbols"
            )

        for symbol in symbols:
            result = fuzzy_search(
                symbol,
                first_ticker=True,
                **kwargs,  # fuzzy search exchange_type, asset_type, session, proxies, timeout
            )

            valid_symbols.append(result)

            if progress_bar:
                pbar.update()

        valid_symbols = filter(lambda s: s is not None, valid_symbols)
        symbols = list(set(s.symbol for s in valid_symbols))

    if progress_bar:
        pbar = enlighten.Counter(
            total=len(symbols), desc="Downloading Summary Data...", unit="symbols"
        )

    for symbol in symbols:
        results = get_summary_page(
            symbol,
            use_fuzzy_search=False,
            page_not_found_ok=page_not_found_ok,
            **kwargs,  # fuzzy search exchange_type, asset_type, session, proxies, timeout
        )

        if results:
            summary_pages.append(results)

        if progress_bar:
            pbar.update()

    if len(summary_pages) > 0:
        return summary_pages

    return None


def _download_summary_pages_with_threads(
    symbols: List[str],
    use_fuzzy_search: bool,
    page_not_found_ok: bool,
    thread_count: int,
    progress_bar: bool,
    **kwargs,  # noqa: ANN003
) -> Optional[SummaryPageGroup]:
    summary_pages = SummaryPageGroup()

    if use_fuzzy_search:
        valid_symbols = []

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = [
                executor.submit(
                    fuzzy_search,
                    symbol,
                    first_ticker=True,
                    **kwargs,  # fuzzy_search: exchange_type, asset_type,
                    # kwargs also for requestor: session, proxies, timeout
                )
                for symbol in symbols
            ]

            if progress_bar:
                pbar = enlighten.Counter(
                    total=len(futures), desc="Validating symbols...", unit="symbols"
                )

            for future in as_completed(futures):
                valid_symbols.append(future.result())

                if progress_bar:
                    pbar.update()

        valid_symbols = filter(lambda s: s is not None, valid_symbols)
        symbols = list(set(s.symbol for s in valid_symbols))

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = [
            executor.submit(
                get_summary_page,
                symbol,
                use_fuzzy_search=False,
                page_not_found_ok=page_not_found_ok,
                **kwargs,  # fuzzy_search: exchange_type, asset_type,
                # kwargs also for requestor: session, proxies, timeout
            )
            for symbol in symbols
        ]

        if progress_bar:
            pbar = enlighten.Counter(
                total=len(futures), desc="Downloading Summary Data...", unit="symbols"
            )

        for future in as_completed(futures):
            results = future.result()

            if results:
                summary_pages.append(results)

            if progress_bar:
                pbar.update()

    if len(summary_pages) > 0:
        return summary_pages

    return None


# Might dump this. Let the user put the pieces together.
def get_multiple_summary_pages(  # pylint: disable=too-many-arguments
    symbols: List[str],
    use_fuzzy_search: bool = True,
    page_not_found_ok: bool = True,
    with_threads: bool = False,
    thread_count: int = 5,
    progress_bar: bool = True,
    **kwargs,  # noqa: ANN003
) -> Optional[SummaryPageGroup]:
    """Get multiple summary pages.

    Args:
        symbols (List[str]): Ticker symbols or company names
        use_fuzzy_search (bool): If True does a symbol lookup validation prior
            to requesting data.
        page_not_found_ok (bool): If True Returns None when page is not found.
        with_threads (bool): True Download using threading else with single thread.
        thread_count (int): number of threads to use if with_threads is set to True.
        **kwargs: requestor kwargs (session, proxies, and timeout)
        progress_bar (bool): If True shows the progress bar else the progress bar
            is not shown.

    Returns:
        SummaryPageGroup: When data is found.
        None: No data is found and page_not_found_ok is True.

    Raises:
        SummaryPageNotFound: When a page is not found and the page_not_found_ok arg is false.
    """
    symbols = list(set(symbols))

    if with_threads:
        return _download_summary_pages_with_threads(
            symbols,
            use_fuzzy_search=use_fuzzy_search,
            page_not_found_ok=page_not_found_ok,
            thread_count=thread_count,
            progress_bar=progress_bar,
            **kwargs,  # fuzzy search exchange_type, asset_type
        )
    return _download_summary_pages_without_threads(
        symbols,
        use_fuzzy_search=use_fuzzy_search,
        page_not_found_ok=page_not_found_ok,
        progress_bar=progress_bar,
        **kwargs,  # fuzzy search exchange_type, asset_type
    )
