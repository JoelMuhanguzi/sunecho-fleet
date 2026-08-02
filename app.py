import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import pytz

# ── Page Configuration ────────────────────────────────────────────────────────
st.set_page_config(page_title="SunEcho Fleet Monitor", page_icon="🔋", layout="wide")

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL = "https://noise-sensors-dashboard.herokuapp.com"
EAT      = pytz.timezone("Africa/Kampala")
PT       = "plotly_dark"
UNREG    = {"SB1006"}   

C = dict(
    battery="#3fb950", panel="#58a6ff", low="#d29922",
    critical="#f85149", purple="#bc8cff", noise="#ff7b72",
    neutral="#484f58",
)

# ── Helpers ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60) # Caches data for 60 seconds to prevent API spamming
def api_pages(path, max_pages=10):
    out, url, n = [], f"{BASE_URL}{path}", 0
    while url and n < max_pages:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        d = r.json()
        out.extend(d.get("results", []))
        url = d.get("next")
        n += 1
    return out

def to_eat(ts):
    return pd.to_datetime(ts, utc=True).tz_convert(EAT)

def charge_status(bv, pv, dev, batt_low, batt_crit):
    if bv < batt_crit:   return "CRITICAL", "#f85149"
    if bv < batt_low:    return "LOW",      "#d29922"
    if dev in UNREG:     return "OK (unreg)", "#3fb950"
    if pv > bv + 0.10:   return "CHARGING", "#58a6ff"
    return "OK / idle",  "#3fb950"

@st.cache_data(ttl=300)
def load_locations():
    rows = api_pages("/devices/locations/")
    df = pd.DataFrame(rows).rename(columns={"device_name": "device"})
    df["nema_cat"] = df["location_description"].str.extract(r"Category\s+([A-Z])")
    df["label"]    = df["device"] + " — " + df["village"] + ", " + df["division"]
    return df

@st.cache_data(ttl=60)
def load_fleet():
    rows = api_pages("/device_metrics/devices/", max_pages=5)
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
    df["is_day"] = df["time_uploaded"].dt.hour.between(6, 21)
    return df

# ── Sidebar Controls ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    history_pages = st.slider("History depth (pages)", 1, 8, 4)
    batt_low = st.number_input("Low Battery Threshold (V)", value=3.60, step=0.1)
    batt_crit = st.number_input("Critical Battery Threshold (V)", value=3.40, step=0.1)
    
    st.header("🔍 Deep Dive")
    focus_options = ["— none —", "SB1", "SB2", "SB3", "SB4", "SB5", "SB6", "SB7", "SB8", "SB9", "SB10", "SB12", "SB13", "SB1006"]
    focus = st.selectbox("Select Device", focus_options)
    
    if st.button("🔄 Force Refresh Data", type="primary"):
        st.cache_data.clear()

# ── Main Dashboard ────────────────────────────────────────────────────────────
st.title("🔋 SunEcho Fleet Health Dashboard")
st.markdown("Monitoring the noise pollution sensing network across Kampala & Entebbe.")

# Fetch Data
with st.spinner("Fetching fleet telemetry..."):
    loc_df = load_locations()
    fleet_df = load_fleet()

lc = ["device", "city", "division", "parish", "village", "nema_cat", "day_limit", "night_limit", "label"]
fleet_df = fleet_df.merge(loc_df[lc], on="device", how="left")
fleet_df["label"] = fleet_df.apply(lambda r: r["label"] if pd.notna(r.get("label")) else f"{r['device']} — (no location)", axis=1)

fleet_df[["charge_status", "sc"]] = fleet_df.apply(
    lambda r: pd.Series(charge_status(r["battery_voltage"], r["panel_voltage"], r["device"], batt_low, batt_crit)), axis=1)

now = datetime.now(EAT)
fleet_df["mins_ago"] = ((pd.Timestamp(now) - fleet_df["time_uploaded"]).dt.total_seconds().div(60).round(1))
fleet_df["online"] = fleet_df["mins_ago"] < 30
fleet_df["over_day"] = (fleet_df["avg_db_level"] > fleet_df["day_limit"]).fillna(False)

loc_lkp = loc_df.set_index("device")[["day_limit", "night_limit", "label", "nema_cat", "village", "division"]].to_dict("index")

# ── KPIs ──────────────────────────────────────────────────────────────────────
n_on = int(fleet_df["online"].sum())
n_off = len(fleet_df) - n_on
n_chg = int((fleet_df["charge_status"] == "CHARGING").sum())
n_low = int((fleet_df["charge_status"] == "LOW").sum())
n_crit = int((fleet_df["charge_status"] == "CRITICAL").sum())
n_loud = int(fleet_df["over_day"].sum())

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Online", n_on)
col2.metric("Offline/Stale", n_off)
col3.metric("Charging", n_chg)
col4.metric("Low Batt", n_low)
col5.metric("Critical Batt", n_crit)
col6.metric("Over Noise Limit", n_loud)

st.divider()

# ── Alerts ────────────────────────────────────────────────────────────────────
has_alerts = False
for _, r in fleet_df[~fleet_df["online"]].iterrows():
    st.error(f"⚠️ **{r['device']}** OFFLINE — last seen {r['mins_ago']:.0f} min ago · {r['label']}")
    has_alerts = True
for _, r in fleet_df[fleet_df["charge_status"] == "CRITICAL"].iterrows():
    st.error(f"🔴 **{r['device']}** CRITICAL BATTERY · {r['battery_voltage']:.3f} V · {r['label']}")
    has_alerts = True
for _, r in fleet_df[fleet_df["charge_status"] == "LOW"].iterrows():
    st.warning(f"🟡 **{r['device']}** LOW BATTERY · {r['battery_voltage']:.3f} V · {r['label']}")
    has_alerts = True
