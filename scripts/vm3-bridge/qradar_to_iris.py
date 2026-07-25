#!/usr/bin/env python3
"""
qradar_to_iris.py — DFIR platform bridge (Stage 1/2).
Maps a REAL QRadar offense (JSON) into a DFIR-IRIS alert and creates it via the IRIS REST API.
Severity is resolved by NAME from IRIS at runtime (install-agnostic).

Lives on VM3 at ~/qradar-iris-bridge/qradar_to_iris.py (run inside the .venv).
Usage:  python qradar_to_iris.py <offense.json>
Env: IRIS_KEY (required), IRIS_URL (default https://127.0.0.1),
     IRIS_CUSTOMER_ID (default 1), IRIS_ALERT_STATUS_ID (default 2 = 'New'),
     QRADAR_HOST (default 192.168.56.10).

IRIS v2.4.27 severity IDs (from GET /manage/severities/list):
  Medium=1, Unspecified=2, Informational=3, Low=4, High=5, Critical=6

Proven end-to-end: QRadar offense #1 (mag 3) -> IRIS Alert (severity Low, status New).
Get a real offense JSON with:
  curl -s -k -H "SEC: $TOKEN" "https://<qradar>/api/siem/offenses/<id>"
"""
import os, sys, json, datetime, urllib3, requests
from dfir_iris_client.session import ClientSession
from dfir_iris_client.alert import Alert
urllib3.disable_warnings()

IRIS_URL             = os.environ.get("IRIS_URL", "https://127.0.0.1")
IRIS_KEY             = os.environ.get("IRIS_KEY", "")
IRIS_CUSTOMER_ID     = int(os.environ.get("IRIS_CUSTOMER_ID", "1"))
IRIS_ALERT_STATUS_ID = int(os.environ.get("IRIS_ALERT_STATUS_ID", "2"))
QRADAR_HOST          = os.environ.get("QRADAR_HOST", "192.168.56.10")

def get_severity_map():
    r = requests.get(f"{IRIS_URL}/manage/severities/list",
                     headers={"Authorization": f"Bearer {IRIS_KEY}"}, verify=False, timeout=10)
    r.raise_for_status()
    return {s["severity_name"]: s["severity_id"] for s in r.json()["data"]}

def magnitude_to_name(m):
    if m >= 9: return "Critical"
    if m >= 7: return "High"
    if m >= 4: return "Medium"
    if m >= 2: return "Low"
    return "Informational"

def offense_to_alert(o, sev_map):
    mag   = o.get("magnitude", 5)
    desc  = (o.get("description") or "QRadar Offense").strip()
    src   = o.get("offense_source", "")
    cats  = ", ".join(o.get("categories") or [])
    ls    = (o.get("log_sources") or [{}])[0].get("name", "n/a")
    start = o.get("start_time")
    evt_time = (datetime.datetime.utcfromtimestamp(start/1000).strftime("%Y-%m-%dT%H:%M:%S")
                if start else datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"))
    body = (f"QRadar Offense #{o.get('id')} - {desc}\n"
            f"Source: {src}\n"
            f"Magnitude {mag} | Severity {o.get('severity')} | "
            f"Credibility {o.get('credibility')} | Relevance {o.get('relevance')}\n"
            f"Events: {o.get('event_count')} | Categories: {cats}\n"
            f"Log source: {ls} | Status: {o.get('status')}")
    return {
        "alert_title": f"[QRadar Offense #{o.get('id')}] {desc}",
        "alert_description": body,
        "alert_source": "IBM QRadar",
        "alert_source_ref": f"qradar-offense-{o.get('id')}",
        "alert_source_link": f"https://{QRADAR_HOST}/console/do/sem/offensesummary?appName=Sem&pageId=OffenseSummary&summaryId={o.get('id')}",
        "alert_source_content": o,
        "alert_severity_id": sev_map.get(magnitude_to_name(mag), sev_map.get("Medium")),
        "alert_status_id": IRIS_ALERT_STATUS_ID,
        "alert_customer_id": IRIS_CUSTOMER_ID,
        "alert_tags": f"qradar,offense,{(o.get('status') or '').lower()}",
        "alert_source_event_time": evt_time,
    }

def main():
    if not IRIS_KEY:
        sys.exit("ERROR: IRIS_KEY env var not set.")
    path = sys.argv[1] if len(sys.argv) > 1 else "offense_1.json"
    with open(path) as f:
        offense = json.load(f)
    sev_map = get_severity_map()
    session = ClientSession(apikey=IRIS_KEY, host=IRIS_URL, ssl_verify=False, agent="qradar-iris-bridge")
    payload = offense_to_alert(offense, sev_map)
    print(f"Offense #{offense.get('id')} (mag {offense.get('magnitude')}) "
          f"-> IRIS severity_id {payload['alert_severity_id']}")
    resp = Alert(session=session).add_alert(payload)
    if resp.is_success():
        d = resp.get_data()
        print(f"\n[OK] IRIS Alert #{d.get('alert_id')} created from REAL QRadar offense - "
              f"severity '{d['severity']['severity_name']}', status '{d['status']['status_name']}'")
    else:
        print("\n[FAIL]", resp.get_msg()); print(resp.get_data())

if __name__ == "__main__":
    main()
