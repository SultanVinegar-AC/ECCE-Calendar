
import io
import re

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
#  ECCE FUNDING LOGIC
# ---------------------------------------------------

def process_funding_calendar(df: pd.DataFrame, date_format: str = "%d/%m/%Y") -> pd.DataFrame:
    """Apply ECCE funding rules to a holidays DataFrame.

    Input columns:
      - Holiday Name
      - Start Date
      - End Date

    Rules:
      - 1-day closure  -> removed
      - 2-day closure  -> Funding Assigned To = "Only Service"
      - 3+ day closure -> Funding Assigned To = "None"
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
        if days == 1:
            return None
        if days == 2:
            return "Only Service"
        return "None"

    df["Funding Assigned To"] = df["__days"].apply(funding_rule)

    # Remove 1-day closures (where rule returned None)
    df = df[df["Funding Assigned To"].notna()]

    # Format dates back to strings
    df["Start Date"] = df["__start_dt"].dt.strftime(date_format)
    df["End Date"] = df["__end_dt"].dt.strftime(date_format)

    # Final column order
    return df[["Holiday Name", "Start Date", "End Date", "Funding Assigned To"]]


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
        with st.spinner("Reading ECCE PDF..."):
            holidays = parse_ecce_pdf_to_df(uploaded_file.read(), date_format=date_format)

        if holidays.empty:
            st.error("No closures detected. Check that this is a valid Service Calendar export.")
            return

        st.success("PDF parsed successfully. Raw closures:")
        st.dataframe(holidays, use_container_width=True)

        with st.spinner("Applying funding rules..."):
            final_df = process_funding_calendar(holidays, date_format=date_format)

        if final_df.empty:
            st.warning("No rows left after applying the rules (no multi-day closures).")
            return

        st.success("Funding rules applied. Here is your processed ECCE calendar:")
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
