from pathlib import Path
import io
import re
import json
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import pandas as pd
import streamlit as st
import pdfplumber


# ---------------------------------------------------
#  ECCE PDF PARSER
# ---------------------------------------------------

def parse_ecce_pdf_to_df(pdf_bytes: bytes, date_format: str = "%d/%m/%Y") -> pd.DataFrame:
    """Parse an ECCE Service Calendar PDF (Hive export) into a DataFrame.

    Output columns:
      - Holiday Name
      - Start Date
      - End Date
    """
    rows = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""

            # Look for the section that lists closure dates
            if "We will be closed on the following dates:" not in text:
                continue

            after = text.split("We will be closed on the following dates:")[-1]
            lines = [l.strip() for l in after.splitlines() if l.strip()]

            # Accepted formats:
            #   25/08/2025 - 29/08/2025
            #   02/02/2026
            pattern = re.compile(
                r"^(\d{1,2}/\d{1,2}/\d{4})(?:\s*-\s*(\d{1,2}/\d{1,2}/\d{4}))?$"
            )

            for line in lines:
                # Stop at footer-ish content
                if line.lower().startswith("this calendar has been registered"):
                    break

                match = pattern.match(line)
                if not match:
                    continue

                start_str, end_str = match.groups()

                try:
                    start_dt = pd.to_datetime(start_str, dayfirst=True)
                    end_dt = pd.to_datetime(end_str, dayfirst=True) if end_str else start_dt
                except Exception:
                    # Skip rows with invalid dates
                    continue

                rows.append(
                    {
                        "Holiday Name": "Closed",
                        "Start Date": start_dt.strftime(date_format),
                        "End Date": end_dt.strftime(date_format),
                    }
                )

    if not rows:
        # Return an empty dataframe with the correct columns
        return pd.DataFrame(columns=["Holiday Name", "Start Date", "End Date"])

    return pd.DataFrame(rows, columns=["Holiday Name", "Start Date", "End Date"])



# ---------------------------------------------------
#  OPENHOLIDAYS (Public Holidays) HELPERS

# Local on-disk cache (survives API outages; Render filesystem is ephemeral but fine day-to-day)
# Using /tmp is usually writable on hosted platforms.
BANK_HOLIDAY_CACHE_PATH = Path("/tmp/openholidays_ie_cache.json")


def get_bank_holidays_ie_with_cache(valid_from: str, valid_to: str, language_iso_code: str = "EN") -> dict:
    """Return {YYYY-MM-DD: Holiday Name}.

    Priority:
      1) OpenHolidays API (fresh)
      2) Local cache on disk (last-known-good)
      3) Empty dict (safe fallback)

    If the API succeeds, we overwrite the cache.
    """
    try:
        holidays = fetch_openholidays_public_holidays_ie(
            valid_from=valid_from,
            valid_to=valid_to,
            language_iso_code=language_iso_code,
        )

        payload = {
            "countryIsoCode": "IE",
            "validFrom": valid_from,
            "validTo": valid_to,
            "generatedAtUtc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "holidays": holidays,
        }
        BANK_HOLIDAY_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return holidays

    except Exception as e:
        # API failed: try cached
        try:
            if BANK_HOLIDAY_CACHE_PATH.exists():
                cached = json.loads(BANK_HOLIDAY_CACHE_PATH.read_text(encoding="utf-8"))
                holidays = cached.get("holidays", {}) or {}
                st.warning("OpenHolidays is unavailable - using cached bank holidays.")
                return holidays
        except Exception:
            pass

        # No cache available - fail safe
        st.warning(f"OpenHolidays is unavailable and no cache was found. No bank-holiday removals applied. ({e})")
        return {}

# ---------------------------------------------------

