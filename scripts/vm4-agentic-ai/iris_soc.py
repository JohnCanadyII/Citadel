#!/usr/bin/env python3
"""
iris_soc.py — the autonomous SOC loop (VM4, Phase 5).
Reads QRadar-sourced alerts from DFIR-IRIS, runs the 5-agent team (soc.py) on each
alert's embedded offense, and posts the analysis back onto the alert as a comment.
Dedupes via a local state file so alerts are never double-analyzed.

Lives at ~/agentic-soc/iris_soc.py (run inside the .venv, alongside soc.py).
Usage:
  python iris_soc.py            # analyze up to 1 NEW QRadar alert (quick)
  python iris_soc.py --limit 3  # up to 3 new
  python iris_soc.py --all      # all new
Env: IRIS_KEY (required), IRIS_URL (default https://192.168.1.60).

Proven: read IRIS alert #4 -> ran 5-agent pipeline -> posted AI analysis comment
back to the alert ("comment POSTED OK"). Full loop: QRadar -> IRIS -> AI -> IRIS.

IRIS alert API used:
  GET  /alerts/filter?per_page=100&page=1&sort=desc  -> data.alerts[]
  POST /alerts/<id>/comments/add   body {"comment_text": ...}
  (each alert's original offense is in alert_source_content; alert_source == 'IBM QRadar')
"""
import os, sys, json, asyncio, urllib3, requests
from soc import run_pipeline, build_report

urllib3.disable_warnings()
IRIS_URL = os.environ.get("IRIS_URL", "https://192.168.1.60")
IRIS_KEY = os.environ.get("IRIS_KEY", "")
SOURCE   = "IBM QRadar"
STATE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iris_soc_state.json")
HEADERS  = {"Authorization": f"Bearer {IRIS_KEY}", "Content-Type": "application/json"}


def load_state():
    try:
        return set(json.load(open(STATE)).get("analyzed_alert_ids", []))
    except FileNotFoundError:
        return set()


def save_state(s):
    json.dump({"analyzed_alert_ids": sorted(s)}, open(STATE, "w"))


def fetch_alerts():
    r = requests.get(f"{IRIS_URL}/alerts/filter",
                     params={"per_page": 100, "page": 1, "sort": "desc"},
                     headers=HEADERS, verify=False, timeout=30)
    r.raise_for_status()
    return r.json().get("data", {}).get("alerts", [])


def post_comment(alert_id, text):
    r = requests.post(f"{IRIS_URL}/alerts/{alert_id}/comments/add",
                      json={"comment_text": text}, headers=HEADERS, verify=False, timeout=30)
    try:
        ok = r.json().get("status") == "success"
    except Exception:
        ok = False
    return ok, r.text[:200]


async def main():
    if not IRIS_KEY:
        sys.exit("ERROR: IRIS_KEY not set.")
    args = sys.argv[1:]
    limit = 10**9 if "--all" in args else 1
    if "--limit" in args:
        try:
            limit = int(args[args.index("--limit") + 1])
        except Exception:
            pass
    alerts = fetch_alerts()
    qradar = [a for a in alerts if a.get("alert_source") == SOURCE]
    state = load_state()
    todo = [a for a in qradar if a.get("alert_id") not in state][:limit]
    print(f"{len(qradar)} QRadar alerts in IRIS | {len(todo)} to analyze this run "
          f"(limit {limit if limit < 10**9 else 'all'}).")
    if not todo:
        print("Nothing new to analyze. (Use --all or --limit N to do more.)")
        return
    for a in todo:
        aid = a.get("alert_id")
        offense = a.get("alert_source_content") or {}
        if not offense:
            print(f"  alert #{aid}: no offense content, skipping")
            continue
        print(f"\n=== Analyzing IRIS alert #{aid}: {a.get('alert_title','')} ===")
        analysis = await run_pipeline(offense)
        report = build_report(offense, analysis)
        open(f"report_alert_{aid}.md", "w").write(report)
        comment = "## Agentic SOC - Autonomous AI Analysis\n\n" + report
        ok, resp = post_comment(aid, comment)
        print(f"  -> IRIS alert #{aid}: comment {'POSTED OK' if ok else 'FAILED: ' + resp}")
        if ok:
            state.add(aid); save_state(state)
    print(f"\nDone. Analyzed {len(state)} alert(s) total: {sorted(state)}")
    print("Open the alert in IRIS (Alerts tab) to see the AI analysis comment.")


if __name__ == "__main__":
    asyncio.run(main())
