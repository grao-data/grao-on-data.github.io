#!/usr/bin/env python3
"""Compare freshly read roles with the saved snapshot and build the Hirewire report.

Usage:
    python diff_roles.py --current current.json [--snapshot snapshot.json]
                         [--config hirewire/config.json] [--links links.json] --out-dir out

Writes report.json, snapshot.json, email.txt and email.html into the out directory.

The script does not trust the status the reader gives. It downgrades a status when the
evidence in the record does not support it, so a cut-off or empty read is never reported
as "no new roles".

Keywords come from the config: a top-level "keywords" list, which a company's own
"keywords" list replaces. A role is reported when its title or team contains any keyword,
ignoring case and punctuation. With no keywords, every role is reported. The snapshot
always keeps every open role, so changing keywords later never makes old roles look new.

Run it twice when links need checking: once to find out which links to check
(report.json lists them under links_to_check), then again with --links to build the email.
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

VALID = {"read", "partial", "could_not_read"}
STATUS_WORDS = {
    "read": "read in full",
    "partial": "partly read",
    "could_not_read": "could not read",
}
DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%m/%d/%Y", "%Y/%m/%d")
LINK_STATES = {"ok", "broken", "manual", "unverified"}


def parse_time(value):
    """Return an aware UTC datetime from an ISO string, a date, text like 'Oct 7, 2026', or epoch seconds or milliseconds."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)) or (
            isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value.strip())
        ):
            number = float(value)
            if number > 1e11:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc)
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
            for fmt in DATE_FORMATS:
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def format_date(value):
    parsed = parse_time(value)
    if parsed is None:
        return None
    return f"{parsed:%b} {parsed.day}, {parsed.year}"


def squash(text):
    """Lowercase and reduce punctuation to single spaces so 'Sr. Technical Product-Manager' compares cleanly."""
    return " " + re.sub(r"[^a-z0-9+#]+", " ", str(text or "").lower()).strip() + " "


def clean_keywords(raw):
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    seen, out = set(), []
    for item in raw:
        text = str(item).strip()
        if text and squash(text) not in seen:
            seen.add(squash(text))
            out.append(text)
    return out


def matches(role, keywords):
    """True when there are no keywords, or any keyword starts a word in the title or team.

    Matching at the start of a word lets 'engineer' find 'Engineering Manager' while 'pm' does not find 'Development'.
    """
    if not keywords:
        return True
    haystack = squash(f"{role.get('title', '')} {role.get('team', '')}")
    for keyword in keywords:
        needle = squash(keyword).rstrip()
        if needle.strip() and needle in haystack:
            return True
    return False


def good_link(url, base):
    """Return an absolute http(s) link, or '' when there is nothing usable."""
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url) and base:
        url = urljoin(base, url)
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or " " in url:
        return ""
    return url


def normalise_roles(raw_roles, base):
    roles = []
    for raw in raw_roles or []:
        title = (raw.get("title") or "").strip() or "(untitled)"
        location = (raw.get("location") or "").strip()
        team = (raw.get("team") or "").strip()
        url = good_link(raw.get("url"), base)
        role_id = str(raw.get("id") or url or f"{title}|{location}")
        roles.append(
            {
                "id": role_id,
                "title": title,
                "location": location,
                "team": team,
                "url": url,
                "posted_at": raw.get("posted_at"),
                "open": raw.get("open") is not False,
            }
        )
    return roles


def assess(company, prior_count):
    """Return (status, notes). Downgrade the declared status when the evidence does not support it."""
    roles = company.get("roles") or []
    open_count = sum(1 for r in roles if r.get("open") is not False)
    declared = company.get("status")
    status = declared if declared in VALID else "could_not_read"
    notes = []
    if declared not in VALID:
        notes.append("the status was missing or not recognised")
    signals = sorted(set(company.get("page_signals") or []))

    empty_is_confirmed = status == "read" and company.get("confirmed_empty") is True
    if status != "could_not_read" and len(roles) == 0 and not empty_is_confirmed:
        status = "could_not_read"
        if signals:
            notes.append("no roles came back and the page carries warning signs: " + ", ".join(signals))
        elif prior_count:
            notes.append(f"no roles came back, but {prior_count} were seen at the last read")
        elif company.get("method") == "feed" and not company.get("source_confirmed"):
            notes.append("the feed returned nothing and its board name was not confirmed")
        else:
            notes.append("no roles came back and the source does not say it has none")

    if status == "read":
        if company.get("closed_cleanly") is False:
            status = "partial"
            notes.append("the response ended mid-record")
        total = company.get("total_stated")
        if isinstance(total, int) and not isinstance(total, bool) and total > open_count:
            status = "partial"
            notes.append(f"the source states {total} roles and {open_count} were read")
        limit = company.get("limit_used")
        if isinstance(limit, int) and not isinstance(limit, bool) and len(roles) >= limit:
            status = "partial"
            notes.append(f"the read stopped at a limit of {limit}")
        if "login_wall" in signals:
            status = "partial"
            notes.append("part of the list is behind a login")
    return status, notes


