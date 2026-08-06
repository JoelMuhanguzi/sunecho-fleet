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
        padding-top: 1rem !important;
        padding-bottom: 1rem !important;
        padding-left: 0.6rem !important;
        padding-right: 0.6rem !important;
    }
    [data-testid="stMetricValue"] { font-size: 1.25rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
    .stButton>button { width: 100%; border-radius: 6px; height: 2.8rem; }
    .stDataFrame { width: 100% !important; }
    button[data-baseweb="tab"] { font-size: 1.1rem; padding-top: 1rem; padding-bottom: 1rem; }
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
    """Fallback fetch to get the last known record for devices not in the recent fleet pull."""
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

def charge_status(bv, pv, dev, batt_low, batt_crit, is_online):
    if pd.isna(bv):      return "NO RECORD", "#484f58"
    if not is_online:    return "OFFLINE / STALE", "#8b949e"
    if bv < batt_crit:   return "CRITICAL", "#f85149"
    if bv < batt_low:    return "LOW",      "#d29922"
    if dev in UNREG:     return "OK (unreg)", "#3fb950"
    if pd.notna(pv) and pv > bv + 0.10: return "CHARGING", "#58a6ff"
    return "OK / idle",  "#3fb950"

def format_last_seen(ts, now):
    if pd.isna(ts): 
        return "No history", "Never"
    delta = now - ts
    days = delta.days
    secs = delta.seconds
    
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
    history_pages = st.slider("History depth (pages)", 1, 8, 4)
    if st.button("🔄 Force Refresh Telemetry", type="primary"):
        st.cache_data.clear()

# ── Header ────────────────────────────────────────────────────────────────────
st.title("🔋 SunEcho Ops Monitor")

# Fetch Data
with st.spinner("Connecting to telemetry backend..."):
    loc_df = load_locations()
    fleet_metrics = load_fleet()

if not fleet_metrics.empty:
    fleet_df = loc_df.merge(fleet_metrics, on="device", how="left")
else:
    fleet_df = loc_df.copy()
    for col in ["time_uploaded", "battery_voltage", "panel_voltage", "sig_strength"]:
        fleet_df[col] = pd.NaT if col == "time_uploaded" else float('nan')

# Deep-fetch for missing devices
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

# Calculations
ts_formatted = fleet_df["time_uploaded"].apply(lambda ts: format_last_seen(ts, now))
fleet_df["last_seen_full"] = [t[0] for t in ts_formatted]
fleet_df["elapsed_ago"] = [t[1] for t in ts_formatted]
fleet_df["online"] = fleet_df["time_uploaded"].apply(lambda ts: False if pd.isna(ts) else (now - ts).total_seconds() / 60 < 30)
fleet_df[["charge_status", "sc"]] = fleet_df.apply(lambda r: pd.Series(charge_status(r["battery_voltage"], r["panel_voltage"], r["device"], batt_low, batt_crit, r["online"])), axis=1)

# Formatting for UI
fleet_df["bv_fmt"] = fleet_df["battery_voltage"].apply(lambda v: f"{v:.2f}V" if pd.notna(v) else "N/A")
fleet_df["pv_fmt"] = fleet_df["panel_voltage"].apply(lambda v: f"{v:.2f}V" if pd.notna(v) else "N/A")

# ── Top KPIs ──────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Online", f"{int(fleet_df['online'].sum())}/{len(fleet_df)}")
c2.metric("Offline", len(fleet_df) - int(fleet_df["online"].sum()))
c3.metric("Charging", int((fleet_df["charge_status"] == "CHARGING").sum()))
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
    has_alerts = False
    for _, r in fleet_df[~fleet_df["online"]].iterrows():
        has_alerts, _ = True, st.error(f"⚠️ **{r['device']}** OFFLINE ({r['elapsed_ago']})\n\n📍 *{r['label']}*\n\n📊 **Last Known:** Panel: `{r['pv_fmt']}` | Batt: `{r['bv_fmt']}`")

    for _, r in fleet_df[fleet_df["charge_status"] == "CRITICAL"].iterrows():
        has_alerts, _ = True, st.error(f"🔴 **{r['device']}** CRITICAL BATT (`{r['bv_fmt']}`) · {r['label']}")

    for _, r in fleet_df[fleet_df["charge_status"] == "LOW"].iterrows():
        has_alerts, _ = True, st.warning(f"🟡 **{r['device']}** LOW BATT (`{r['bv_fmt']}`) · {r['label']}")

    if not has_alerts: st.success("✅ All registered devices online and operational.")

    st.subheader("📋 Fleet Hardware Status")
    st.caption("💡 *Tap any row below to trigger a deep-dive trace.*")
    
    # Swapped order: Panel first, then Battery
    tbl = fleet_df[["device", "label", "online", "charge_status", "pv_fmt", "bv_fmt", "elapsed_ago", "last_seen_full"]].sort_values("device").reset_index(drop=True).copy()
    tbl.columns = ["Device", "Location", "Online", "Status", "Last Panel", "Last Batt", "Age", "Last Upload Timestamp"]
    
    selection_event = st.dataframe(tbl, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row")

    selected_device = tbl.iloc[selection_event.selection.rows[0]]["Device"] if selection_event and selection_event.selection and selection_event.selection.rows else None

    # Deep Dive
    st.divider()
    if selected_device:
        st.subheader(f"🔍 Trace: {selected_device}")
        with st.spinner("Fetching trace..."):
            df_f = load_history(selected_device, history_pages)
        if not df_f.empty:
            # Side-by-side Layout for Panel (Left) and Battery (Right)
            col_panel, col_batt = st.columns(2)
            
            with col_panel:
                fig_p = go.Figure(go.Scatter(x=df_f["time_uploaded"], y=df_f["panel_voltage"], name="Panel", line=dict(color=C["panel"])))
                fig_p.update_layout(template=PT, title="Panel Voltage (V)", height=300, margin=dict(t=40, b=20, l=10, r=10))
                st.plotly_chart(fig_p, use_container_width=True)
                
            with col_batt:
                fig_b = go.Figure(go.Scatter(x=df_f["time_uploaded"], y=df_f["battery_voltage"], name="Battery", line=dict(color=C["battery"])))
                fig_b.update_layout(template=PT, title="Battery Voltage (V)", height=300, margin=dict(t=40, b=20, l=10, r=10))
                st.plotly_chart(fig_b, use_container_width=True)

            # Signal Strength spanning full width underneath
            fig_sig = go.Figure(go.Scatter(x=df_f["time_uploaded"], y=df_f["sig_strength"], name="Signal", line=dict(color=C["purple"])))
            fig_sig.update_layout(template=PT, title="GSM Signal Strength", height=250, margin=dict(t=40, b=20, l=10, r=10))
            st.plotly_chart(fig_sig, use_container_width=True)
        else:
            st.info("No recorded trace data found.")
    else:
        st.info("👆 Tap a row in the table to view history.")

# ==============================================================================
# TAB 2: MAP VIEW
# ==============================================================================
with tab_map:
    st.subheader("🗺️ Node Geography & Status")
    map_df = fleet_df.dropna(subset=['lat', 'lon'])
    
    if not map_df.empty:
        map_df["hover_text"] = (
            "<b>" + map_df["device"] + "</b><br>" +
            "📍 " + map_df["label"] + "<br>" +
            "⚡ Panel: " + map_df["pv_fmt"] + " | 🔋 Batt: " + map_df["bv_fmt"] + "<br>" +
            "📡 Last Seen: " + map_df["elapsed_ago"]
        )
        
        fig_map = go.Figure(go.Scattermapbox(
            lat=map_df["lat"],
            lon=map_df["lon"],
            mode='markers',
            marker=go.scattermapbox.Marker(
                size=16, 
                color=map_df["sc"].tolist(), 
                opacity=0.85
            ),
            text=map_df["hover_text"],
            hoverinfo="text"
        ))
        
        # Hardcoded center to precisely frame the geography of Uganda
        center_lat = 1.3733
        center_lon = 32.2903
        
        fig_map.update_layout(
            mapbox_style="carto-darkmatter", 
            mapbox=dict(
                center=dict(lat=center_lat, lon=center_lon),
                zoom=6.5 # Scaled nicely for a country-wide overview
            ),
            margin={"r":0, "t":10, "l":0, "b":0},
            height=600,
            template=PT
        )
        st.plotly_chart(fig_map, use_container_width=True)
    else:
        st.warning("⚠️ No valid GPS coordinates found.")
