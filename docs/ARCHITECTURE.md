# Architecture & Build Notes

A 4-VM Digital Forensics & Incident Response (DFIR) platform with an agentic-AI
automation layer, built on a single workstation with VirtualBox.

> **Security note:** This document is a sanitized version of my private build
> handoff. All real passwords, API tokens, and keys have been replaced with
> placeholders. Nothing in this repository contains a live credential.

## The pipeline (working end-to-end)

```
Endpoint / network traffic
        │
        ▼
[VM2] NSM Sensor  ── Suricata + Zeek ── rsyslog (compact EVE JSON) ──▶ :514
        │
        ▼
[VM1] IBM QRadar (SIEM)  ── CRE rule: "Suricata Alert → Offense"
        │  REST: GET /api/siem/offenses
        ▼
[VM3] DFIR-IRIS (case management)  ◀── bridge + poller (systemd timer, every 2 min)
        │  REST: /alerts/filter, /alerts/<id>/comments/add
        ▼
[VM4] Agentic AI  ── 1 orchestrator + 4 specialist agents (Claude Agent SDK)
        │
        └──────────────▶ writes the AI analysis back onto the IRIS alert
```

## VMs

| VM | Role | Stack |
|----|------|-------|
| VM1 `QRadar`     | SIEM / correlation           | IBM QRadar CE 7.6.0.0 |
| VM2 `NSM_Sensor` | Network security monitoring  | Suricata + Zeek → rsyslog → QRadar |
| VM3 `DFIR-IRIS`  | IR case management + bridge  | Ubuntu 22.04, Docker, DFIR-IRIS v2.4.27 |
| VM4 `Agentic_AI` | Multi-agent AI SOC analyst   | Ubuntu 22.04, Node 20 + Python 3.10, Claude Agent SDK |

## Host & resource strategy

Single workstation, 32 GB RAM, Windows 11 + VirtualBox 7.x. With Hyper-V/VBS
present, VirtualBox runs in the slower NEM execution mode, so the VMs are kept
lightweight and **Host I/O Cache** is enabled on their disk controllers.

QRadar alone reserves ~24 GB, so all four VMs cannot run at once. The design
works around this: the AI layer (VM4) reads offenses **from IRIS**, not from a
live QRadar connection. At analysis time only **IRIS (10 GB) + VM4 (6 GB) = 16 GB**
need to be running. Offenses are pushed into IRIS while QRadar is up (via the
Stage-3 poller), then analyzed later — QRadar and VM4 never need to be co-resident.

## Networking

Two host networks: a bridged `192.168.1.0/24` LAN and a host-only
`192.168.56.0/24`. QRadar is reached over host-only; the sensor, IRIS, and VM4
sit on both. VM4 reaches the IRIS API over the LAN.

## Data flow details

**VM2 → VM1.** Suricata and Zeek run on the sensor interface. rsyslog forwards
**compact** EVE JSON alert lines (`"event_type":"alert"`, no spaces) to QRadar
on 514/UDP. QRadar parses them under log source `NSM-Sensor-Suricata`; a custom
rule generates an offense indexed by source IP.

**VM1 → VM3.** `qradar_to_iris.py` maps a QRadar offense JSON into a DFIR-IRIS
alert (title, description, source content, severity, tags). Severity is resolved
**by name** from IRIS at runtime (`GET /manage/severities/list`) so the mapping
survives IRIS installs where the numeric IDs differ. `qradar_poller.py` polls
open offenses on a schedule (systemd timer) and syncs only new ones, deduping
against a local state file.

**VM3 → VM4 → VM3 (the autonomous loop).** `iris_soc.py` pulls QRadar-sourced
alerts from IRIS, hands each embedded offense to the 5-agent team in `soc.py`,
then posts the resulting analysis back onto the alert as a comment. A local
state file prevents re-analyzing the same alert.

## The agent team (VM4)

`soc.py` runs one orchestrator plus four specialists, each a Claude Agent SDK
call with its own expert persona:

1. **Triage** — true/false positive, confidence, priority.
2. **Investigation** — reconstructs what happened; scenario, affected entities.
3. **Threat Intel** — IOC enrichment + MITRE ATT&CK mapping.
4. **Response** — NIST 800-61 containment and remediation.
5. **Orchestrator** — synthesizes the four into a decision-ready executive summary.

The SDK rides the **Claude Max subscription** through a headless Claude Code
login on the VM — no per-call API billing. Each specialist returns JSON, which
is parsed defensively (extract the outer `{...}`, then `json.loads`).

## Lessons worth keeping

- **NEM console typing** lags and drops characters; SSH avoids it. When driving
  the VM console directly, send discrete key presses with settle waits.
- **Large multi-line pastes garble** in an SSH terminal. Transfer files as
  base64 chunks (`echo '<chunk>' >> f.b64`, then `base64 -d f.b64 > file`) and
  validate with `python -c "import ast; ast.parse(open('file').read())"`. Paste
  the decode command separately from the echo lines.
- **Claude Code on a headless server:** run `claude`, choose the subscription
  account, open the printed OAuth URL in a browser, paste the code back.
  `claude -p "..."` is a headless one-shot; the Agent SDK reuses that same auth.
- **LLM JSON:** instruct "JSON only," then regex-extract and parse defensively.
- **QRadar severity mapping:** IRIS severity IDs are not ordered the way you'd
  expect, so resolve them by name, not by a hardcoded number.
