import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import pytz

# ── Page Configuration ────────────────────────────────────────────────────────
st.set_page_config(page_title="SunEcho Health Monitor", page_icon="🔋", layout="wide")

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
def api_pages(path, max_pages=15):
    out, url, n = [], f"{BASE_URL}{path}", 0
    while url and n < max_pages:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            d = r.json()
            out.extend(d.get("results", []))
            url = d.get("next")
            n += 1
        except Exception:
            break
    return out

def to_eat(ts):
    return pd.to_datetime(ts, utc=True).tz_convert(EAT)

def charge_status(bv, pv, dev, batt_low, batt_crit):
    if pd.isna(bv):      return "NO DATA", "#484f58"
    if bv < batt_crit:   return "CRITICAL", "#f85149"
    if bv < batt_low:    return "LOW",      "#d29922"
    if dev in UNREG:     return "OK (unreg)", "#3fb950"
    if pd.notna(pv) and pv > bv + 0.10: return "CHARGING", "#58a6ff"
    return "OK / idle",  "#3fb950"

def format_last_seen(ts, now):
    if pd.isna(ts): return "No recent data"
    delta = now - ts
    days = delta.days
    secs = delta.seconds
    hours = secs // 3600
    mins = (secs % 3600) // 60
    
    ts_str = ts.strftime('%Y-%m-%d %H:%M')
    if days > 0: return f"{ts_str} ({days}d {hours}h ago)"
    if hours > 0: return f"{ts_str} ({hours}h {mins}m ago)"
    return f"{ts_str} ({mins}m ago)"

@st.cache_data(ttl=300)
def load_locations():
    rows = api_pages("/devices/locations/")
    if not rows: return pd.DataFrame(columns=["device", "label"])
    df = pd.DataFrame(rows).rename(columns={"device_name": "device"})
    df["label"] = df["device"] + " — " + df["village"].fillna("Unknown") + ", " + df["division"].fillna("Unknown")
    return df[["device", "label"]]

@st.cache_data(ttl=60)
def load_fleet():
    # Pulling up to 15 pages to catch devices that have been dead for a few days
    rows = api_pages("/device_metrics/devices/", max_pages=15)
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["time_uploaded"] = df["time_uploaded"].apply(to_eat)
    df = (df.sort_values("time_uploaded", ascending=False)
            .drop_duplicates(subset="device", keep="first")
            .reset_index(drop=True))
    return df

@st.cache_data(ttl=120)
def load_history(device, pages):
    rows = api_pages(f"/device_metrics/device/by-device-id/{device}/history/", max_pages=pages)
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["time_uploaded"] = df["time_uploaded"].apply(to_eat)
    df = df.sort_values("time_uploaded").reset_index(drop=True)
    return df

# ── Sidebar Controls ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    history_pages = st.slider("Deep-dive history depth (pages)", 1, 10, 5)
    batt_low = st.number_input("Low Battery Threshold (V)", value=3.60, step=0.1)
    batt_crit = st.number_input("Critical Battery Threshold (V)", value=3.40, step=0.1)
    
    st.header("🔍 Deep Dive")
    focus_options = ["— none —", "SB1", "SB2", "SB3", "SB4", "SB5", "SB6", "SB7", "SB8", "SB9", "SB10", "SB12", "SB13", "SB1006"]
    focus = st.selectbox("Select Device", focus_options)
    
    if st.button("🔄 Force Refresh Data", type="primary"):
        st.cache_data.clear()

# ── Main Dashboard ────────────────────────────────────────────────────────────
st.title("🔋 Fleet Hardware Health Dashboard")
st.markdown("Detailed power, connectivity, and status telemetry for all registered nodes.")

# Fetch Data
with st.spinner("Fetching fleet telemetry..."):
    loc_df = load_locations()
    fleet_metrics = load_fleet()

# We LEFT JOIN metrics onto locations. This guarantees ALL devices appear.
if not fleet_metrics.empty:
    fleet_df = loc_df.merge(fleet_metrics, on="device", how="left")
else:
    fleet_df = loc_df.copy()
    for col in ["time_uploaded", "battery_voltage", "panel_voltage", "sig_strength", "data_balance"]:
        fleet_df[col] = pd.NaT if col == "time_uploaded" else float('nan')

now = datetime.now(EAT)
fleet_df["last_seen_str"] = fleet_df["time_uploaded"].apply(lambda ts: format_last_seen(ts, now))
fleet_df["online"] = fleet_df["time_uploaded"].apply(lambda ts: False if pd.isna(ts) else (now - ts).total_seconds() / 60 < 30)

fleet_df[["charge_status", "sc"]] = fleet_df.apply(
    lambda r: pd.Series(charge_status(r["battery_voltage"], r["panel_voltage"], r["device"], batt_low, batt_crit)), axis=1)

# ── KPIs ──────────────────────────────────────────────────────────────────────
n_total = len(fleet_df)
n_on = int(fleet_df["online"].sum())
n_off = n_total - n_on
n_chg = int((fleet_df["charge_status"] == "CHARGING").sum())
n_low = int((fleet_df["charge_status"] == "LOW").sum())
n_crit = int((fleet_df["charge_status"] == "CRITICAL").sum())
n_nodata = int((fleet_df["charge_status"] == "NO DATA").sum())

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Total Nodes", n_total)
col2.metric("Online", n_on)
col3.metric("Offline/Stale", n_off)
col4.metric("Charging", n_chg)
col5.metric("Low/Critical Batt", n_low + n_crit)
col6.metric("No Data", n_nodata)

