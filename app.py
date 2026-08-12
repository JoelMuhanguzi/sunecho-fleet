import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import pytz

# ── Page Configuration ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SunEcho Ops Monitor", 
    page_icon="🔋", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ── Custom CSS for Mobile & Field Responsiveness ──────────────────────────────
st.markdown("""
<style>
    .main .block-container {
        padding-top: 0.2rem !important; 
        padding-bottom: 1rem !important;
        padding-left: 0.6rem !important;
        padding-right: 0.6rem !important;
    }
    [data-testid="stMetricValue"] { font-size: 1.25rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
    .stButton>button { width: 100%; border-radius: 6px; height: 2.8rem; }
    .stDataFrame { width: 100% !important; }
    button[data-baseweb="tab"] { font-size: 1.1rem; padding-top: 1rem; padding-bottom: 1rem; }
    h3 { margin-top: 0rem !important; padding-top: 0rem !important; padding-bottom: 0.2rem !important; }
</style>
""", unsafe_allow_html=True)

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL = "https://noise-sensors-dashboard.herokuapp.com"
EAT      = pytz.timezone("Africa/Kampala")
PT       = "plotly_dark"
UNREG    = {"SB1006"}   

C = dict(
    battery="#3fb950", panel="#58a6ff", low="#d29922",
    critical="#f85149", purple="#bc8cff", neutral="#484f58",
)

# Timeframe mapping: (Hours to look back, Pages to fetch from API)
TIMEFRAMES = {
    "Last 12 Hours": (12, 15),
    "Last 24 Hours": (24, 30),
    "Last 3 Days": (72, 80),
    "Last 7 Days": (168, 150)
}

# ── Helpers ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def api_pages(path, max_pages=6):
    out, url, n = [], f"{BASE_URL}{path}", 0
    while url and n < max_pages:
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            d = r.json()
            out.extend(d.get("results", []))
            url = d.get("next")
            n += 1
        except Exception:
            break
    return out

def fetch_absolute_latest(device):
    try:
        url = f"{BASE_URL}/device_metrics/device/by-device-id/{device}/history/"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                return results[0] 
    except Exception:
        pass
    return None

def to_eat(ts):
    return pd.to_datetime(ts, utc=True).tz_convert(EAT)

def get_network_status(ts, bv, now):
    if pd.isna(ts) or pd.isna(bv): return False, "NO DATA", "#484f58"
    mins_ago = (now - ts).total_seconds() / 60.0
    
    if bv < 3.5:
        if mins_ago < 720: return False, "HIBERNATING", "#bc8cff" 
        else: return False, "OFFLINE", "#8b949e"
            
    if bv >= 4.0: expected_interval = 5
    elif bv >= 3.9: expected_interval = 10
    elif bv >= 3.7: expected_interval = 30
    else: expected_interval = 60
    
    if mins_ago <= (expected_interval * 3) + 10: return True, "ONLINE", "#3fb950"
    return False, "OFFLINE", "#8b949e"

def charge_status(bv, pv, dev, batt_low, batt_crit):
    if pd.isna(bv):      return "NO RECORD", "#484f58"
    if bv < batt_crit:   return "CRITICAL", "#f85149"
    if bv < batt_low:    return "LOW",      "#d29922"
    if dev in UNREG:     return "OK (unreg)", "#3fb950"
    if pd.notna(pv) and pv > bv + 0.10: return "CHARGING", "#58a6ff"
    return "OK / idle",  "#3fb950"

def format_last_seen(ts, now):
    if pd.isna(ts): return "No history", "Never"
    delta = now - ts
    days, secs = delta.days, delta.seconds
    date_str = ts.strftime('%m-%d %H:%M')
    ago_str = f"{days}d {secs // 3600}h ago" if days > 0 else f"{secs // 3600}h {(secs % 3600) // 60}m ago" if secs >= 3600 else f"{(secs % 3600) // 60}m ago"
    return f"{date_str} ({ago_str})", ago_str

