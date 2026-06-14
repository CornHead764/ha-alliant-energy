"""Alliant Energy API Client."""
import logging
from datetime import datetime, date, timedelta
from typing import Optional
import json
import aiohttp
import time

_LOGGER = logging.getLogger(__name__)

# Service type that identifies an electric meter in the GetMeterAndPremise
# response (``deviceAttribute5``). Only electric meters work with the
# /UsageAPI/.../Electric endpoints this integration consumes.
ELECTRIC_SERVICE_TYPE = "ERES"


def _safe_float(value) -> float | None:
    """Parse a value to float, returning None instead of raising."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

class AlliantEnergyData:
    """Class to hold the energy data."""
    def __init__(self):
        self.usage_to_date: float | None = None
        self.forecasted_usage: float | None = None
        self.typical_usage: float | None = None
        self.cost_to_date: float | None = None
        self.forecasted_cost: float | None = None
        self.typical_cost: float | None = None
        self.start_date: datetime | None = None
        self.end_date: datetime | None = None
        self.last_api_update: datetime | None = None
        self.last_meter_read: datetime | None = None
        self.cost_per_kwh: float | None = None
        self.customer_charge: float = 0.4932  # Daily customer charge
        self.is_cost_estimated: bool = False
        # Actual billed amount of the most recent completed period, used as a
        # real fallback when no projection exists and a net-export month would
        # otherwise produce a misleading negative estimate.
        self.last_actual_cost: float | None = None
        # Actual net usage (kWh) of that same completed period.
        self.last_actual_usage: float | None = None
        # Peak month so far this year, from the projected endpoint.
        self.highest_usage_this_year: float | None = None
        self.highest_cost_this_year: float | None = None
        # Average daily net usage (negative when net-exporting).
        self.avg_daily_usage: float | None = None
        # Billing-cycle progress derived from start/end dates.
        self.days_into_period: int | None = None
        self.days_remaining: int | None = None

    def calculate_cost(self, kwh: float, days: float) -> float | None:
        """Calculate cost including customer charge."""
        if self.cost_per_kwh is None or kwh is None or days is None:
            return None
        return (kwh * self.cost_per_kwh) + (days * self.customer_charge)

class AlliantEnergyAuthError(Exception):
    """Exception for authentication errors."""
    pass

class AlliantEnergyMeter:
    """A single Alliant Energy meter and the account it belongs to."""

    def __init__(
        self,
        meter_number: str,
        account_number: str,
        premise_number: str,
        service_type: str | None = None,
        address: str | None = None,
    ) -> None:
        self.meter_number = meter_number
        self.account_number = account_number
        self.premise_number = premise_number
        self.service_type = service_type
        self.address = address

    @property
    def label(self) -> str:
        """Human-readable label used in the config flow meter picker."""
        parts = [f"Meter {self.meter_number}"]
        if self.address:
            parts.append(self.address)
        parts.append(f"acct {self.account_number}")
        return " — ".join(parts)

    def to_dict(self) -> dict:
        """Serialize for storage in the config entry."""
        return {
            "meter_number": self.meter_number,
            "account_number": self.account_number,
            "premise_number": self.premise_number,
            "service_type": self.service_type,
            "address": self.address,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlliantEnergyMeter":
        """Rehydrate from config-entry storage."""
        return cls(
            meter_number=data["meter_number"],
            account_number=data["account_number"],
            premise_number=data["premise_number"],
            service_type=data.get("service_type"),
            address=data.get("address"),
        )

# Candidate keys that may carry a street address in the addresses/meter
# payloads. The Alliant backend has changed field names before, so we probe
# several and fall back gracefully.
_ADDRESS_KEYS = (
    "serviceAddress",
    "premiseAddress",
    "address",
    "addressLine1",
    "fullAddress",
    "nickName",
    "nickname",
)

def _extract_address(*objs: dict) -> str | None:
    """Pull a human-friendly address out of the first object that has one."""
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for key in _ADDRESS_KEYS:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None

class AlliantEnergyClient:
    """Client to handle Alliant Energy API interaction."""

    BASE_URL = "https://alliant-svc.smartcmobile.com"

    def __init__(self, username: str, password: str, store: Optional["Store"] = None):
        self._username = username
        self._password = password
        self._store = store
        self._token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expires_at: Optional[float] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._uuid: Optional[str] = None

    def _get_base_headers(self) -> dict:
        """Get base headers used in all requests."""
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "origin": "https://myaccount.alliantenergy.com",
            "priority": "u=1, i",
            "pt": "1",
            "referer": "https://myaccount.alliantenergy.com/",
            "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "st": 'PL',
            "uid": "2",
            "user-agent": "Mozilla/9.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "Accept-Encoding": "gzip"
        }

    async def _load_cached_auth(self) -> bool:
        """Load cached authentication data."""
        if not self._store:
            return False

        auth_data = await self._store.async_load()
        if not auth_data:
            return False

        now = time.time()
        if now >= auth_data.get("expires_at", 0) - 60:
            _LOGGER.debug("Cached token expired")
            return False

        self._token = auth_data.get("token")
        self._refresh_token = auth_data.get("refresh_token")
        self._token_expires_at = auth_data.get("expires_at")
        self._uuid = auth_data.get("uuid")

        _LOGGER.debug("Loaded cached authentication data")
        return bool(self._token and self._uuid)

    async def _save_auth_data(self):
        """Save authentication data to cache."""
        if not self._store:
            return

        auth_data = {
            "token": self._token,
            "refresh_token": self._refresh_token,
            "expires_at": self._token_expires_at,
            "uuid": self._uuid,
        }

        await self._store.async_save(auth_data)
        _LOGGER.debug("Saved authentication data to cache")

    async def _get_token(self, use_refresh_token: bool = False) -> str:
        """Get authentication token."""
        auth_url = f"{self.BASE_URL}/UsermanagementAPI/api/1/Login/auth"

        payload = {
            "username": self._username,
            "password": self._password,
            "guestToken": "",
            "customattributes": {
                "ip": "",
                "client": "Web",
                "version": "-",
                "deviceId": "||Chrome||143||Linux||-||",
                "deviceName": "Chrome",
                "deviceType": 0,
                "os": "Linux"
            }
        }

        headers = {
            **self._get_base_headers(),
            "Content-Type": "application/json",
            "st": "PL",
            "uid": "1"
        }

        _LOGGER.debug("Authenticating with Alliant Energy...")
        async with self._session.post(auth_url, json=payload, headers=headers) as response:
            if response.status != 200:
                raise AlliantEnergyAuthError("Failed to authenticate")

            data = await response.json()
            if data["status"]["type"] != "success":
                raise AlliantEnergyAuthError(f"Authentication failed: {data['status']['message']}")

            self._token = data["data"]["accessToken"]
            self._refresh_token = data["data"]["refreshToken"]
            self._token_expires_at = time.time() + (data["data"]["expiresIn"] * 60)
            self._uuid = data["data"]["user"]["uuid"]

            await self._save_auth_data()

            return self._token

    async def _ensure_token(self):
        """Ensure we have a valid token."""
        if not self._token:
            if not await self._load_cached_auth():
                await self._get_token(use_refresh_token=False)
        elif time.time() >= self._token_expires_at - 60:  # Refresh 1 minute before expiration
            try:
                await self._get_token(use_refresh_token=True)
            except AlliantEnergyAuthError:
                await self._get_token(use_refresh_token=False)

    async def _get_accounts(self) -> list[dict]:
        """Return every account/premise tied to the authenticated user."""
        url = f"{self.BASE_URL}/Services/api/1/Addresses/User/{self._uuid}"

        headers = {
            **self._get_base_headers(),
            "Authorization": f"Bearer {self._token}"
        }

        async with self._session.get(url, headers=headers) as response:
            if response.status != 200:
                raise AlliantEnergyAuthError("Failed to get account details")

            data = await response.json()
            if not data["data"]:
                raise AlliantEnergyAuthError("No account found")

            return data["data"]

    async def _get_meters_for_account(
        self, account_number: str, premise_number: str
    ) -> list[dict]:
        """Return the raw meter list for a single account/premise."""
        url = f"{self.BASE_URL}/Services/api/1/Usages/GetMeterAndPremise"

        headers = {
            **self._get_base_headers(),
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json"
        }

        payload = {
            "accountNumber": account_number,
            "premiseNumber": premise_number,
        }

        async with self._session.post(url, json=payload, headers=headers) as response:
            if response.status != 200:
                raise AlliantEnergyAuthError("Failed to get meter details")

            data = await response.json()
            return data.get("data") or []

    async def async_get_meters(self) -> list[AlliantEnergyMeter]:
        """Discover all electric meters across every account on the login.

        This replaces the old behaviour of silently picking the first
        ``ERES`` meter, which broke when a meter was swapped and the retired
        one stayed visible on the account.
        """
        if not self._session:
            self._session = aiohttp.ClientSession()

        await self._ensure_token()

        meters: list[AlliantEnergyMeter] = []
        seen: set[tuple[str, str, str]] = set()
        for account in await self._get_accounts():
            account_number = account["accountNumber"]
            premise_number = account["premiseNumber"]
            raw_meters = await self._get_meters_for_account(
                account_number, premise_number
            )
            _LOGGER.debug(
                "Account %s premise %s returned %d meter(s): %s",
                account_number,
                premise_number,
                len(raw_meters),
                json.dumps(raw_meters),
            )

            for meter in raw_meters:
                service_type = meter.get("deviceAttribute5")
                if service_type != ELECTRIC_SERVICE_TYPE:
                    _LOGGER.debug(
                        "Skipping non-electric meter %s (service type %s)",
                        meter.get("meterNumber"),
                        service_type,
                    )
                    continue

                meter_number = meter["meterNumber"]
                dedupe_key = (account_number, premise_number, meter_number)
                if dedupe_key in seen:
                    _LOGGER.debug(
                        "Skipping duplicate meter %s on account %s premise %s",
                        meter_number,
                        account_number,
                        premise_number,
                    )
                    continue
                seen.add(dedupe_key)

                meters.append(
                    AlliantEnergyMeter(
                        meter_number=meter_number,
                        account_number=account_number,
                        premise_number=premise_number,
                        service_type=service_type,
                        address=_extract_address(meter, account),
                    )
                )

        if not meters:
            raise AlliantEnergyAuthError("No electric meters found")

        return meters

    async def async_get_data(self, meter: AlliantEnergyMeter) -> AlliantEnergyData:
        """Get the energy data for a single meter."""
        if not self._session:
            self._session = aiohttp.ClientSession()

        await self._ensure_token()

        account_number = meter.account_number
        premise_number = meter.premise_number
        meter_number = meter.meter_number

        today = datetime.now().date()
        first_of_month = today.replace(day=1)
        last_of_month = date(today.year, today.month + 1, 1) if today.month < 12 else date(today.year + 1, 1, 1)

        data = AlliantEnergyData()
        data.last_api_update = datetime.now()

        # Get historical data first
        historical_url = f"{self.BASE_URL}/UsageAPI/api/V1/Electric"
        historical_params = {
            "AccountNumber": f"{premise_number}-{account_number}",
            "MeterNumber": meter_number,
            "From": (today - timedelta(days=365)).strftime("%Y-%m-%d"),
            "To": last_of_month.strftime("%Y-%m-%d"),
            "Uom": "kWh",
            "Periodicity": "MO"
        }

        headers = {
            **self._get_base_headers(),
            "Authorization": f"Bearer {self._token}"
        }

        async with self._session.get(historical_url, params=historical_params, headers=headers) as response:
            if response.status == 200:
                historical_raw = await response.json()
                _LOGGER.debug(
                    "Historical raw for meter %s: %s",
                    meter_number,
                    json.dumps(historical_raw),
                )
                historical = historical_raw["Result"]["electricUsages"]
                if historical:
                    # Sort by reading date for reliability
                    sorted_readings = sorted(
                        historical,
                        key=lambda x: datetime.fromisoformat(x["readingFrom"].replace("Z", "+00:00"))
                    )

                    # Derive cost per kWh from the most recent period that has
                    # positive net consumption. Solar/net-metering customers
                    # routinely have net-export (negative consumption) months,
                    # which can't yield a sensible per-kWh rate, so we walk
                    # backwards to the latest period that actually drew power.
                    for reading in reversed(sorted_readings):
                        period_start = datetime.fromisoformat(reading["readingFrom"].replace("Z", "+00:00"))
                        period_end = datetime.fromisoformat(reading["readingTo"].replace("Z", "+00:00"))
                        days_in_period = (period_end - period_start).days
                        try:
                            total_cost = float(reading["amount"])
                            total_usage = float(reading["consumption"])
                        except (TypeError, ValueError):
                            continue

                        if days_in_period <= 0 or total_usage <= 0:
                            continue

                        # Subtract out customer charge
                        customer_charge_total = days_in_period * data.customer_charge
                        energy_cost = total_cost - customer_charge_total

                        data.cost_per_kwh = energy_cost / total_usage
                        _LOGGER.debug(
                            "Calculated cost per kWh from period %s-%s: $%.4f "
                            "(total cost: $%.2f - customer charge: $%.2f for %d days = $%.2f energy cost / %.1f kWh)",
                            period_start.date(),
                            period_end.date(),
                            data.cost_per_kwh,
                            total_cost,
                            data.customer_charge,
                            days_in_period,
                            energy_cost,
                            total_usage,
                        )
                        break
                    else:
                        _LOGGER.debug(
                            "No period with positive consumption found for "
                            "meter %s; cost per kWh unavailable",
                            meter_number,
                        )

                    # Calculate average period length for billing period
                    # projection. Drop meter-swap stubs and partial reads
                    # (e.g. a 0-day swap row or a 7-day partial) so a normal
                    # ~monthly cycle isn't skewed short.
                    period_lengths = []
                    for reading in sorted_readings:
                        start = datetime.fromisoformat(reading["readingFrom"].replace("Z", "+00:00"))
                        end = datetime.fromisoformat(reading["readingTo"].replace("Z", "+00:00"))
                        period_lengths.append((end - start).days)

                    full_periods = [d for d in period_lengths if d >= 20]
                    avg_period_length = round(
                        sum(full_periods) / len(full_periods)
                        if full_periods
                        else sum(period_lengths) / len(period_lengths)
                    )

                    # Get last completed billing period
                    last_period = sorted_readings[-1]
                    last_period_end = datetime.fromisoformat(last_period["readingTo"].replace("Z", "+00:00"))

                    # Real billed amount and net usage of that period (true
                    # numbers even in a net-export month). last_actual_cost
                    # also feeds the cost fallback below.
                    data.last_actual_cost = _safe_float(last_period.get("amount"))
                    data.last_actual_usage = _safe_float(last_period.get("consumption"))

                    # Calculate current billing period
                    data.start_date = last_period_end.replace(tzinfo=None)
                    data.end_date = data.start_date + timedelta(days=avg_period_length)

                    # Billing-cycle progress (floored at 0 so a freshly
                    # started or overrun cycle doesn't report negatives).
                    now = datetime.now().replace(tzinfo=None)
                    data.days_into_period = max((now - data.start_date).days, 0)
                    data.days_remaining = max((data.end_date - now).days, 0)

                    # Set last meter read
                    data.last_meter_read = datetime.fromisoformat(
                        last_period["readingTo"].replace("Z", "+00:00")
                    )

            elif response.status == 401:
                _LOGGER.error("Authentication failed for historical data. Token may have expired.")
                await self._get_token()
                return await self.async_get_data(meter)

        # Get projected data
        projected_url = f"{self.BASE_URL}/UsageAPI/api/V1/ProjectedElectric"
        projected_params = {
            "AccountNumber": f"{premise_number}-{account_number}",
            "MeterNumber": meter_number,
            "StartDate": first_of_month.strftime("%Y-%m-%d"),
            "EndDate": last_of_month.strftime("%Y-%m-%d"),
            "Type": "0"
        }

        async with self._session.get(projected_url, params=projected_params, headers=headers) as response:
            if response.status == 200:
                projected_raw = await response.json()
                _LOGGER.debug(
                    "Projected raw for meter %s: %s",
                    meter_number,
                    json.dumps(projected_raw),
                )
                projected = projected_raw["Result"]["projectedElectric"]
                try:
                    data.usage_to_date = float(projected["soFarThisMonthProjectedConsumption"])
                except (ValueError, TypeError):
                    data.usage_to_date = None

                try:
                    data.forecasted_usage = float(projected["projectedConsumption"])
                except (ValueError, TypeError):
                    data.forecasted_usage = None

                try:
                    data.typical_usage = float(projected["averageThisYearConsumption"])
                except (ValueError, TypeError):
                    data.typical_usage = None

                data.highest_usage_this_year = _safe_float(
                    projected.get("highestThisYearConsumption")
                )
                data.highest_cost_this_year = _safe_float(
                    projected.get("highestThisYearAmount")
                )
                data.avg_daily_usage = _safe_float(
                    projected.get("averageDailyConsumption")
                )

                # Cost resolution order, for each of cost-to-date and
                # forecasted cost:
                #   1. Alliant's own projected dollar amount, if it gives one
                #   2. our estimate (rate * usage + customer charge), but only
                #      if it's positive
                #   3. the last actually-billed amount from history
                # Steps 2/3 exist because Alliant returns "0" (no projection)
                # for net-metering meters, and a net-export month makes the
                # estimate negative, which would misrepresent the bill.
                days_so_far = None
                if data.start_date is not None:
                    days_so_far = max(
                        (datetime.now().replace(tzinfo=None) - data.start_date).days,
                        0,
                    )

                api_cost = _safe_float(projected.get("soFarThisMonthProjectedAmount"))
                if api_cost is not None and api_cost > 0:
                    data.cost_to_date = api_cost
                else:
                    estimated = None
                    if days_so_far is not None and data.usage_to_date is not None:
                        estimated = data.calculate_cost(data.usage_to_date, days_so_far)
                    if estimated is not None and estimated > 0:
                        data.cost_to_date = estimated
                        data.is_cost_estimated = True
                    elif data.last_actual_cost is not None:
                        data.cost_to_date = data.last_actual_cost
                        data.is_cost_estimated = True

                period_days = None
                if data.start_date is not None and data.end_date is not None:
                    period_days = (data.end_date - data.start_date).days

                api_cost = _safe_float(projected.get("projectedAmount"))
                if api_cost is not None and api_cost > 0:
                    data.forecasted_cost = api_cost
                else:
                    estimated = None
                    if period_days is not None and data.forecasted_usage is not None:
                        estimated = data.calculate_cost(data.forecasted_usage, period_days)
                    if estimated is not None and estimated > 0:
                        data.forecasted_cost = estimated
                        data.is_cost_estimated = True
                    elif data.last_actual_cost is not None:
                        data.forecasted_cost = data.last_actual_cost
                        data.is_cost_estimated = True

                try:
                    data.typical_cost = float(projected["averageThisYearAmount"])
                except (ValueError, TypeError):
                    data.typical_cost = None

            elif response.status == 401:
                _LOGGER.error("Authentication failed for projected data. Token may have expired.")
            else:
                _LOGGER.error("Failed to get projected data: %s", response.status)

        return data

    async def async_close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        """Async enter."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async exit."""
        await self.async_close()