def newest_first(roles):
    far_past = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return sorted(roles, key=lambda r: parse_time(r.get("posted_at")) or far_past, reverse=True)


def decorate(role, links, base):
    """Add the display fields every reported role carries: date, link and the state of the link."""
    out = dict(role)
    out["posted_display"] = format_date(role.get("posted_at"))
    url = role.get("url") or ""
    out["link"] = url
    if not url:
        out["link_state"] = "none"
    else:
        state = links.get(url, "unverified")
        out["link_state"] = state if state in LINK_STATES else "unverified"
    out["fallback_link"] = base
    return out


def build(current, snapshot, config, links, now, lookback_days, prune, baseline_cap, keyword_override):
    checked_at = parse_time(current.get("checked_at")) or now
    checked_date = checked_at.date().isoformat()
    cutoff = checked_at - timedelta(days=lookback_days)
    prior_all = (snapshot or {}).get("companies", {})
    new_snapshot = {} if prune else dict(prior_all)

    global_keywords = clean_keywords(keyword_override if keyword_override is not None else (config or {}).get("keywords"))
    company_keywords = {}
    for entry in (config or {}).get("companies", []):
        if keyword_override is None and entry.get("keywords"):
            company_keywords[entry.get("name")] = clean_keywords(entry.get("keywords"))

    results = []
    for company in current["companies"]:
        name = company.get("name") or "(unnamed)"
        keywords = company_keywords.get(name, global_keywords)
        base = good_link(company.get("final_url") or company.get("careers_url"), "")
        prior_entry = prior_all.get(name)
        baseline = prior_entry is None
        prior = (prior_entry or {}).get("roles", {})

        status, notes = assess(company, len(prior))
        declared = company.get("status")
        reason_parts = []
        if company.get("reason"):
            reason_parts.append(str(company["reason"]).strip())
        reason_parts.extend(notes)
        if status != "read" and not reason_parts:
            reason_parts.append("no reason was recorded")
        reason = "; ".join(reason_parts).strip().rstrip(".")
        reason = reason[:1].upper() + reason[1:]

        roles = normalise_roles(company.get("roles"), base)
        open_roles = {r["id"]: r for r in roles if r["open"]}
        closed_ids = {r["id"] for r in roles if not r["open"]}
        matching_open = [r for r in open_roles.values() if matches(r, keywords)]

        new, removed, unmatched_new, hidden = [], [], 0, 0
        listing = None
        if status in ("read", "partial"):
            if baseline:
                if keywords:
                    listing = "open_now"
                    new = newest_first(matching_open)
                    if len(new) > baseline_cap:
                        hidden = len(new) - baseline_cap
                        new = new[:baseline_cap]
                else:
                    listing = "recent"
                    new = [
                        r for r in open_roles.values()
                        if (parse_time(r["posted_at"]) or datetime(1970, 1, 1, tzinfo=timezone.utc)) >= cutoff
                    ]
            else:
                fresh = [r for rid, r in open_roles.items() if rid not in prior]
                new = [r for r in fresh if matches(r, keywords)]
                unmatched_new = len(fresh) - len(new)

            if status == "read":
                gone = [rid for rid in prior if rid not in open_roles]
            else:
                gone = [rid for rid in prior if rid in closed_ids]
            removed = [dict(prior[rid], id=rid) for rid in gone if matches(prior[rid], keywords)]

            kept = {} if status == "read" else {k: v for k, v in prior.items() if k not in closed_ids}
            for rid, role in open_roles.items():
                old = prior.get(rid) or {}
                kept[rid] = {
                    "title": role["title"],
                    "location": role["location"],
                    "team": role["team"],
                    "url": role["url"],
                    "posted_at": role["posted_at"],
                    "first_seen": old.get("first_seen") or checked_date,
                }
            new_snapshot[name] = {
                "last_read": checked_at.isoformat(),
                "status": status,
                "roles": kept,
            }
        elif prior_entry is not None:
            new_snapshot[name] = prior_entry

        results.append(
            {
                "name": name,
                "careers_url": base,
                "status": status,
                "status_words": STATUS_WORDS[status],
                "downgraded_from": declared if declared in VALID and declared != status else None,
                "reason": reason,
                "baseline": baseline and status in ("read", "partial"),
                "listing": listing,
                "keywords": keywords,
                "roles_read": len(open_roles),
                "roles_matching": len(matching_open),
                "total_stated": company.get("total_stated"),
                "new": [decorate(r, links, base) for r in newest_first(new)],
                "hidden_new": hidden,
                "new_not_matching": unmatched_new,
                "removed": removed,
            }
        )

    def count_new(item):
        return 0 if item["listing"] == "open_now" else len(item["new"])

    summary = {
        "companies": len(results),
        "read": sum(1 for r in results if r["status"] == "read"),
        "partial": sum(1 for r in results if r["status"] == "partial"),
        "could_not_read": sum(1 for r in results if r["status"] == "could_not_read"),
        "new_roles": sum(count_new(r) for r in results),
        "open_now_roles": sum(len(r["new"]) + r["hidden_new"] for r in results if r["listing"] == "open_now"),
        "needs_a_look": sum(1 for r in results if r["status"] != "read"),
    }
    to_check = []
    for item in results:
        for role in item["new"]:
            if role["link"] and role["link"] not in to_check:
                to_check.append(role["link"])
    report = {
        "checked_at": checked_at.isoformat(),
        "lookback_days": lookback_days,
        "keywords": global_keywords,
        "summary": summary,
        "links_to_check": to_check,
        "companies": results,
    }
    return report, {"companies": new_snapshot}


