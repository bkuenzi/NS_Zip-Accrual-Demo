"""Runtime settings (env) and YAML-backed configuration stores."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("ACCRUAL_CONFIG_DIR", PROJECT_ROOT / "config"))


class Settings(BaseSettings):
    """Environment-driven settings. See .env.example for every knob."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # runtime mode
    mode: Literal["mock", "live"] = Field("mock", alias="ACCRUAL_MODE")
    outbound_mode: Literal["dry_run", "sandbox", "live"] = Field(
        "dry_run", alias="ACCRUAL_OUTBOUND_MODE"
    )
    sandbox_allowed_domains: str = Field("", alias="ACCRUAL_SANDBOX_ALLOWED_DOMAINS")
    sandbox_redirect: str = Field("", alias="ACCRUAL_SANDBOX_REDIRECT")

    # close & policy
    base_currency: str = Field("USD", alias="ACCRUAL_BASE_CURRENCY")
    close_timezone: str = Field("America/New_York", alias="ACCRUAL_CLOSE_TIMEZONE")
    variance_threshold_pct: Decimal = Field(
        Decimal("5.0"), alias="ACCRUAL_VARIANCE_THRESHOLD_PCT"
    )
    materiality_floor: Decimal = Field(Decimal("250.00"), alias="ACCRUAL_MATERIALITY_FLOOR")
    reminder_days: list[int] = Field([3, 7, 10], alias="ACCRUAL_REMINDER_DAYS")
    checkpoint_days: list[int] = Field([5, 10], alias="ACCRUAL_CHECKPOINT_DAYS")
    ad_settle_hours: int = Field(72, alias="ACCRUAL_AD_SETTLE_HOURS")
    reversal_lookback_periods: int = Field(2, alias="ACCRUAL_REVERSAL_LOOKBACK_PERIODS")
    escalation_reraise_business_days: int = Field(
        2, alias="ACCRUAL_ESCALATION_RERAISE_BUSINESS_DAYS"
    )

    # escalation delivery
    escalation_channels: str = Field("email", alias="ACCRUAL_ESCALATION_CHANNELS")
    team_lead_email: str = Field("", alias="ACCRUAL_TEAM_LEAD_EMAIL")
    slack_webhook_url: str = Field("", alias="SLACK_WEBHOOK_URL")

    # NetSuite TBA
    netsuite_account_id: str = Field("", alias="NETSUITE_ACCOUNT_ID")
    netsuite_consumer_key: str = Field("", alias="NETSUITE_CONSUMER_KEY")
    netsuite_consumer_secret: str = Field("", alias="NETSUITE_CONSUMER_SECRET")
    netsuite_token_id: str = Field("", alias="NETSUITE_TOKEN_ID")
    netsuite_token_secret: str = Field("", alias="NETSUITE_TOKEN_SECRET")

    # Zip
    zip_api_base_url: str = Field("https://api.ziphq.com", alias="ZIP_API_BASE_URL")
    zip_api_key: str = Field("", alias="ZIP_API_KEY")

    # Google Ads
    google_ads_developer_token: str = Field("", alias="GOOGLE_ADS_DEVELOPER_TOKEN")
    google_ads_oauth_client_id: str = Field("", alias="GOOGLE_ADS_OAUTH_CLIENT_ID")
    google_ads_oauth_client_secret: str = Field("", alias="GOOGLE_ADS_OAUTH_CLIENT_SECRET")
    google_ads_refresh_token: str = Field("", alias="GOOGLE_ADS_REFRESH_TOKEN")
    google_ads_login_customer_id: str = Field("", alias="GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    google_ads_customer_ids: str = Field("", alias="GOOGLE_ADS_CUSTOMER_IDS")

    # Meta Ads
    meta_access_token: str = Field("", alias="META_ACCESS_TOKEN")
    meta_ad_account_ids: str = Field("", alias="META_AD_ACCOUNT_IDS")

    # email
    mailbox_address: str = Field("accruals@yourco.example", alias="ACCRUAL_MAILBOX_ADDRESS")
    smtp_host: str = Field("", alias="SMTP_HOST")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    smtp_username: str = Field("", alias="SMTP_USERNAME")
    smtp_password: str = Field("", alias="SMTP_PASSWORD")
    imap_host: str = Field("", alias="IMAP_HOST")
    imap_port: int = Field(993, alias="IMAP_PORT")
    imap_username: str = Field("", alias="IMAP_USERNAME")
    imap_password: str = Field("", alias="IMAP_PASSWORD")

    # LLM fallback
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")
    llm_model: str = Field("claude-haiku-4-5", alias="ACCRUAL_LLM_MODEL")

    # storage
    db_path: str = Field("data/accruals.db", alias="ACCRUAL_DB_PATH")
    output_dir: str = Field("output", alias="ACCRUAL_OUTPUT_DIR")
    artifacts_dir: str = Field("output/artifacts", alias="ACCRUAL_ARTIFACTS_DIR")

    company_name: str = Field("YourCo", alias="ACCRUAL_COMPANY_NAME")

    @property
    def effective_outbound_mode(self) -> str:
        # Mock mode can never touch a real mailbox.
        return "dry_run" if self.mode == "mock" else self.outbound_mode

    @property
    def escalation_channel_list(self) -> list[str]:
        return [c.strip() for c in self.escalation_channels.split(",") if c.strip()]

    def require(self, names_by_env: dict[str, str], purpose: str) -> None:
        """Fail fast with a labeled list of the missing env vars a command needs."""
        missing = [env for env, value in names_by_env.items() if not value]
        if missing:
            raise ConfigError(
                f"Missing configuration for {purpose}: set "
                + ", ".join(sorted(missing))
                + " (see .env.example)"
            )


class ConfigError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ── YAML stores ──────────────────────────────────────────────────────────────


@dataclass
class GLMapping:
    gl_account: str
    cost_center: str


@dataclass
class GLMappingStore:
    """config/gl_mappings.yaml — vendor coding, subsidiary maps, threshold overrides."""

    accrued_liability_account: str
    vendors: dict[str, GLMapping]
    zip_business_units: dict[str, str]
    ad_accounts: dict[str, dict[str, str]]
    vendor_variance_overrides: dict[str, Decimal]
    gl_variance_overrides: dict[str, Decimal]

    @classmethod
    def load(cls, path: Path | None = None) -> GLMappingStore:
        path = path or CONFIG_DIR / "gl_mappings.yaml"
        raw = yaml.safe_load(path.read_text()) or {}
        overrides = raw.get("variance_overrides") or {}
        return cls(
            accrued_liability_account=str(raw.get("accrued_liability_account", "2150")),
            vendors={
                vid: GLMapping(str(m["gl_account"]), str(m["cost_center"]))
                for vid, m in (raw.get("vendors") or {}).items()
            },
            zip_business_units={
                str(k): str(v) for k, v in (raw.get("zip_business_units") or {}).items()
            },
            ad_accounts={
                str(k): {kk: str(vv) for kk, vv in v.items()}
                for k, v in (raw.get("ad_accounts") or {}).items()
            },
            vendor_variance_overrides={
                str(k): Decimal(str(v)) for k, v in (overrides.get("vendors") or {}).items()
            },
            gl_variance_overrides={
                str(k): Decimal(str(v)) for k, v in (overrides.get("gl_accounts") or {}).items()
            },
        )

    def coding_for(self, vendor_id: str) -> GLMapping | None:
        return self.vendors.get(vendor_id)

    def variance_threshold_for(
        self, vendor_id: str, gl_account: str | None, default: Decimal
    ) -> Decimal:
        if vendor_id in self.vendor_variance_overrides:
            return self.vendor_variance_overrides[vendor_id]
        if gl_account and gl_account in self.gl_variance_overrides:
            return self.gl_variance_overrides[gl_account]
        return default


@dataclass
class ContactRecord:
    vendor_id: str
    name: str
    email: str
    verified: bool


@dataclass
class VendorContactStore:
    """config/vendor_contacts.yaml — managed via `accrual-agent contacts`."""

    path: Path
    contacts: dict[str, ContactRecord] = field(default_factory=dict)
    allowed_domains: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> VendorContactStore:
        path = path or CONFIG_DIR / "vendor_contacts.yaml"
        raw = yaml.safe_load(path.read_text()) if path.exists() else {}
        raw = raw or {}
        contacts = {
            vid: ContactRecord(
                vendor_id=vid,
                name=str(c.get("name", "")),
                email=str(c.get("email", "")),
                verified=bool(c.get("verified", False)),
            )
            for vid, c in (raw.get("contacts") or {}).items()
        }
        allowed = {
            vid: [str(d) for d in doms]
            for vid, doms in (raw.get("allowed_domains") or {}).items()
        }
        return cls(path=path, contacts=contacts, allowed_domains=allowed)

    def get(self, vendor_id: str) -> ContactRecord | None:
        return self.contacts.get(vendor_id)

    def verified_contact(
        self, vendor_id: str, vendor_domains: list[str]
    ) -> tuple[ContactRecord | None, str | None]:
        """Return (contact, None) when sendable, else (None, block_reason).

        Verified requires BOTH the explicit flag and a domain cross-check
        against the NetSuite vendor master (or the per-vendor allowlist).
        """
        rec = self.contacts.get(vendor_id)
        if rec is None or not rec.email:
            return None, "no contact on file"
        if not rec.verified:
            return None, f"contact {rec.email} not marked verified"
        domain = rec.email.rsplit("@", 1)[-1].lower()
        acceptable = {d.lower() for d in vendor_domains}
        acceptable.update(d.lower() for d in self.allowed_domains.get(vendor_id, []))
        if acceptable and domain not in acceptable:
            return None, (
                f"contact domain {domain} does not match vendor master domains "
                f"{sorted(acceptable)}"
            )
        return rec, None

    def upsert(self, record: ContactRecord) -> None:
        self.contacts[record.vendor_id] = record
        self._write()

    def _write(self) -> None:
        data = {
            "contacts": {
                vid: {"name": c.name, "email": c.email, "verified": c.verified}
                for vid, c in sorted(self.contacts.items())
            },
            "allowed_domains": self.allowed_domains,
        }
        self.path.write_text(yaml.safe_dump(data, sort_keys=False))
