#!/usr/bin/env python3
"""Create (or reuse) ODC Brevo transactional templates and print their IDs.

Reads BREVO_API_KEY / BREVO_SENDER_EMAIL / BREVO_SENDER_NAME from the
environment (or from infra/parameters.json / .env when run from the repo root).

Usage:
  python infra/scripts/create_brevo_templates.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.brevo.com/v3/smtp/templates"

# Must match params built by backend/app/notifications/brevo.py::_build_template_params
TEMPLATES = [
    {
        "key": "BrevoTemplateBookingConfirmed",
        "env": "BREVO_TEMPLATE_BOOKING_CONFIRMED",
        "templateName": "ODC — Booking confirmed",
        "subject": "Meeting room booked — {{ params.room_name }}",
        "html": """
<html><body style="font-family:Arial,sans-serif;color:#222;line-height:1.5">
  <h2>Booking confirmed</h2>
  <p>Hi {{ params.recipient_name }},</p>
  <p>Your booking for <strong>{{ params.room_name }}</strong> is confirmed.</p>
  <ul>
    <li><strong>Start:</strong> {{ params.start_at }}</li>
    <li><strong>End:</strong> {{ params.end_at }}</li>
    <li><strong>Purpose:</strong> {{ params.purpose }}</li>
  </ul>
  <p style="color:#666;font-size:12px">ODC Meeting Room Booking Assistant</p>
</body></html>
""".strip(),
    },
    {
        "key": "BrevoTemplateBookingExtended",
        "env": "BREVO_TEMPLATE_BOOKING_EXTENDED",
        "templateName": "ODC — Booking extended",
        "subject": "Meeting extended — {{ params.room_name }}",
        "html": """
<html><body style="font-family:Arial,sans-serif;color:#222;line-height:1.5">
  <h2>Booking extended</h2>
  <p>Hi {{ params.recipient_name }},</p>
  <p>Your booking for <strong>{{ params.room_name }}</strong> has been extended.</p>
  <ul>
    <li><strong>Previous end:</strong> {{ params.previous_end_at }}</li>
    <li><strong>New end:</strong> {{ params.end_at }}</li>
    <li><strong>Start:</strong> {{ params.start_at }}</li>
  </ul>
  <p style="color:#666;font-size:12px">ODC Meeting Room Booking Assistant</p>
</body></html>
""".strip(),
    },
    {
        "key": "BrevoTemplateBookingCancelled",
        "env": "BREVO_TEMPLATE_BOOKING_CANCELLED",
        "templateName": "ODC — Booking cancelled",
        "subject": "Booking cancelled — {{ params.room_name }}",
        "html": """
<html><body style="font-family:Arial,sans-serif;color:#222;line-height:1.5">
  <h2>Booking cancelled</h2>
  <p>Hi {{ params.recipient_name }},</p>
  <p>Your booking for <strong>{{ params.room_name }}</strong> has been cancelled.</p>
  <ul>
    <li><strong>Was:</strong> {{ params.start_at }} → {{ params.end_at }}</li>
  </ul>
  <p style="color:#666;font-size:12px">ODC Meeting Room Booking Assistant</p>
</body></html>
""".strip(),
    },
    {
        "key": "BrevoTemplateVacateReminder",
        "env": "BREVO_TEMPLATE_VACATE_REMINDER",
        "templateName": "ODC — Vacate reminder",
        "subject": "Please vacate soon — {{ params.room_name }}",
        "html": """
<html><body style="font-family:Arial,sans-serif;color:#222;line-height:1.5">
  <h2>Please vacate the room soon</h2>
  <p>Hi {{ params.recipient_name }},</p>
  <p>Your meeting in <strong>{{ params.room_name }}</strong> ends in
     about <strong>{{ params.lead_minutes }}</strong> minutes, and another
     meeting is scheduled right after.</p>
  <ul>
    <li><strong>Your slot:</strong> {{ params.start_at }} → {{ params.end_at }}</li>
  </ul>
  <p>Please wrap up and leave the room on time. Thank you!</p>
  <p style="color:#666;font-size:12px">ODC Meeting Room Booking Assistant</p>
