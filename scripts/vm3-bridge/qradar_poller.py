#!/usr/bin/env python3
"""qradar_poller.py — Stage 3: poll QRadar offenses and sync NEW ones into DFIR-IRIS.
Reuses the mapping from qradar_to_iris.py; dedupes via a local state file (poller_state.json).

Modes:
  python qradar_poller.py --once            # one poll against LIVE QRadar (needs QRADAR_TOKEN + QRadar up)
  python qradar_poller.py --dry-run FILE     # read offense(s) from a local JSON file (test without QRadar)

Env: IRIS_KEY (req), QRADAR_TOKEN (req for --once), QRADAR_HOST (default 192.168.56.10),
     IRIS_URL, IRIS_CUSTOMER_ID, IRIS_ALERT_STATUS_ID (inherited from the bridge module).

Lives on VM3 at ~/qradar-iris-bridge/qradar_poller.py. Runs as systemd timer
qradar-iris-poller.timer (every 2 min) -> qradar-iris-poller.service, EnvironmentFile poller.env.
Verified: dry-run pushes+dedupes; timer recurs; live run fails only when QRadar is down.
"""
import os, sys, json, argparse, urllib3, requests
from dfir_iris_client.session import ClientSession
from dfir_iris_client.alert import Alert
from qradar_to_iris import get_severity_map, offense_to_alert, IRIS_URL, IRIS_KEY

urllib3.disable_warnings()

QRADAR_HOST  = os.environ.get("QRADAR_HOST", "192.168.56.10")
QRADAR_TOKEN = os.environ.get("QRADAR_TOKEN", "")
STATE_FILE   = os.environ.get("STATE_FILE",
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "poller_state.json"))
FIELDS = ("id,description,magnitude,status,offense_source,start_time,severity,"
          "credibility,relevance,event_count,categories,log_sources")

def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f).get("sent_offense_ids", []))
    except FileNotFoundError:
        return set()

def save_state(sent):
    with open(STATE_FILE, "w") as f:
        json.dump({"sent_offense_ids": sorted(sent)}, f)

def fetch_open_offenses():
    r = requests.get(f"https://{QRADAR_HOST}/api/siem/offenses",
                     headers={"SEC": QRADAR_TOKEN},
                     params={"filter": "status=OPEN", "fields": FIELDS},
                     verify=False, timeout=30)
    r.raise_for_status()
    return r.json()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="poll live QRadar once")
    ap.add_argument("--dry-run", metavar="FILE", help="read offense(s) from a local JSON file")
    args = ap.parse_args()
    if not IRIS_KEY:
        sys.exit("ERROR: IRIS_KEY not set.")

    if args.dry_run:
        with open(args.dry_run) as f:
            offenses = json.load(f)
        offenses = offenses if isinstance(offenses, list) else [offenses]
        print(f"[dry-run] loaded {len(offenses)} offense(s) from {args.dry_run}")
    elif args.once:
        if not QRADAR_TOKEN:
            sys.exit("ERROR: QRADAR_TOKEN not set (needed for --once).")
        offenses = fetch_open_offenses()
        print(f"[live] QRadar returned {len(offenses)} OPEN offense(s)")
    else:
        sys.exit("Specify --once (live) or --dry-run FILE.")

    sent = load_state()
    new = [o for o in offenses if o.get("id") not in sent]
    print(f"{len(new)} new / {len(offenses)} total (already synced: {len(offenses) - len(new)})")
    if not new:
        print("Nothing new to sync."); return

    sev_map = get_severity_map()
    session = ClientSession(apikey=IRIS_KEY, host=IRIS_URL, ssl_verify=False, agent="qradar-iris-poller")
    alert_client = Alert(session=session)
    for o in new:
        payload = offense_to_alert(o, sev_map)
        resp = alert_client.add_alert(payload)
        if resp.is_success():
            d = resp.get_data()
            print(f"  offense #{o.get('id')} -> IRIS Alert #{d.get('alert_id')} "
                  f"({d['severity']['severity_name']})")
            sent.add(o.get("id"))
        else:
            print(f"  offense #{o.get('id')} FAILED: {resp.get_msg()}")
    save_state(sent)
    print(f"Done. State now tracks {len(sent)} offense(s): {sorted(sent)}")

if __name__ == "__main__":
    main()

# --- systemd (installed on VM3) ---
# /etc/systemd/system/qradar-iris-poller.service
#   [Service] Type=oneshot  User=johnc
#   WorkingDirectory=/home/johnc/qradar-iris-bridge
#   EnvironmentFile=/home/johnc/qradar-iris-bridge/poller.env
#   ExecStart=/home/johnc/qradar-iris-bridge/.venv/bin/python .../qradar_poller.py --once
# /etc/systemd/system/qradar-iris-poller.timer
#   [Timer] OnBootSec=2min  OnUnitActiveSec=2min  Persistent=true  [Install] WantedBy=timers.target
# Manage: sudo systemctl {start,stop,status} qradar-iris-poller.timer ; journalctl -u qradar-iris-poller.service
