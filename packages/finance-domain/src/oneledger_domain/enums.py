"""Enumerations shared by the ledger, API and database."""

from __future__ import annotations

from enum import StrEnum


class AccountNature(StrEnum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"


class AccountKind(StrEnum):
    BANK_SAVINGS = "BANK_SAVINGS"
    BANK_CURRENT = "BANK_CURRENT"
    CASH = "CASH"
    WALLET = "WALLET"
    CREDIT_CARD = "CREDIT_CARD"
    LOAN = "LOAN"
    BROKERAGE = "BROKERAGE"
    INVESTMENT = "INVESTMENT"
    FIXED_DEPOSIT = "FIXED_DEPOSIT"
    OTHER_ASSET = "OTHER_ASSET"
    OTHER_LIABILITY = "OTHER_LIABILITY"

    @property
    def nature(self) -> AccountNature:
        return AccountNature.LIABILITY if self in _LIABILITY_KINDS else AccountNature.ASSET

    @property
    def is_liquid(self) -> bool:
        return self in (AccountKind.BANK_SAVINGS, AccountKind.BANK_CURRENT, AccountKind.CASH, AccountKind.WALLET)

    @property
    def is_bank(self) -> bool:
        return self in (AccountKind.BANK_SAVINGS, AccountKind.BANK_CURRENT)


_LIABILITY_KINDS = frozenset({AccountKind.CREDIT_CARD, AccountKind.LOAN, AccountKind.OTHER_LIABILITY})


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class TransactionStatus(StrEnum):
    POSTED = "POSTED"
    PENDING = "PENDING"
    VOID = "VOID"


class TransactionSourceKind(StrEnum):
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    PROVIDER = "PROVIDER"
    MANUAL_DERIVED = "MANUAL_DERIVED"


class PaymentChannel(StrEnum):
    UPI = "UPI"
    CARD = "CARD"
    NEFT = "NEFT"
    IMPS = "IMPS"
    RTGS = "RTGS"
    ATM = "ATM"
    CASH = "CASH"
    CHEQUE = "CHEQUE"
    NACH = "NACH"
    NETBANKING = "NETBANKING"
    INTERNAL = "INTERNAL"
    OTHER = "OTHER"


class AllocationEffect(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"
    INVESTMENT = "investment"
    LOAN_PRINCIPAL = "loan_principal"
    ADJUSTMENT = "adjustment"
    UNCLASSIFIED = "unclassified"


class ClassificationSource(StrEnum):
    USER = "USER"
    RULE = "RULE"
    MERCHANT = "MERCHANT"
    PATTERN = "PATTERN"
    TRANSFER_MATCH = "TRANSFER_MATCH"
    AI = "AI"
    FALLBACK = "FALLBACK"
    SYSTEM = "SYSTEM"


class TransferStatus(StrEnum):
    SUGGESTED = "SUGGESTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class RelationKind(StrEnum):
    REFUND = "REFUND"
    REVERSAL = "REVERSAL"
    DUPLICATE = "DUPLICATE"
    CORRECTION = "CORRECTION"
    MERGE = "MERGE"
    PENDING_TO_POSTED = "PENDING_TO_POSTED"


class BalanceKind(StrEnum):
    CURRENT = "CURRENT"
    AVAILABLE = "AVAILABLE"
    STATEMENT = "STATEMENT"
    OPENING = "OPENING"


class BalanceSource(StrEnum):
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    PROVIDER = "PROVIDER"
    DERIVED = "DERIVED"


class ReviewKind(StrEnum):
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    TRANSFER_SUGGESTION = "TRANSFER_SUGGESTION"
    UNMATCHED_TRANSFER = "UNMATCHED_TRANSFER"
    RECONCILIATION = "RECONCILIATION"
    SOURCE_REVISION = "SOURCE_REVISION"
    CATEGORIZATION = "CATEGORIZATION"
    LOAN_PAYMENT_SUGGESTION = "LOAN_PAYMENT_SUGGESTION"


class ReviewStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ImportState(StrEnum):
    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    NEEDS_MAPPING = "NEEDS_MAPPING"
    VALIDATING = "VALIDATING"
    PREVIEW_READY = "PREVIEW_READY"
    CONFIRMED = "CONFIRMED"
    COMMITTING = "COMMITTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DELETED = "DELETED"


class ImportRowStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"


class ImportRowResolution(StrEnum):
    IMPORT = "IMPORT"
    SKIP = "SKIP"
    LINK = "LINK"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RecurrenceCadence(StrEnum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


class RecurrenceType(StrEnum):
    SALARY = "SALARY"
    RENT = "RENT"
    EMI = "EMI"
    SUBSCRIPTION = "SUBSCRIPTION"
    INSURANCE = "INSURANCE"
    SIP = "SIP"
    TRANSFER = "TRANSFER"
    BILL = "BILL"
    OTHER = "OTHER"


class InstrumentType(StrEnum):
    STOCK = "STOCK"
    MUTUAL_FUND = "MUTUAL_FUND"
    ETF = "ETF"
    PPF = "PPF"
    NPS = "NPS"
    FIXED_DEPOSIT = "FIXED_DEPOSIT"
    GOLD = "GOLD"
    BOND = "BOND"
    REAL_ESTATE = "REAL_ESTATE"
    OTHER = "OTHER"


class InvestmentAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    CONTRIBUTION = "CONTRIBUTION"
    WITHDRAWAL = "WITHDRAWAL"
    DIVIDEND = "DIVIDEND"
    INTEREST = "INTEREST"
    FEE = "FEE"
