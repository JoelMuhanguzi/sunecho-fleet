import os
import requests
import pandas as pd
from datetime import datetime
import pytz

# ── Telegram Configuration ──────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("AAES0ICBKBDHdHML3mln7c3KvOQAet8u3B4")
TELEGRAM_CHAT_ID = os.environ.get("8886673900")
API_URL = "https://noise-sensors-dashboard.herokuapp.com/device_metrics/devices/"
EAT = pytz.timezone("Africa/Kampala")

def send_telegram_alert(message):
    """Fires a formatted markdown message to your Telegram chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

def to_eat(ts):
    return pd.to_datetime(ts, utc=True).tz_convert(EAT)

def get_network_status(ts, bv, now):
    """Calculates status based on Arduino dynamic cycle timing."""
    if pd.isna(ts) or pd.isna(bv): return "NO DATA"
    mins_ago = (now - ts).total_seconds() / 60.0
    
    # Below 3.5V, TX is disabled. If seen in last 12h, it's just hibernating.
    if bv < 3.5:
        return "HIBERNATING" if mins_ago < 720 else "OFFLINE"
            
    if bv >= 4.0: expected = 5
    elif bv >= 3.9: expected = 10
    elif bv >= 3.7: expected = 30
    else: expected = 60
    
    if mins_ago <= (expected * 3) + 10: 
        return "ONLINE"
    return "OFFLINE"

def main():
    print("Fetching fleet data...")
    # Fetch recent pages
    rows, url, n = [], API_URL, 0
    while url and n < 5:
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()
            rows.extend(data.get("results", []))
            url = data.get("next")
            n += 1
        except Exception as e:
            print(f"API Error: {e}")
            break

    if not rows:
        send_telegram_alert("⚠️ *SunEcho API Error*\nCould not fetch fleet telemetry.")
        return

    df = pd.DataFrame(rows)
    df = df[~df["device"].str.contains("SEAS", case=False, na=False)]
    df["time_uploaded"] = df["time_uploaded"].apply(to_eat)
    
    # Get latest record per device
    df = df.sort_values("time_uploaded", ascending=False).drop_duplicates(subset="device")
    
    now = datetime.now(EAT)
    alerts = []

    # Evaluate each device
    for _, row in df.iterrows():
        dev = row["device"]
        bv = row["battery_voltage"]
        ts = row["time_uploaded"]
        
        status = get_network_status(ts, bv, now)
        
        # Check for Critical/Low Battery independent of network status
        if pd.notna(bv) and bv < 3.40:
            alerts.append(f"🔴 *{dev}* CRITICAL BATT: `{bv:.2f}V`")
        elif pd.notna(bv) and bv < 3.60:
            alerts.append(f"🟡 *{dev}* LOW BATT: `{bv:.2f}V`")
            
        # Check Network Status
        if status == "OFFLINE":
            elapsed = (now - ts).total_seconds() / 3600
            alerts.append(f"⚠️ *{dev}* OFFLINE (Last seen {elapsed:.1f}h ago)")
        elif status == "HIBERNATING":
            alerts.append(f"💤 *{dev}* HIBERNATING (TX Paused, Batt: `{bv:.2f}V`)")

    # Only send a message if there are actionable alerts
    if alerts:
        alert_msg = "🚨 *SunEcho Fleet Update*\n\n" + "\n".join(alerts)
        send_telegram_alert(alert_msg)
        print(f"Sent {len(alerts)} alerts to Telegram.")
    else:
        print("All nodes nominal. No alerts sent.")

if __name__ == "__main__":
    main()