for _, r in fleet_df[fleet_df["sig_strength"] < 15].iterrows():
    st.warning(f"📶 **{r['device']}** WEAK SIGNAL · RSSI {r['sig_strength']:.0f} · {r['label']}")
    has_alerts = True
for _, r in fleet_df[fleet_df["over_day"]].iterrows():
    st.info(f"🔊 **{r['device']}** avg {r['avg_db_level']:.1f} dB > day limit {r['day_limit']:.0f} dB (Cat {r['nema_cat']}) · {r['label']}")
    has_alerts = True

if not has_alerts:
    st.success("✅ All devices nominal.")

# ── Fleet Table ───────────────────────────────────────────────────────────────
st.subheader("Fleet Summary")
tbl = fleet_df[["device", "label", "nema_cat", "online", "charge_status", "panel_voltage", "battery_voltage", "avg_db_level", "day_limit", "sig_strength", "data_balance", "mins_ago"]].sort_values("device").copy()
tbl.columns = ["Device", "Location", "Cat", "Online", "Status", "Panel V", "Battery V", "Avg dB", "Day Limit", "Signal", "Data (bytes)", "Stale (min)"]
st.dataframe(tbl, use_container_width=True, hide_index=True)

# ── Power Charts ──────────────────────────────────────────────────────────────
st.subheader("Fleet Power Snapshot")
fs = fleet_df.sort_values("device").reset_index(drop=True)

colA, colB = st.columns(2)

with colA:
    fig_batt = go.Figure(go.Bar(x=fs["device"], y=fs["battery_voltage"], marker_color=fs["sc"].tolist()))
    fig_batt.add_hline(y=batt_low, line_dash="dash", line_color=C["low"], annotation_text=f"Low {batt_low} V")
    fig_batt.add_hline(y=batt_crit, line_dash="dash", line_color=C["critical"], annotation_text=f"Critical {batt_crit} V")
    fig_batt.update_layout(template=PT, title="Battery Voltage", yaxis_range=[2.4, 4.7], height=300, margin=dict(t=40, b=40, l=10, r=10))
    st.plotly_chart(fig_batt, use_container_width=True)

    sc = ["#f85149" if v < 15 else "#d29922" if v < 20 else "#3fb950" for v in fs["sig_strength"]]
    fig_signal = go.Figure(go.Bar(x=fs["device"], y=fs["sig_strength"], marker_color=sc))
    fig_signal.update_layout(template=PT, title="GSM Signal Strength", height=280, margin=dict(t=40, b=20, l=10, r=10))
    st.plotly_chart(fig_signal, use_container_width=True)

with colB:
    fig_panel = go.Figure(go.Bar(x=fs["device"], y=fs["panel_voltage"].clip(upper=6.0), marker_color=C["panel"]))
    fig_panel.update_layout(template=PT, title="Panel Voltage (capped 6 V)", height=300, margin=dict(t=40, b=40, l=10, r=10))
    st.plotly_chart(fig_panel, use_container_width=True)

    dc = ["#f85149" if v == 0 else "#d29922" if v < 1000 else "#3fb950" for v in fs["data_balance"]]
    fig_data = go.Figure(go.Bar(x=fs["device"], y=fs["data_balance"], marker_color=dc))
    fig_data.update_layout(template=PT, title="SIM Data Balance (bytes)", height=280, margin=dict(t=40, b=20, l=10, r=10))
    st.plotly_chart(fig_data, use_container_width=True)

# ── Deep-Dive Plot ────────────────────────────────────────────────────────────
if focus and focus != "— none —":
    st.divider()
    st.subheader(f"Deep-Dive: {focus}")
    
    with st.spinner(f"Loading history for {focus}..."):
        df_f = load_history(focus, history_pages)
    
    if not df_f.empty:
        info = loc_lkp.get(focus, {})
        df_f["day_limit"] = info.get("day_limit", 60)
        df_f["night_limit"] = info.get("night_limit", 50)
        df_f["limit"] = df_f.apply(lambda r: r["day_limit"] if r["is_day"] else r["night_limit"], axis=1)
        df_f["over_limit"] = df_f["avg_db_level"] > df_f["limit"]
        
        t = df_f["time_uploaded"]
        nema = info.get("nema_cat", "?")
        
        fig_deep = make_subplots(rows=3, cols=1, shared_xaxes=True, 
                                 subplot_titles=["Voltage Rails", "GSM Signal Strength", f"Noise vs NEMA Cat {nema} Limits"], 
                                 vertical_spacing=0.07, row_heights=[0.37, 0.21, 0.42])
        
        fig_deep.add_trace(go.Scatter(x=t, y=df_f["battery_voltage"], name="Battery V", line=dict(color=C["battery"], width=2.5)), row=1, col=1)
        fig_deep.add_trace(go.Scatter(x=t, y=df_f["panel_voltage"], name="Panel V", line=dict(color=C["panel"], width=2, dash="dash")), row=1, col=1)
        fig_deep.add_trace(go.Scatter(x=t, y=df_f["sig_strength"], name="Signal", line=dict(color=C["purple"], width=2)), row=2, col=1)
        fig_deep.add_trace(go.Scatter(x=t, y=df_f["avg_db_level"], name="Avg dB", line=dict(color=C["noise"], width=2)), row=3, col=1)
        
        fig_deep.update_layout(template=PT, height=700, margin=dict(t=40, b=20, l=45, r=20))
        st.plotly_chart(fig_deep, use_container_width=True)
        
        exc = df_f["over_limit"].mean() * 100
        st.info(f"**Time exceeding NEMA limit (selected window):** `{exc:.1f}%`")
