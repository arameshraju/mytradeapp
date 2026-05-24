import streamlit as st

st.set_page_config(
    page_title="Trading App",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

pg = st.navigation([
    st.Page("pages/0_Dashboard.py", title="Dashboard",     icon="🏠"),
    st.Page("pages/1_Chart.py",     title="Chart",          icon="📈"),
    st.Page("pages/2_Options.py",   title="Options Chain",  icon="🎯"),
])
pg.run()