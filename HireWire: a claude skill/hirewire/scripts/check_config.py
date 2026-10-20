#!/usr/bin/env python3
"""Check a Hirewire config file and print the schedule it describes.

Usage:
    python check_config.py hirewire/config.json

Exits with 1 when something must be fixed. Warnings do not change the exit code.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SOURCE_TYPES = {"page", "greenhouse", "lever", "ashby", "eightfold"}
FEED_TYPES_NEEDING_BOARD = {"greenhouse", "lever", "ashby"}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def next_run(hour, minute, tz):
    now = datetime.now(tz)
    candidate = datetime(now.year, now.month, now.day, hour, minute, tzinfo=tz)
    if candidate <= now:
        tomorrow = (now + timedelta(days=1)).date()
        candidate = datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=tz)
    return candidate


def main(path):
    errors, warnings = [], []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        print(f"error: {path} does not exist")
        return 1
    except json.JSONDecodeError as err:
        print(f"error: {path} is not valid JSON ({err})")
        return 1

    email = config.get("email", "")
    if not EMAIL_PATTERN.match(str(email)):
        errors.append(f"email '{email}' does not look like an email address")

    time_text = str(config.get("delivery_time", ""))
    match = TIME_PATTERN.match(time_text)
    if not match:
        errors.append(f"delivery_time '{time_text}' must be 24-hour HH:MM, for example 08:00 or 17:30")

    tz = None
    tz_name = config.get("timezone", "")
    try:
        tz = ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        errors.append(f"timezone '{tz_name}' is not a known name, for example America/Los_Angeles")

    def check_keywords(value, where):
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(k, str) and k.strip() for k in value):
            errors.append(f"{where}: keywords must be a list of non-empty text, for example [\"technical product manager\"]")
            return []
        return [k.strip() for k in value]

    keywords = check_keywords(config.get("keywords"), "keywords")

    companies = config.get("companies")
    if not isinstance(companies, list) or not companies:
        errors.append("companies must be a list with at least one company")
        companies = []
    seen, custom = set(), []
    for index, company in enumerate(companies, start=1):
        name = company.get("name", "")
        label = name or f"company {index}"
        if not name:
            errors.append(f"company {index} has no name")
        elif name in seen:
            errors.append(f"'{name}' appears twice, and names are the snapshot keys")
        seen.add(name)
        own_keywords = check_keywords(company.get("keywords"), f"{label} keywords")
        if own_keywords:
            custom.append(f"{label}: {', '.join(own_keywords)}")
        url = str(company.get("careers_url", ""))
        if not url.startswith(("http://", "https://")):
            errors.append(f"{label}: careers_url must start with http:// or https://")
        source = company.get("source") or {}
        kind = source.get("type")
        if kind not in SOURCE_TYPES:
            errors.append(f"{label}: source type '{kind}' must be one of {', '.join(sorted(SOURCE_TYPES))}")
        elif kind in FEED_TYPES_NEEDING_BOARD:
            if not source.get("board"):
                errors.append(f"{label}: a {kind} source needs a board name")
            elif source.get("confirmed") is not True:
                warnings.append(f"{label}: board name '{source['board']}' is not confirmed from the careers page, so an empty result cannot be trusted")

    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}")
    if errors:
        return 1

    hour, minute = int(match.group(1)), int(match.group(2))
    upcoming = next_run(hour, minute, tz)
    print(f"ok: {len(companies)} companies, digest to {email}")
    print("watching for: " + (", ".join(keywords) if keywords else "all roles (no keywords set)"))
    for line in custom:
        print(f"  own keywords, {line}")
    print(f"schedule: every day at {hour:02d}:{minute:02d} {tz_name} (cron {minute} {hour} * * *)")
    print(f"next run: {upcoming:%Y-%m-%d %H:%M %Z} ({upcoming.astimezone(timezone.utc):%H:%M} UTC)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
