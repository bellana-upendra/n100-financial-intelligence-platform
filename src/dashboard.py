from __future__ import annotations

import sqlite3
import streamlit as st
import pandas as pd

from config import get_settings


def load_data():

    settings = get_settings()

    if not settings.database_path.exists():
        st.error("Database not found. Run ETL first.")
        return None

    conn = sqlite3.connect(settings.database_path)

    tables = pd.read_sql_query(
        """
        SELECT name 
        FROM sqlite_master 
        WHERE type='table' 
        AND name NOT LIKE 'sqlite_%'
        """,
        conn,
    )["name"].tolist()

    summary = []

    for table in sorted(tables):
        count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

        summary.append({"Table": table, "Rows": count})

    conn.close()

    return pd.DataFrame(summary)


def main():

    st.set_page_config(page_title="N100 Financial Intelligence Platform", layout="wide")

    st.title("📊 N100 Financial Intelligence Platform")

    st.subheader("Sprint 4 Dashboard")

    df = load_data()

    if df is not None:

        col1, col2 = st.columns(2)

        with col1:
            st.metric("Total Tables", len(df))

        with col2:
            st.metric("Total Rows", int(df["Rows"].sum()))

        st.subheader("Database Overview")

        st.dataframe(df, use_container_width=True)

        st.bar_chart(df.set_index("Table"))


if __name__ == "__main__":
    main()
