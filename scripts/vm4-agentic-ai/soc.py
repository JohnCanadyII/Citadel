#!/usr/bin/env python3
"""
Agentic SOC (VM4) — multi-agent analysis of a QRadar offense, built on the Claude Agent SDK.
Runs on VM4 (agentic-ai, 192.168.1.70) on a Claude Max plan via headless Claude Code.

Architecture:  1 Orchestrator (incident commander) + 4 specialist agents
  1. Triage        - true/false positive, confidence, priority
  2. Investigation - what happened, scenario, affected entities
  3. Threat Intel  - IOC enrichment + MITRE ATT&CK mapping
  4. Response      - enterprise-standard containment / remediation (NIST 800-61)

Lives at ~/agentic-soc/soc.py (run inside the .venv).
Usage:  python soc.py [offense.json]   (defaults to a built-in sample offense)
Output: prints the orchestrator's executive summary + writes report_offense_<id>.md

Proven: produced a decision-ready incident writeup (MITRE mapping, deconfliction SLA,
containment plan) from the sample offense. iris_soc.py wires this to DFIR-IRIS.
"""
import sys, json, re, asyncio
from claude_agent_sdk import query, ClaudeAgentOptions


async def ask(system_prompt: str, user_prompt: str) -> str:
    opts = ClaudeAgentOptions(system_prompt=system_prompt, max_turns=1, allowed_tools=[])
    final, assistant = "", ""
    async for message in query(prompt=user_prompt, options=opts):
        for block in (getattr(message, "content", None) or []):
            t = getattr(block, "text", None)
            if t:
                assistant += t
        r = getattr(message, "result", None)
        if r:
            final = str(r)
    return (final or assistant).strip()


def parse_json(text: str) -> dict:
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    raw = m.group(0) if m else text
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": text.strip()}


SPECIALISTS = {
    "triage": (
        "You are a Tier-1 SOC triage analyst. You assess SIEM offenses for legitimacy and urgency. "
        "You are precise, evidence-based, and concise.",
        'Assess this QRadar offense. Respond ONLY with a JSON object with keys: '
        '"verdict" (one of "true_positive","false_positive","needs_review"), '
        '"confidence" (integer 0-100), "priority" (one of "P1","P2","P3","P4"), "rationale" (1-2 sentences).',
    ),
    "investigation": (
        "You are a Tier-2 SOC investigation analyst who reconstructs what happened from SIEM and network-sensor data.",
        'Investigate this offense. Respond ONLY with a JSON object with keys: '
        '"attack_summary" (2-3 sentences), "likely_scenario" (string), '
        '"affected_entities" (array of strings), "key_indicators" (array of strings).',
    ),
    "threat_intel": (
        "You are a cyber threat intelligence analyst specializing in IOC enrichment and MITRE ATT&CK mapping.",
        'Enrich this offense. Respond ONLY with a JSON object with keys: '
        '"iocs" (array of objects with "type","value","context"), '
        '"mitre_attack" (array of objects with "technique_id","name","rationale"), "threat_assessment" (1-2 sentences).',
    ),
    "response": (
        "You are a SOC incident-response lead who recommends enterprise-standard containment and remediation "
        "aligned to NIST 800-61 and industry best practices.",
        'Recommend a response for this offense. Respond ONLY with a JSON object with keys: '
        '"immediate_containment" (array of strings), "remediation_steps" (array of strings), '
        '"best_practices" (array of strings), "escalation" (string).',
    ),
}
ORCHESTRATOR_SYS = (
    "You are the lead SOC analyst / incident commander. You synthesize specialist findings into a clear, "
    "decision-ready executive summary for an incident case record."
)


async def run_specialist(name, offense):
    sys_prompt, instruction = SPECIALISTS[name]
    user = (f"{instruction}\n\nQRadar offense (JSON):\n{json.dumps(offense, indent=2)}\n\n"
            "Output the JSON object only - no prose, no markdown fences.")
    return parse_json(await ask(sys_prompt, user))


