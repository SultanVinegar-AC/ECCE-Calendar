import io

import pandas as pd
import streamlit as st


# ---------------------------------------------------
#  ECCE LOGIC ONLY
# ---------------------------------------------------

def process_funding_calendar(df, col_holiday, col_start, col_end, date_format):
    df = df.copy()

    # Parse dates
    df["__start_dt"] = pd.to_datetime(df[col_start], format=date_format, errors="coerce")
    df["__end_dt"] = pd.to_datetime(df[col_end], format=date_format, errors="coerce")

    bad_dates = df[df["__start_dt"].isna() | df["__end_dt"].isna()]
    if not bad_dates.empty:
        st.warning(
            f"{len(bad_dates)} row(s) had invalid dates and were skipped. "
            "Check your date format or fix those rows in the source file."
        )
    df = df.dropna(subset=["__start_dt", "__end_dt"])

    # Length in days (inclusive)
    df["__days"] = (df["__end_dt"] - df["__start_dt"]).dt.days + 1

    # Apply rules
    def funding_rule(days):
        if days == 1:
            return None
        if days == 2:
            return "Only Service"
        return "None"  # 3+ days

    df["Funding Assigned To"] = df["__days"].apply(funding_rule)

    # Remove 1-day closures
    df = df[df["Funding Assigned To"].notna()]

    # Format dates
    df[col_start] = df["__start_dt"].dt.strftime(date_format)
    df[col_end] = df["__end_dt"].dt.strftime(date_format)

    # Final export columns
    out_df = df[[col_holiday, col_start, col_end, "Funding Assigned To"]].rename(
        columns={
            col_holiday: "Holiday Name",
            col_start: "Start Date",
            col_end: "End Date"
        }
    )

    return out_df


def main():
    st.set_page_config(page_title="ECCE Calendar Tool", page_icon="📚")

    st.title("📚 ECCE Calendar Tool")
    st.write("Upload an ECCE calendar and apply funding rules (1/2/3+ days).")

    uploaded_file = st.file_uploader(
        "Upload your ECCE calendar file (CSV/Excel)",
        type=["csv", "xlsx", "xls"]
    )

    if not uploaded_file:
        st.info("Upload a CSV or Excel file to continue.")
        return

    try:
        if uploaded_file.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Could not read file: {e}")
        return

    if df.empty:
        st.error("The file appears to be empty.")
        return

    st.write("Detected columns:", list(df.columns))

    col_holiday = st.selectbox("Holiday Name Column", df.columns.tolist())
    col_start = st.selectbox("Start Date Column", df.columns.tolist())
    col_end = st.selectbox("End Date Column", df.columns.tolist())

    date_format = st.text_input("Date format", "%d/%m/%Y")

    if st.button("Process ECCE Calendar"):
        with st.spinner("Applying ECCE rules..."):
            try:
                out_df = process_funding_calendar(df, col_holiday, col_start, col_end, date_format)
            except Exception as e:
                st.error(f"Processing failed: {e}")
                return

        if out_df.empty:
            st.warning("No rows left after applying the rules.")
            return

        st.success("Done! Preview below.")
        st.dataframe(out_df, use_container_width=True)

        csv_buffer = io.StringIO()
        out_df.to_csv(csv_buffer, index=False)

        st.download_button(
            "⬇️ Download Processed ECCE CSV",
            csv_buffer.getvalue().encode("utf-8"),
            "ecce_calendar_processed.csv",
            "text/csv"
        )


if __name__ == "__main__":
    main()