# ----- email -----

def role_head(role):
    bits = [role["title"]] + [b for b in (role.get("location"), role.get("team")) if b]
    return " | ".join(bits)


def role_date_text(role):
    return role["posted_display"] or "date not listed by the company"


def role_link_lines(role):
    state = role["link_state"]
    if state == "none":
        tail = f" Careers page: {role['fallback_link']}" if role["fallback_link"] else ""
        return [f"Link: no direct link was found.{tail}"]
    if state == "ok":
        return [f"Link: {role['link']}"]
    if state == "broken":
        lines = [f"Link: {role['link']} (did not load when checked)"]
        if role["fallback_link"]:
            lines.append(f"Careers page: {role['fallback_link']}")
        return lines
    if state == "manual":
        return [f"Link: {role['link']} (manual inspection needed)"]
    return [f"Link: {role['link']} (not checked)"]


def watching_text(report):
    keywords = report["keywords"]
    per_company = sorted({c["name"] for c in report["companies"] if c["keywords"] and c["keywords"] != keywords})
    text = "Watching for: " + (", ".join(keywords) if keywords else "all roles")
    if per_company:
        text += f" (different keywords for {', '.join(per_company)})"
    return text + "."


def when_text(report):
    parsed = parse_time(report["checked_at"])
    if parsed is None:
        return report["checked_at"]
    return f"{parsed:%a %b} {parsed.day}"


def subject_line(summary):
    new, open_now, look = summary["new_roles"], summary["open_now_roles"], summary["needs_a_look"]
    if new:
        head = f"{new} new role" + ("" if new == 1 else "s")
    elif open_now:
        head = f"{open_now} matching role" + ("" if open_now == 1 else "s") + " open now"
    else:
        head = "no new roles"
    if look:
        head += f", {look} " + ("company needs" if look == 1 else "companies need") + " a look"
    return f"Hirewire: {head}"


def new_tag(company, report):
    tag = company["status_words"]
    if company["listing"] == "recent":
        tag += f", first check, posted in the last {report['lookback_days']} days"
    return tag


