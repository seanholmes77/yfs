from enum import Enum


class AssetTypes(str, Enum):
    CURRENCY = "Currency"
    ETF = "ETF"
    EQUITY = "Equity"
    FUND = "Fund"
    FUTURES = "Futures"
    INDEX = "Index"
    MONEY_MARKET = "MoneyMarket"
    OPTION = "Option"
    SHITCOINS = "CRYPTOCURRENCY"


VALID_ASSET_TYPES = [asset.value for asset in AssetTypes]