</body></html>
""".strip(),
    },
    {
        "key": "BrevoTemplateWaitlistAvailable",
        "env": "BREVO_TEMPLATE_WAITLIST_AVAILABLE",
        "templateName": "ODC — Waitlist slot available",
        "subject": "Room available — {{ params.room_name }}",
        "html": """
<html><body style="font-family:Arial,sans-serif;color:#222;line-height:1.5">
  <h2>A waitlisted slot is free</h2>
  <p>Hi {{ params.recipient_name }},</p>
  <p><strong>{{ params.room_name }}</strong> is now available for the window
     you were waiting for.</p>
  <ul>
    <li><strong>Start:</strong> {{ params.start_at }}</li>
    <li><strong>End:</strong> {{ params.end_at }}</li>
  </ul>
  <p>Book it soon before someone else takes it.</p>
  <p style="color:#666;font-size:12px">ODC Meeting Room Booking Assistant</p>
</body></html>
""".strip(),
    },
]


def _load_secrets() -> tuple[str, str, str]:
    # Prefer environment, then parameters.json, then .env
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    sender = os.getenv("BREVO_SENDER_EMAIL", "").strip()
    name = os.getenv("BREVO_SENDER_NAME", "").strip() or "ODC Meeting Room"

    params_path = ROOT / "infra" / "parameters.json"
    if params_path.exists():
        data = json.loads(params_path.read_text())
        api_key = api_key or str(data.get("BrevoApiKey") or "").strip()
        sender = sender or str(data.get("BrevoSenderEmail") or "").strip()
        name = name or str(data.get("BrevoSenderName") or "ODC Meeting Room").strip()

    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")
            if k == "BREVO_API_KEY" and not api_key:
                api_key = v
            elif k == "BREVO_SENDER_EMAIL" and not sender:
                sender = v
            elif k == "BREVO_SENDER_NAME" and name == "ODC Meeting Room":
                name = v or name

    if not api_key or not sender:
        raise SystemExit("Need BREVO_API_KEY and BREVO_SENDER_EMAIL")
    return api_key, sender, name


def _list_templates(client: httpx.Client) -> dict[str, int]:
    """Map templateName → id for existing templates (first page, up to 50)."""
    resp = client.get(API, params={"templateStatus": True, "limit": 50, "offset": 0})
    resp.raise_for_status()
    body = resp.json()
    out: dict[str, int] = {}
    for t in body.get("templates") or []:
        out[t["name"]] = int(t["id"])
    # Also grab inactive ones
    resp2 = client.get(API, params={"templateStatus": False, "limit": 50, "offset": 0})
    if resp2.is_success:
        for t in (resp2.json().get("templates") or []):
            out.setdefault(t["name"], int(t["id"]))
    return out


def main() -> int:
    api_key, sender, sender_name = _load_secrets()
    headers = {"api-key": api_key, "accept": "application/json", "content-type": "application/json"}

    with httpx.Client(headers=headers, timeout=30.0) as client:
        existing = _list_templates(client)
        print(f"Found {len(existing)} existing Brevo templates")

        results: dict[str, int] = {}
        for spec in TEMPLATES:
            name = spec["templateName"]
            if name in existing:
                tid = existing[name]
                print(f"  reuse  {name!r} → id={tid}")
                # Ensure it's active
                client.put(f"{API}/{tid}", json={"isActive": True})
            else:
                payload = {
                    "templateName": name,
                    "subject": spec["subject"],
                    "sender": {"name": sender_name, "email": sender},
                    "htmlContent": spec["html"],
                    "isActive": True,
                }
                resp = client.post(API, json=payload)
                if resp.status_code >= 400:
                    print(f"  FAIL   {name!r}: {resp.status_code} {resp.text}", file=sys.stderr)
                    return 1
                tid = int(resp.json()["id"])
                print(f"  create {name!r} → id={tid}")
            results[spec["key"]] = tid

    print("\n=== IDs (parameters.json keys) ===")
    print(json.dumps(results, indent=2))
    out_path = ROOT / "infra" / ".deploy" / "brevo_templates.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2) + "\n")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