@st.cache_data(ttl=300)
def load_locations():
    rows = api_pages("/devices/locations/")
    if not rows: return pd.DataFrame(columns=["device", "label", "lat", "lon"])
    df = pd.DataFrame(rows)
    lat_col = "latitude" if "latitude" in df.columns else "lat" if "lat" in df.columns else None
    lon_col = "longitude" if "longitude" in df.columns else "lon" if "lon" in df.columns else None
    df = df.rename(columns={"device_name": "device", lat_col: "lat", lon_col: "lon"})
    df = df[~df["device"].str.contains("SEAS", case=False, na=False)]
    df["label"] = df["device"] + " — " + df["village"].fillna("Unknown") + ", " + df["division"].fillna("Unknown")
    if "lat" in df.columns and "lon" in df.columns:
        df["lat"], df["lon"] = pd.to_numeric(df["lat"], errors="coerce"), pd.to_numeric(df["lon"], errors="coerce")
    else:
        df["lat"], df["lon"] = float('nan'), float('nan')
    return df[["device", "label", "lat", "lon"]]

@st.cache_data(ttl=60)
def load_fleet():
    rows = api_pages("/device_metrics/devices/", max_pages=6)
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[~df["device"].str.contains("SEAS", case=False, na=False)]
    df["time_uploaded"] = df["time_uploaded"].apply(to_eat)
    return (df.sort_values("time_uploaded", ascending=False)
              .drop_duplicates(subset="device", keep="first")
              .reset_index(drop=True))

@st.cache_data(ttl=120)
def load_history(device, pages):
    rows = api_pages(f"/device_metrics/device/by-device-id/{device}/history/", max_pages=pages)
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["time_uploaded"] = df["time_uploaded"].apply(to_eat)
    return df.sort_values("time_uploaded").reset_index(drop=True)

# ── Sidebar Controls ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Field Thresholds")
    batt_low = st.number_input("Low Battery Threshold (V)", value=3.60, step=0.1)
    batt_crit = st.number_input("Critical Battery Threshold (V)", value=3.40, step=0.1)
    if st.button("🔄 Force Refresh Telemetry", type="primary"):
        st.cache_data.clear()

# ── Compact Header ────────────────────────────────────────────────────────────
st.markdown("### 🔋 SunEcho Ops Monitor")

with st.spinner("Connecting to telemetry backend..."):
    loc_df = load_locations()
    fleet_metrics = load_fleet()

if not fleet_metrics.empty:
    fleet_df = loc_df.merge(fleet_metrics, on="device", how="left")
else:
    fleet_df = loc_df.copy()
    for col in ["time_uploaded", "battery_voltage", "panel_voltage", "sig_strength"]:
        fleet_df[col] = pd.NaT if col == "time_uploaded" else float('nan')

missing_idx = fleet_df[fleet_df["time_uploaded"].isna()].index
for idx in missing_idx:
    dev = fleet_df.loc[idx, "device"]
    latest_rec = fetch_absolute_latest(dev)
    if latest_rec:
        fleet_df.loc[idx, "time_uploaded"] = to_eat(latest_rec.get("time_uploaded"))
        fleet_df.loc[idx, "battery_voltage"] = latest_rec.get("battery_voltage")
        fleet_df.loc[idx, "panel_voltage"] = latest_rec.get("panel_voltage")
        fleet_df.loc[idx, "sig_strength"] = latest_rec.get("sig_strength")

now = datetime.now(EAT)

ts_formatted = fleet_df["time_uploaded"].apply(lambda ts: format_last_seen(ts, now))
fleet_df["last_seen_full"] = [t[0] for t in ts_formatted]
fleet_df["elapsed_ago"] = [t[1] for t in ts_formatted]

status_data = fleet_df.apply(lambda r: pd.Series(get_network_status(r["time_uploaded"], r["battery_voltage"], now)), axis=1)
fleet_df[["is_online", "net_status", "net_color"]] = status_data
fleet_df["online"] = fleet_df["is_online"] 