@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)  # cache for 24h
def fetch_openholidays_public_holidays_ie(valid_from: str, valid_to: str, language_iso_code: str = "EN") -> dict:
    """Fetch Irish public holidays from OpenHolidays API for a given date range.

    valid_from / valid_to: YYYY-MM-DD
    Returns: dict[date_iso (YYYY-MM-DD)] = holiday_name
    """
    base_url = "https://openholidaysapi.org/PublicHolidays"
    params = {
        "countryIsoCode": "IE",
        "languageIsoCode": language_iso_code,
        "validFrom": valid_from,
        "validTo": valid_to,
    }
    url = f"{base_url}?{urlencode(params)}"
    req = Request(url, headers={"accept": "text/json"})

    try:
        with urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
            data = json.loads(payload)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
        # Bubble up a clean exception for the UI layer
        raise RuntimeError(f"OpenHolidays API request failed: {e}")

    holidays = {}

    for item in data or []:
        # Dates can appear in slightly different shapes; handle common variants defensively
        start_raw = item.get("startDate") or item.get("start") or item.get("date")
        end_raw = item.get("endDate") or item.get("end") or start_raw

        # Sometimes startDate/endDate might be nested objects
        if isinstance(start_raw, dict):
            start_raw = start_raw.get("date") or start_raw.get("value")
        if isinstance(end_raw, dict):
            end_raw = end_raw.get("date") or end_raw.get("value")

        if not start_raw:
            continue

        # Name can be a string or a list of translations
        name = item.get("name") or item.get("holidayName") or item.get("title") or "Bank Holiday"
        if isinstance(name, list) and name:
            # Try to find EN translation first
            en = next((n for n in name if isinstance(n, dict) and (n.get("language") == "EN" or n.get("languageIsoCode") == "EN")), None)
            picked = en or name[0]
            if isinstance(picked, dict):
                name = picked.get("text") or picked.get("name") or picked.get("value") or "Bank Holiday"
            else:
                name = str(picked)
        elif isinstance(name, dict):
            name = name.get("text") or name.get("name") or name.get("value") or "Bank Holiday"
        else:
            name = str(name)

        try:
            start_dt = datetime.strptime(str(start_raw), "%Y-%m-%d").date()
            end_dt = datetime.strptime(str(end_raw), "%Y-%m-%d").date()
        except ValueError:
            # If the API ever returns a datetime string, try slicing the date part
            try:
                start_dt = datetime.strptime(str(start_raw)[:10], "%Y-%m-%d").date()
                end_dt = datetime.strptime(str(end_raw)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue

        # Expand multi-day holidays to individual dates (rare but safe)
        cur = start_dt
        while cur <= end_dt:
            holidays[cur.isoformat()] = name
            cur = cur + timedelta(days=1)

    return holidays


def calendar_range_for_openholidays(df: pd.DataFrame, date_format: str) -> tuple[str, str]:
    """Return (valid_from, valid_to) as YYYY-MM-DD.

    valid_from = earliest Start Date in the uploaded calendar
    valid_to   = 31 Dec of the year containing the last End Date in the uploaded calendar
    """
    tmp = df.copy()
    tmp["__start_dt"] = pd.to_datetime(tmp["Start Date"], format=date_format, errors="coerce")
    tmp["__end_dt"] = pd.to_datetime(tmp["End Date"], format=date_format, errors="coerce")
    tmp = tmp.dropna(subset=["__start_dt", "__end_dt"])

    if tmp.empty:
        # Safe fallback: current year window
        today = date.today()
        return today.replace(month=1, day=1).isoformat(), today.replace(month=12, day=31).isoformat()

    min_start = tmp["__start_dt"].min().date()
    max_end = tmp["__end_dt"].max().date()
    end_of_year = date(max_end.year, 12, 31)

    return min_start.isoformat(), end_of_year.isoformat()

# ---------------------------------------------------
#  ECCE FUNDING LOGIC
# ---------------------------------------------------

def process_funding_calendar(df: pd.DataFrame, date_format: str = "%d/%m/%Y") -> pd.DataFrame:
    """Apply ECCE funding rules to a holidays DataFrame.

    Rules:
      - 1-day closure  -> removed
      - 2-day closure  -> Funding Received to = "Only Service"
      - 3+ day closure -> Funding Received to = "None"
    """
    df = df.copy()

    # Parse dates
    df["__start_dt"] = pd.to_datetime(df["Start Date"], format=date_format, errors="coerce")
    df["__end_dt"] = pd.to_datetime(df["End Date"], format=date_format, errors="coerce")

    # Drop rows with bad dates
    df = df.dropna(subset=["__start_dt", "__end_dt"])

    # Inclusive day length
    df["__days"] = (df["__end_dt"] - df["__start_dt"]).dt.days + 1

    def funding_rule(days: int):
        # For your ECCE export, treat 1- and 2-day closures as "Only Service".
        # (Bank-holiday single-day closures will be excluded separately.)
        if days in (1, 2):
            return "Only Service"
        return None

    # Identify bank holidays (via OpenHolidays) so we can exclude 1-day bank-holiday closures
    valid_from, valid_to = calendar_range_for_openholidays(df, date_format=date_format)

    bank_holidays = get_bank_holidays_ie_with_cache(valid_from=valid_from, valid_to=valid_to, language_iso_code="EN")
    bank_holiday_lookup = set(bank_holidays.keys())
# A row is a bank-holiday single-day closure if Start Date is a bank holiday AND duration is 1 day
    df["__is_bank_holiday_single_day"] = df["__start_dt"].dt.date.astype(str).isin(bank_holiday_lookup) & (df["__days"] == 1)

    # Exclude only 1-day closures that are bank holidays
    df = df[~df["__is_bank_holiday_single_day"]]

    # Apply funding classification (kept rows)
    df["Funding Received to"] = [funding_rule(int(d)) for d in df["__days"]]
# Format dates back to strings
    df["Start Date"] = df["__start_dt"].dt.strftime(date_format)
    df["End Date"] = df["__end_dt"].dt.strftime(date_format)

    # Final column order
    return df[["Holiday Name", "Start Date", "End Date", "Funding Received to"]]


# ---------------------------------------------------
#  STREAMLIT APP
# ---------------------------------------------------

def main():
    st.set_page_config(page_title="ECCE Calendar Tool", page_icon="📚")

    st.title("📚 ECCE Calendar Tool (PDF Only)")
    st.write(
        "Upload an ECCE Service Calendar PDF and I'll:\n"
        "- Extract closures automatically\n"
        "- Remove 1-day closures\n"
        "- Tag 2-day closures as 'Only Service'\n"
        "- Tag 3+ day closures as 'None'\n"
        "Then you can download a processed CSV."
    )

    uploaded_file = st.file_uploader("Upload your ECCE calendar PDF", type=["pdf"])

    if not uploaded_file:
        st.info("Upload a PDF file to continue.")
        return

    date_format = "%d/%m/%Y"

    if st.button("Process ECCE Calendar"):
        with st.spinner("Reading ECCE PDF and extracting closures..."):
            holidays = parse_ecce_pdf_to_df(uploaded_file.read(), date_format=date_format)

        if holidays.empty:
            st.error("No closures detected. Check that this is a valid Service Calendar export.")
            return

        # Apply rules
        with st.spinner("Applying funding rules..."):
            final_df = process_funding_calendar(holidays, date_format=date_format)

        if final_df.empty:
            st.warning("No rows left after applying the rules (no multi-day closures).")
            return

        # Celebration message FIRST
        st.info("🎉 Feel free to worship Krish & Andy, cos we've fuggin done it again 😎")

        # Then show only the processed file
        st.success("Here is your processed ECCE calendar:")
        st.dataframe(final_df, use_container_width=True)

        csv_buf = io.StringIO()
        final_df.to_csv(csv_buf, index=False)

        st.download_button(
            "⬇️ Download Processed CSV",
            csv_buf.getvalue().encode("utf-8"),
            "ecce_calendar_processed.csv",
            "text/csv",
        )


if __name__ == "__main__":
    main()