st.divider()

# ── Alerts ────────────────────────────────────────────────────────────────────
has_alerts = False
for _, r in fleet_df[~fleet_df["online"]].iterrows():
    st.error(f"⚠️ **{r['device']}** OFFLINE — {r['last_seen_str']} · {r['label']}")
    has_alerts = True
for _, r in fleet_df[fleet_df["charge_status"] == "CRITICAL"].iterrows():
    st.error(f"🔴 **{r['device']}** CRITICAL BATTERY · {r['battery_voltage']:.3f} V · {r['label']}")
    has_alerts = True
for _, r in fleet_df[fleet_df["charge_status"] == "LOW"].iterrows():
    st.warning(f"🟡 **{r['device']}** LOW BATTERY · {r['battery_voltage']:.3f} V · {r['label']}")
    has_alerts = True
for _, r in fleet_df[(fleet_df["sig_strength"].notna()) & (fleet_df["sig_strength"] < 15)].iterrows():
    st.warning(f"📶 **{r['device']}** WEAK SIGNAL · RSSI {r['sig_strength']:.0f} · {r['label']}")
    has_alerts = True

if not has_alerts:
    st.success("✅ Fleet is 100% nominal and reporting.")

# ── Fleet Table ───────────────────────────────────────────────────────────────
st.subheader("Fleet Summary")
tbl = fleet_df[["device", "label", "online", "charge_status", "panel_voltage", "battery_voltage", "sig_strength", "data_balance", "last_seen_str"]].sort_values("device").copy()
tbl.columns = ["Device", "Location", "Online", "Status", "Panel V", "Battery V", "Signal", "Data (bytes)", "Last Upload"]
st.dataframe(tbl, use_container_width=True, hide_index=True)

# ── Power Charts ──────────────────────────────────────────────────────────────
st.subheader("Fleet Hardware Snapshot")
fs = fleet_df.sort_values("device").reset_index(drop=True)

colA, colB = st.columns(2)

with colA:
    fig_batt = go.Figure(go.Bar(x=fs["device"], y=fs["battery_voltage"], marker_color=fs["sc"].tolist()))
    fig_batt.add_hline(y=batt_low, line_dash="dash", line_color=C["low"], annotation_text=f"Low {batt_low} V")
    fig_batt.add_hline(y=batt_crit, line_dash="dash", line_color=C["critical"], annotation_text=f"Critical {batt_crit} V")
    fig_batt.update_layout(template=PT, title="Battery Voltage", yaxis_range=[2.4, 4.7], height=300, margin=dict(t=40, b=40, l=10, r=10))
    st.plotly_chart(fig_batt, use_container_width=True)

    sig_colors = ["#f85149" if (pd.notna(v) and v < 15) else "#d29922" if (pd.notna(v) and v < 20) else "#3fb950" if pd.notna(v) else "#484f58" for v in fs["sig_strength"]]
    fig_signal = go.Figure(go.Bar(x=fs["device"], y=fs["sig_strength"], marker_color=sig_colors))
    fig_signal.update_layout(template=PT, title="GSM Signal Strength", height=280, margin=dict(t=40, b=20, l=10, r=10))
    st.plotly_chart(fig_signal, use_container_width=True)

with colB:
    # Cap panel voltage in display so outliers don't crush the chart scale
    fig_panel = go.Figure(go.Bar(x=fs["device"], y=fs["panel_voltage"].clip(upper=6.0), marker_color=C["panel"]))
    fig_panel.update_layout(template=PT, title="Panel Voltage (capped 6 V)", height=300, margin=dict(t=40, b=40, l=10, r=10))
    st.plotly_chart(fig_panel, use_container_width=True)

    data_colors = ["#f85149" if v == 0 else "#d29922" if (pd.notna(v) and v < 1000) else "#3fb950" if pd.notna(v) else "#484f58" for v in fs["data_balance"]]
    fig_data = go.Figure(go.Bar(x=fs["device"], y=fs["data_balance"], marker_color=data_colors))
    fig_data.update_layout(template=PT, title="SIM Data Balance (bytes)", height=280, margin=dict(t=40, b=20, l=10, r=10))
    st.plotly_chart(fig_data, use_container_width=True)

# ── Deep-Dive Plot ────────────────────────────────────────────────────────────
if focus and focus != "— none —":
    st.divider()
    st.subheader(f"Hardware Deep-Dive: {focus}")
    
    with st.spinner(f"Loading history for {focus}..."):
        df_f = load_history(focus, history_pages)
    
    if not df_f.empty:
        t = df_f["time_uploaded"]
        
        fig_deep = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                                 subplot_titles=["Voltage Rails", "GSM Signal Strength"], 
                                 vertical_spacing=0.1, row_heights=[0.6, 0.4])
        
        fig_deep.add_trace(go.Scatter(x=t, y=df_f["battery_voltage"], name="Battery V", line=dict(color=C["battery"], width=2.5)), row=1, col=1)
        fig_deep.add_trace(go.Scatter(x=t, y=df_f["panel_voltage"], name="Panel V", line=dict(color=C["panel"], width=2, dash="dash")), row=1, col=1)
        fig_deep.add_trace(go.Scatter(x=t, y=df_f["sig_strength"], name="Signal", line=dict(color=C["purple"], width=2)), row=2, col=1)
        
        fig_deep.update_layout(template=PT, height=500, margin=dict(t=40, b=20, l=45, r=20))
        st.plotly_chart(fig_deep, use_container_width=True)
    else:
        st.info("No historical data found for this device in the selected window.")