def email_text(report):
    summary, companies = report["summary"], report["companies"]
    lines = [f"Subject: {subject_line(summary)}", ""]
    lines.append(
        f"Hirewire checked {summary['companies']} " + ("company" if summary["companies"] == 1 else "companies") + f" on {when_text(report)}."
    )
    lines.append(watching_text(report))
    lines.append("")

    def block(title, items, tagger):
        if not items:
            return
        lines.append(title)
        for company in items:
            lines.append(f"{company['name']} ({tagger(company)})")
            for role in company["new"]:
                lines.append(f"  - {role_head(role)}")
                lines.append(f"    Posted: {role_date_text(role)}")
                for link_line in role_link_lines(role):
                    lines.append(f"    {link_line}")
            if company["hidden_new"]:
                lines.append(f"  ...and {company['hidden_new']} more matching roles. See {company['careers_url']}")
        lines.append("")

    block("NEW ROLES", [c for c in companies if c["new"] and c["listing"] != "open_now"], lambda c: new_tag(c, report))
    block(
        "OPEN NOW (FIRST CHECK)",
        [c for c in companies if c["new"] and c["listing"] == "open_now"],
        lambda c: c["status_words"] + ", first check, every matching role open today",
    )

    needs = [c for c in companies if c["status"] != "read"]
    if needs:
        lines.append("NEEDS A LOOK")
        for company in needs:
            lines.append(f"- {company['name']}: {company['status_words']}. {company['reason']}.")
            if company["status"] == "could_not_read":
                lines.append("  Nothing is known about new roles here. Check it by hand" + (f": {company['careers_url']}" if company["careers_url"] else "."))
            else:
                lines.append("  Roles outside the part that was read are unchecked." + (f" {company['careers_url']}" if company["careers_url"] else ""))
        lines.append("")

    baselines = [c for c in companies if c["baseline"]]
    if baselines:
        lines.append("BASELINE SAVED")
        for company in baselines:
            noun = "role" if company["roles_read"] == 1 else "roles"
            extra = f" ({company['roles_matching']} match your keywords)" if company["keywords"] else ""
            lines.append(f"- {company['name']}: {company['roles_read']} open {noun} saved{extra}. New roles show from the next check.")
        lines.append("")

    removed = [c for c in companies if c["removed"]]
    if removed:
        lines.append("NO LONGER LISTED")
        for company in removed:
            lines.append(company["name"])
            for role in company["removed"]:
                lines.append(f"  - {role_head(role)}")
        lines.append("")

    unmatched = sum(c["new_not_matching"] for c in companies)
    if unmatched:
        lines.append(f"{unmatched} other new " + ("role" if unmatched == 1 else "roles") + " did not match your keywords.")
        lines.append("")

    quiet = [c["name"] for c in companies if c["status"] == "read" and not c["new"] and not c["baseline"]]
    if quiet:
        lines.append("Read in full with nothing new: " + ", ".join(quiet) + ".")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def email_html(report):
    esc = html.escape
    summary, companies = report["summary"], report["companies"]
    parts = [
        "<html><body style=\"font-family: -apple-system, Segoe UI, Arial, sans-serif; line-height: 1.45; color: #1a1a1a; max-width: 640px;\">",
        f"<h2 style=\"margin-bottom: 4px;\">{esc(subject_line(summary))}</h2>",
        f"<p style=\"margin-top: 0; color: #555;\">Checked {summary['companies']} on {esc(when_text(report))}. {esc(watching_text(report))}</p>",
    ]

    def anchor(url, text):
        return f"<a href=\"{esc(url, quote=True)}\">{esc(text)}</a>"

    def role_html(role):
        title = esc(role_head(role))
        state = role["link_state"]
        if state in ("ok", "unverified", "manual"):
            title = anchor(role["link"], role_head(role))
        date = f"<br><span style=\"color: #555;\">Posted: {esc(role_date_text(role))}</span>"
        if state == "ok":
            link = ""
        elif state == "manual":
            link = "<br><span style=\"color: #a60;\">Manual inspection needed</span>"
        elif state == "unverified":
            link = "<br><span style=\"color: #555;\">Link not checked</span>"
        elif state == "broken":
            link = f"<br><span style=\"color: #a33;\">This link did not load when checked.</span> {anchor(role['fallback_link'], 'Open the careers page')}" if role["fallback_link"] else "<br><span style=\"color: #a33;\">This link did not load when checked.</span>"
        else:
            link = f"<br>No direct link was found. {anchor(role['fallback_link'], 'Open the careers page')}" if role["fallback_link"] else "<br>No direct link was found."
        return f"<li>{title}{date}{link}</li>"

    def block(title, items, tagger):
        if not items:
            return
        parts.append(f"<h3>{esc(title)}</h3>")
        for company in items:
            parts.append(f"<p style=\"margin-bottom: 2px;\"><strong>{esc(company['name'])}</strong> <span style=\"color: #555;\">({esc(tagger(company))})</span></p><ul style=\"margin-top: 2px;\">")
            parts.extend(role_html(r) for r in company["new"])
            if company["hidden_new"]:
                parts.append(f"<li>...and {company['hidden_new']} more matching roles. {anchor(company['careers_url'], 'See the careers page')}</li>")
            parts.append("</ul>")

    block("New roles", [c for c in companies if c["new"] and c["listing"] != "open_now"], lambda c: new_tag(c, report))
    block(
        "Open now (first check)",
        [c for c in companies if c["new"] and c["listing"] == "open_now"],
        lambda c: c["status_words"] + ", first check, every matching role open today",
    )

    needs = [c for c in companies if c["status"] != "read"]
    if needs:
        parts.append("<h3>Needs a look</h3><ul>")
        for company in needs:
            where = f" {anchor(company['careers_url'], 'Open the careers page')}." if company["careers_url"] else ""
            note = "Nothing is known about new roles here." if company["status"] == "could_not_read" else "Roles outside the part that was read are unchecked."
            parts.append(f"<li><strong>{esc(company['name'])}</strong>: {esc(company['status_words'])}. {esc(company['reason'])}. {note}{where}</li>")
        parts.append("</ul>")

    baselines = [c for c in companies if c["baseline"]]
    if baselines:
        parts.append("<h3>Baseline saved</h3><ul>")
        for company in baselines:
            noun = "role" if company["roles_read"] == 1 else "roles"
            extra = f" ({company['roles_matching']} match your keywords)" if company["keywords"] else ""
            parts.append(f"<li>{esc(company['name'])}: {company['roles_read']} open {noun} saved{extra}. New roles show from the next check.</li>")
        parts.append("</ul>")

    removed = [c for c in companies if c["removed"]]
    if removed:
        parts.append("<h3>No longer listed</h3>")
        for company in removed:
            parts.append(f"<p style=\"margin-bottom: 2px;\"><strong>{esc(company['name'])}</strong></p><ul style=\"margin-top: 2px;\">")
            parts.extend(f"<li>{esc(role_head(r))}</li>" for r in company["removed"])
            parts.append("</ul>")

    unmatched = sum(c["new_not_matching"] for c in companies)
    if unmatched:
        parts.append(f"<p style=\"color: #555;\">{unmatched} other new " + ("role" if unmatched == 1 else "roles") + " did not match your keywords.</p>")
    quiet = [c["name"] for c in companies if c["status"] == "read" and not c["new"] and not c["baseline"]]
    if quiet:
        parts.append(f"<p style=\"color: #555;\">Read in full with nothing new: {esc(', '.join(quiet))}.</p>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--current", required=True, help="current.json written after reading the companies")
    parser.add_argument("--snapshot", help="snapshot.json from the last run; leave out on the first run")
    parser.add_argument("--config", help="config.json, for the keywords")
    parser.add_argument("--keywords", help="comma-separated keywords that replace the config's keywords for every company")
    parser.add_argument("--links", help="links.json mapping each role link to ok, broken, manual or unverified")
    parser.add_argument("--out-dir", default="out")
    parser.add_argument("--lookback-days", type=int, default=2, help="on a first read with no keywords, list roles posted within this many days")
    parser.add_argument("--baseline-cap", type=int, default=25, help="on a first read with keywords, list at most this many roles per company")
    parser.add_argument("--prune", action="store_true", help="drop snapshot entries for companies not in current.json")
    args = parser.parse_args()

    current = load_json(args.current)
    if isinstance(current, list):
        current = {"companies": current}
    if not isinstance(current.get("companies"), list):
        print("current.json needs a 'companies' list", file=sys.stderr)
        return 2

    snapshot = None
    if args.snapshot:
        if Path(args.snapshot).exists():
            snapshot = load_json(args.snapshot)
        else:
            print(f"warning: {args.snapshot} not found, treating this as a first run", file=sys.stderr)
    config = load_json(args.config) if args.config and Path(args.config).exists() else {}
    links = load_json(args.links) if args.links and Path(args.links).exists() else {}
    override = [k for k in args.keywords.split(",")] if args.keywords is not None else None

    report, new_snapshot = build(
        current, snapshot, config, links, datetime.now(timezone.utc), args.lookback_days, args.prune, args.baseline_cap, override
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "snapshot.json").write_text(json.dumps(new_snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "email.txt").write_text(email_text(report), encoding="utf-8")
    (out_dir / "email.html").write_text(email_html(report), encoding="utf-8")

    s = report["summary"]
    print(
        f"{s['companies']} companies: {s['read']} read in full, {s['partial']} partly read, "
        f"{s['could_not_read']} could not read. {s['new_roles']} new roles, {s['open_now_roles']} matching roles open now."
    )
    unchecked = [u for u in report["links_to_check"] if u not in links]
    if unchecked:
        print(f"{len(unchecked)} links still to check (see links_to_check in report.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
