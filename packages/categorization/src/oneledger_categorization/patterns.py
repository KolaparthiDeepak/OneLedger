"""Built-in deterministic merchant patterns for common Indian payees.

Matching runs on normalized description text (upper-case alphanumerics). Patterns never
execute user input; they are fixed, reviewed regular expressions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from oneledger_domain.enums import AllocationEffect as E


@dataclass(frozen=True, slots=True)
class Pattern:
    regex: re.Pattern[str]
    merchant: str | None
    category: str
    effect: E
    direction: int  # -1 debit only, +1 credit only, 0 either
    confidence: Decimal


def _p(
    expr: str, merchant: str | None, category: str, effect: E = E.EXPENSE, direction: int = -1, conf: str = "0.90"
) -> Pattern:
    return Pattern(re.compile(expr), merchant, category, effect, direction, Decimal(conf))


BUILTIN_PATTERNS: list[Pattern] = [
    # Transfers & card payments first: they must never become consumption expense.
    _p(
        r"\b(CC PAYMENT|CREDIT CARD PAYMENT|CARD PAYMENT|CRED CLUB|CREDCLUB|BILLDESK CC|CC BILL)\b",
        None,
        "TRANSFERS_CARD_PAYMENT",
        E.TRANSFER,
        0,
        "0.80",
    ),
    _p(
        r"\b(PAYMENT RECEIVED|PAYMENT THANK YOU|THANK YOU FOR YOUR PAYMENT)\b",
        None,
        "TRANSFERS_CARD_PAYMENT",
        E.TRANSFER,
        1,
        "0.80",
    ),
    _p(r"\b(SELF TRANSFER|OWN ACCOUNT|TO SELF|SELF TRF|SWEEP)\b", None, "TRANSFERS_SELF", E.TRANSFER, 0, "0.75"),
    # Income
    _p(r"\b(SALARY|SAL CREDIT|PAYROLL)\b", None, "INCOME_SALARY", E.INCOME, 1, "0.92"),
    _p(
        r"\b(INT PD|INTEREST CREDIT|INT CREDIT|SB INT|INTEREST PAID|CREDIT INTEREST)\b",
        None,
        "INCOME_INTEREST",
        E.INCOME,
        1,
        "0.90",
    ),
    _p(r"\b(DIVIDEND|DIV)\b", None, "INCOME_DIVIDEND", E.INCOME, 1, "0.85"),
    _p(r"\b(REFUND|REVERSAL|CASHBACK REVERSAL|RETURN)\b", None, "OTHER", E.EXPENSE, 1, "0.70"),
    # Food
    _p(r"\bSWIGGY\s*INSTAMART\b|\bINSTAMART\b", "Swiggy Instamart", "FOOD_GROCERIES"),
    _p(r"\bSWIGGY\b", "Swiggy", "FOOD_DELIVERY", conf="0.97"),
    _p(r"\bZOMATO\b", "Zomato", "FOOD_DELIVERY", conf="0.97"),
    _p(r"\b(BIGBASKET|BIG BASKET|BBNOW)\b", "BigBasket", "FOOD_GROCERIES", conf="0.95"),
    _p(r"\bBLINKIT\b|\bGROFERS\b", "Blinkit", "FOOD_GROCERIES", conf="0.95"),
    _p(r"\bZEPTO\b", "Zepto", "FOOD_GROCERIES", conf="0.95"),
    _p(r"\b(DMART|AVENUE SUPERMARTS)\b", "DMart", "FOOD_GROCERIES"),
    _p(r"\bJIOMART\b", "JioMart", "FOOD_GROCERIES"),
    _p(
        r"\b(STARBUCKS|CAFE COFFEE DAY|CCD|DOMINOS|MCDONALDS|KFC|PIZZA HUT|BURGER KING|SUBWAY|HALDIRAM)\b",
        None,
        "FOOD_RESTAURANTS",
        conf="0.90",
    ),
    _p(r"\b(RESTAURANT|RESTAURANTS|CAFE|DHABA|BAKERY|BAKERS|EATERY)\b", None, "FOOD_RESTAURANTS", conf="0.70"),
    # Everyday food words, often only present in the UPI payee name or the note typed by the payer.
    _p(
        r"\b(PIZZA|PIZZERIA|BURGER|BIRYANI|BIRIYANI|DOSA|IDLI|CHAI|COFFEE|KITCHEN|CANTEEN|TIFFIN|DARSHINI|"
        r"SWEETS|MITHAI|JUICE|ICE ?CREAM|SHAWARMA|MOMOS|CHAAT|BBQ|GRILL|FOOD ?COURT|SNACKS|LUNCH|DINNER|"
        r"BREAKFAST|MEALS|FOODS|BHAVAN|UDUPI|NAASHTA|NASHTA)\b",
        None,
        "FOOD_RESTAURANTS",
        conf="0.65",
    ),
    _p(
        r"\b(KIRANA|SUPERMARKET|SUPER MARKET|HYPERMARKET|PROVISION|PROVISIONS|GENERAL STORES?|VEGETABLES?|"
        r"FRUITS?|MILK|DAIRY|NANDINI|GROCERY|GROCERIES|MORE RETAIL|RATNADEEP|SPENCERS|NATURES BASKET)\b",
        None,
        "FOOD_GROCERIES",
        conf="0.65",
    ),
    _p(r"\b(MEDICAL|MEDICALS|CHEMIST|CHEMISTS|PHARMA|DENTAL|DOCTOR|LAB|LABS)\b", None, "HEALTHCARE", conf="0.65"),
    _p(r"\b(PARKING|TOLL|TOLL PLAZA)\b", None, "TRANSPORT", conf="0.70"),
    # Transport
    _p(r"\bUBER\b", "Uber", "TRANSPORT_CAB", conf="0.93"),
    _p(r"\bOLA\b|\bANI TECHNOLOGIES\b", "Ola", "TRANSPORT_CAB", conf="0.90"),
    _p(r"\bRAPIDO\b", "Rapido", "TRANSPORT_CAB"),
    _p(r"\b(DMRC|BMRCL|METRO RAIL|MUMBAI METRO|HMRL|CMRL)\b", None, "TRANSPORT_METRO"),
    _p(
        r"\b(IOCL|INDIAN OIL|BPCL|BHARAT PETROLEUM|HPCL|HINDUSTAN PETROLEUM|SHELL|PETROL|FUEL|NAYARA)\b",
        None,
        "TRANSPORT_FUEL",
        conf="0.85",
    ),
    _p(r"\b(REDBUS|KSRTC|MSRTC|TSRTC|APSRTC)\b", None, "TRANSPORT_BUS"),
    _p(r"\bIRCTC\b", "IRCTC", "TRANSPORT_TRAIN"),
    _p(r"\b(INDIGO|INTERGLOBE|AIR INDIA|VISTARA|AKASA|SPICEJET)\b", None, "TRANSPORT_FLIGHT"),
    _p(r"\b(MAKEMYTRIP|MAKE MY TRIP|GOIBIBO|CLEARTRIP|YATRA|IXIGO|AGODA|BOOKING COM|AIRBNB|OYO)\b", None, "TRAVEL"),
    _p(r"\b(FASTAG|NETC)\b", None, "TRANSPORT"),
    # Shopping
    _p(r"\b(AMAZON PAY|AMAZON|AMZN)\b", "Amazon", "SHOPPING", conf="0.80"),
    _p(r"\bFLIPKART\b", "Flipkart", "SHOPPING"),
    _p(
        r"\b(MYNTRA|AJIO|NYKAA|MEESHO|TATA CLIQ|DECATHLON|IKEA|CROMA|RELIANCE DIGITAL|LIFESTYLE|WESTSIDE)\b",
        None,
        "SHOPPING",
    ),
    # Subscriptions / entertainment
    _p(r"\bNETFLIX\b", "Netflix", "SUBSCRIPTIONS", conf="0.97"),
    _p(r"\bSPOTIFY\b", "Spotify", "SUBSCRIPTIONS", conf="0.97"),
    _p(r"\b(YOUTUBE PREMIUM|YOUTUBEPREMIUM|GOOGLE YOUTUBE)\b", "YouTube Premium", "SUBSCRIPTIONS"),
    _p(r"\b(PRIME VIDEO|AMAZON PRIME|PRIMEVIDEO)\b", "Amazon Prime", "SUBSCRIPTIONS", conf="0.93"),
    _p(r"\b(HOTSTAR|DISNEY|JIOCINEMA|JIOHOTSTAR|SONYLIV|ZEE5)\b", None, "SUBSCRIPTIONS"),
    _p(
        r"\b(APPLE COM BILL|ITUNES|ICLOUD|GOOGLE PLAY|GOOGLE ONE|MICROSOFT|ADOBE|"
        r"OPENAI|CHATGPT|ANTHROPIC|NOTION|GITHUB)\b",
        None,
        "SUBSCRIPTIONS",
        conf="0.85",
    ),
    _p(r"\b(BOOKMYSHOW|PVR|INOX|CINEPOLIS|DISTRICT)\b", None, "ENTERTAINMENT"),
    # Housing & bills
    _p(r"\b(NOBROKER|RENT)\b", None, "HOUSING_RENT", conf="0.80"),
    _p(r"\b(MAINTENANCE|MYGATE|NOBROKERHOOD|SOCIETY)\b", None, "HOUSING_MAINTENANCE", conf="0.75"),
    _p(
        r"\b(ELECTRICITY|BESCOM|MSEDCL|TATA POWER|ADANI ELEC|TNEB|BSES|CESC|WATER BILL|GAS BILL|MAHANAGAR GAS|IGL)\b",
        None,
        "HOUSING_UTILITIES",
    ),
    _p(
        r"\b(AIRTEL|JIO|VODAFONE|VI PREPAID|BSNL|ACT FIBERNET|HATHWAY|TATA PLAY|BROADBAND|DTH)\b",
        None,
        "HOUSING_UTILITIES",
        conf="0.80",
    ),
    # Health / education / insurance
    _p(
        r"\b(APOLLO|PHARMEASY|NETMEDS|TATA 1MG|1MG|MEDPLUS|PRACTO|HOSPITAL|CLINIC|PHARMACY|DIAGNOSTIC)\b",
        None,
        "HEALTHCARE",
    ),
    _p(r"\b(UDEMY|COURSERA|BYJU|UNACADEMY|SCHOOL|COLLEGE|UNIVERSITY|TUITION)\b", None, "EDUCATION"),
    _p(
        r"\b(LIC|LIFE INSURANCE|HDFC LIFE|ICICI PRU|SBI LIFE|STAR HEALTH|POLICYBAZAAR|INSURANCE|PREMIUM|ACKO|DIGIT)\b",
        None,
        "INSURANCE",
        conf="0.85",
    ),
    # Investments
    _p(
        r"\b(ZERODHA|GROWW|KUVERA|UPSTOX|INDMONEY|PAYTM MONEY|COIN|BSE STAR|NSE CLEARING|ICCL|INDIAN CLEARING|"
        r"MUTUAL FUND|SIP|NPS|PPF|SMALLCASE|ANGEL ONE)\b",
        None,
        "INVESTMENTS",
        E.INVESTMENT,
        -1,
        "0.85",
    ),
    # Loans
    _p(r"\b(EMI|LOAN)\b", None, "LOANS", conf="0.75"),
    # Taxes & fees
    _p(r"\b(CBDT|INCOME TAX|ADVANCE TAX|TDS|GST PAYMENT|PROFESSIONAL TAX)\b", None, "TAXES"),
    _p(
        r"\b(CHARGES|CHRG|CHG|FEE|FEES|ANNUAL FEE|PENALTY|SMS ALERT|AMC|GST ON|LATE PAYMENT|FINANCE CHARGE)\b",
        None,
        "FEES",
        conf="0.80",
    ),
    # Cash
    _p(r"\b(ATM|ATW|NWD|CASH WDL|CASH WITHDRAWAL)\b", None, "CASH", conf="0.85"),
]


def match_builtin(normalized: str, amount_sign: int) -> Pattern | None:
    for p in BUILTIN_PATTERNS:
        if p.direction and p.direction != amount_sign:
            continue
        if p.regex.search(normalized):
            return p
    return None
