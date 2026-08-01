"""Main entry point for the Nifty 100 Streamlit dashboard."""

from pathlib import Path
import sys

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

st.set_page_config(
    page_title="Nifty 100 Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGE_DIR = Path(__file__).resolve().parent / "pages"

if hasattr(st, "navigation"):
    pages = [
        st.Page(PAGE_DIR / "01_home.py", title="Home / Overview", icon="🏠", default=True),
        st.Page(PAGE_DIR / "02_profile.py", title="Company Profile", icon="🏢"),
        st.Page(PAGE_DIR / "03_screener.py", title="Financial Screener", icon="🔎"),
        st.Page(PAGE_DIR / "04_peers.py", title="Peer Comparison", icon="⚖️"),
        st.Page(PAGE_DIR / "05_trends.py", title="Trend Analysis", icon="📈"),
        st.Page(PAGE_DIR / "06_sectors.py", title="Sector Analysis", icon="🧩"),
        st.Page(PAGE_DIR / "07_capital.py", title="Capital Allocation", icon="🗺️"),
        st.Page(PAGE_DIR / "08_reports.py", title="Annual Reports", icon="📄"),
    ]
    navigation = st.navigation(pages)
    navigation.run()
else:
    st.title("Nifty 100 Financial Intelligence Platform")
    st.info(
        "Use the sidebar to open the eight dashboard screens. "
        "Upgrade Streamlit for the enhanced navigation menu."
    )