fleet_df[["charge_status", "sc"]] = fleet_df.apply(lambda r: pd.Series(charge_status(r["battery_voltage"], r["panel_voltage"], r["device"], batt_low, batt_crit)), axis=1)

fleet_df["bv_fmt"] = fleet_df["battery_voltage"].apply(lambda v: f"{v:.2f}V" if pd.notna(v) else "N/A")
fleet_df["pv_fmt"] = fleet_df["panel_voltage"].apply(lambda v: f"{v:.2f}V" if pd.notna(v) else "N/A")

# ── Dynamic KPIs ──────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Online", int((fleet_df['net_status'] == 'ONLINE').sum()))
c2.metric("Sleep", int((fleet_df['net_status'] == 'HIBERNATING').sum()))
c3.metric("Offline", int((fleet_df['net_status'] == 'OFFLINE').sum()))
c4.metric("Low/Crit", int((fleet_df["charge_status"].isin(["LOW", "CRITICAL"])).sum()))

if st.button("🔄 Tap to Refresh Status"):
    st.cache_data.clear()

st.divider()

# ── Tabs Setup ────────────────────────────────────────────────────────────────
tab_dash, tab_map = st.tabs(["📋 Hardware List", "🗺️ Fleet Map"])

# ==============================================================================
# TAB 1: LIST AND CHARTS
# ==============================================================================
with tab_dash:
    hib_nodes = fleet_df[fleet_df["net_status"] == "HIBERNATING"]
    off_nodes = fleet_df[fleet_df["net_status"] == "OFFLINE"]
    crit_nodes = fleet_df[fleet_df["charge_status"] == "CRITICAL"]
    low_nodes = fleet_df[fleet_df["charge_status"] == "LOW"]
    
    total_alerts = len(hib_nodes) + len(off_nodes) + len(crit_nodes) + len(low_nodes)
    
    if total_alerts > 0:
        with st.expander(f"🚨 Tap to view {total_alerts} Active Status Alerts", expanded=False):
            for _, r in hib_nodes.iterrows():
                st.markdown(f"💤 **{r['device']}** HIBERNATING (Batt < 3.5V, TX Paused) · 📍 *{r['label']}*")
            for _, r in off_nodes.iterrows():
                st.markdown(f"⚠️ **{r['device']}** OFFLINE ({r['elapsed_ago']}) · 📍 *{r['label']}* · Batt: `{r['bv_fmt']}`")
            for _, r in crit_nodes.iterrows():
                st.markdown(f"🔴 **{r['device']}** CRITICAL BATT (`{r['bv_fmt']}`) · {r['label']}")
            for _, r in low_nodes.iterrows():
                st.markdown(f"🟡 **{r['device']}** LOW BATT (`{r['bv_fmt']}`) · {r['label']}")
    else:
        st.success("✅ All registered devices online and operational.")

    fleet_df["num_id"] = fleet_df["device"].str.extract(r'(\d+)').astype(float)
    fleet_df["sort_weight"] = fleet_df["net_status"].map({"OFFLINE": 0, "HIBERNATING": 1, "ONLINE": 2})
    tbl = fleet_df.sort_values(by=["sort_weight", "num_id"]).reset_index(drop=True).copy()
    
    tbl = tbl[["device", "label", "net_status", "charge_status", "pv_fmt", "bv_fmt", "elapsed_ago", "last_seen_full"]]
    tbl.columns = ["Device", "Location", "Network", "Power", "Last Panel", "Last Batt", "Age", "Last Upload Timestamp"]
    
    # Removed mobile-hostile row selection from the dataframe
    st.dataframe(tbl, use_container_width=True, hide_index=True, height=400)

    # ── Mobile Friendly Deep Dive Selector ────────────────────────────────────
    st.divider()
    st.subheader("🔍 Deep-Dive Trace Analysis")
    
    col_dev, col_time = st.columns(2)
    with col_dev:
        dev_list = ["— Select a Node —"] + sorted(fleet_df["device"].dropna().unique().tolist())
        selected_device = st.selectbox("Sensor to inspect:", dev_list)
    with col_time:
        time_window = st.selectbox("Timeframe:", list(TIMEFRAMES.keys()))

    if selected_device != "— Select a Node —":
        hours_back, pages_to_fetch = TIMEFRAMES[time_window]
        
        with st.spinner(f"Fetching {time_window.lower()} trace for {selected_device}..."):
            df_f = load_history(selected_device, pages=pages_to_fetch)
            
        if not df_f.empty:
            # Filter the dataframe to match the exact timeframe selected
            cutoff_time = now - pd.Timedelta(hours=hours_back)
            df_f = df_f[df_f["time_uploaded"] >= cutoff_time]
            
            if not df_f.empty:
                # 1. Combined Power Dynamics Chart
                fig_power = go.Figure()
                fig_power.add_trace(go.Scatter(x=df_f["time_uploaded"], y=df_f["battery_voltage"], name="Battery", line=dict(color=C["battery"], width=3)))
                fig_power.add_trace(go.Scatter(x=df_f["time_uploaded"], y=df_f["panel_voltage"], name="Panel", line=dict(color=C["panel"], dash="dot", width=2)))
                
                fig_power.update_layout(
                    template=PT, 
                    title=f"Power Dynamics ({time_window})", 
                    height=350, 
                    margin=dict(t=40, b=20, l=10, r=10),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                st.plotly_chart(fig_power, use_container_width=True)

                # 2. GSM Signal Chart
                fig_sig = go.Figure(go.Scatter(x=df_f["time_uploaded"], y=df_f["sig_strength"], name="Signal", line=dict(color=C["purple"])))
                fig_sig.update_layout(template=PT, title="GSM Signal Strength", height=250, margin=dict(t=40, b=20, l=10, r=10))
                st.plotly_chart(fig_sig, use_container_width=True)
            else:
                st.info(f"No recorded data found for {selected_device} in the {time_window}.")
        else:
            st.info(f"No history found for {selected_device}.")
    else:
        st.info("👆 Use the dropdown above to select a sensor for detailed power charts.")

# ==============================================================================
# TAB 2: MAP VIEW
# ==============================================================================
with tab_map:
    map_df = fleet_df.dropna(subset=['lat', 'lon'])
    
    if not map_df.empty:
        map_df["hover_text"] = (
            "<b>" + map_df["device"] + "</b><br>" +
            "📍 " + map_df["label"] + "<br>" +
            "📡 Net: " + map_df["net_status"] + " (" + map_df["elapsed_ago"] + ")<br>" +
            "⚡ Panel: " + map_df["pv_fmt"] + " | 🔋 Batt: " + map_df["bv_fmt"]
        )
        
        fig_map = go.Figure(go.Scattermapbox(
            lat=map_df["lat"],
            lon=map_df["lon"],
            mode='markers',
            marker=go.scattermapbox.Marker(
                size=16, 
                color=map_df["net_color"].tolist(),
                opacity=0.85
            ),
            text=map_df["hover_text"],
            hoverinfo="text"
        ))
        
        center_lat = map_df["lat"].mean()
        center_lon = map_df["lon"].mean()
        
        lat_spread = map_df["lat"].max() - map_df["lat"].min()
        lon_spread = map_df["lon"].max() - map_df["lon"].min()
        max_spread = max(lat_spread, lon_spread)
        
        if max_spread == 0: zoom_level = 14
        elif max_spread < 0.1: zoom_level = 12
        elif max_spread < 0.5: zoom_level = 10
        else: zoom_level = 7
        
        fig_map.update_layout(
            mapbox_style="open-street-map",
            mapbox=dict(
                center=dict(lat=center_lat, lon=center_lon),
                zoom=zoom_level
            ),
            margin={"r":0, "t":10, "l":0, "b":0},
            height=600,
            template=PT
        )
        st.plotly_chart(fig_map, use_container_width=True)
    else:
        st.warning("⚠️ No valid GPS coordinates found.")