async def run_pipeline(offense):
    findings = {}
    for i, name in enumerate(SPECIALISTS, 1):
        print(f"  [{i}/5] {name.replace('_',' ').title()} agent analyzing...", flush=True)
        findings[name] = await run_specialist(name, offense)
    print("  [5/5] Orchestrator synthesizing...", flush=True)
    findings["executive_summary"] = await ask(
        ORCHESTRATOR_SYS,
        f"QRadar offense:\n{json.dumps(offense, indent=2)}\n\nSpecialist findings:\n{json.dumps(findings, indent=2)}\n\n"
        "Write a concise executive incident summary (5-8 sentences) covering what happened, the assessed "
        "severity/verdict, and the single most important recommended action. Plain text only.",
    )
    return findings


def _md_list(items):
    items = items or []
    if not items:
        return "_none_\n"
    out = ""
    for it in items:
        if isinstance(it, dict):
            out += "- " + " — ".join(str(v) for v in it.values() if v) + "\n"
        else:
            out += f"- {it}\n"
    return out


def build_report(offense, a):
    """Render the specialist findings + orchestrator summary to a Markdown incident report."""
    oid = offense.get("id", "?")
    triage = a.get("triage", {})
    inv    = a.get("investigation", {})
    ti     = a.get("threat_intel", {})
    resp   = a.get("response", {})
    lines = []
    lines.append(f"# Agentic SOC — Incident Analysis: QRadar Offense {oid}\n")
    lines.append("> Generated autonomously by a 1-orchestrator + 4-specialist Claude Agent SDK team.\n")

    lines.append("## Executive Summary\n")
    lines.append(str(a.get("executive_summary", "_n/a_")).strip() + "\n")

    lines.append("## 1. Triage\n")
    lines.append(f"- **verdict:** {triage.get('verdict','?')}")
    lines.append(f"- **confidence:** {triage.get('confidence','?')}")
    lines.append(f"- **priority:** {triage.get('priority','?')}")
    lines.append(f"- **rationale:** {triage.get('rationale','')}\n")

    lines.append("## 2. Investigation\n")
    lines.append(f"**Attack summary:** {inv.get('attack_summary','')}\n")
    lines.append(f"**Likely scenario:** {inv.get('likely_scenario','')}\n")
    lines.append("**Affected entities:**\n" + _md_list(inv.get("affected_entities")))
    lines.append("**Key indicators:**\n" + _md_list(inv.get("key_indicators")))

    lines.append("## 3. Threat Intelligence\n")
    lines.append(f"{ti.get('threat_assessment','')}\n")
    lines.append("**IOCs:**\n" + _md_list(ti.get("iocs")))
    lines.append("**MITRE ATT&CK:**\n" + _md_list(ti.get("mitre_attack")))

    lines.append("## 4. Response (NIST 800-61)\n")
    lines.append("**Immediate containment:**\n" + _md_list(resp.get("immediate_containment")))
    lines.append("**Remediation steps:**\n" + _md_list(resp.get("remediation_steps")))
    lines.append("**Best practices:**\n" + _md_list(resp.get("best_practices")))
    lines.append(f"**Escalation:** {resp.get('escalation','')}\n")
    return "\n".join(lines)


# A realistic sample offense (used when no offense.json is passed) — the Cobalt Strike C2 beacon
# scenario the platform was demoed with (NSM-Sensor-Suricata -> QRadar offense).
SAMPLE_OFFENSE = {
    "id": 1,
    "description": "ET MALWARE Possible Cobalt Strike Beacon",
    "magnitude": 3,
    "severity": 3,
    "credibility": 2,
    "relevance": 3,
    "status": "OPEN",
    "offense_source": "192.168.1.66",
    "event_count": 1,
    "categories": ["Unknown"],
    "log_sources": [{"name": "NSM-Sensor-Suricata"}],
    "source_address": "192.168.1.66",
    "destination_address": "45.77.12.34",
    "destination_port": 443,
}


async def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        with open(path) as f:
            offense = json.load(f)
    else:
        offense = SAMPLE_OFFENSE
    print(f"Analyzing QRadar offense #{offense.get('id')} with the agent team...\n")
    findings = await run_pipeline(offense)

    print("\n" + "=" * 78)
    print(f"Executive Incident Summary — QRadar Offense {offense.get('id')}")
    print("=" * 78 + "\n")
    print(findings.get("executive_summary", "").strip())
    print("\n" + "=" * 78)

    report = build_report(offense, findings)
    out = f"report_offense_{offense.get('id')}.md"
    with open(out, "w") as f:
        f.write(report)
    print(f"\nFull report written to: {out}")


if __name__ == "__main__":
    asyncio.run(main())
