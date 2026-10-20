# Hirewire
 
Hirewire is a Claude skill that watches the careers pages of companies you pick and tells you when they post a role you care about. You give it a list of companies, the job titles or keywords you want, an email address and a time of day. It compares each careers page with what it saw last time and reports only what is new, with a posting date and a checked link for every role.
 
I built it because job boards bury the roles I want and the companies I want to track don't post to job aggregators. Hirewire is designed around that second problem, allowing users to track jobs at companies that only post to their websites. 
 
## What it does
 
- Tracks single-company careers pages. Job boards and aggregators such as LinkedIn Wellfound are out of scope.
- Filters by job title or keyword, ignoring case and punctuation. "technical product manager" finds "Sr. Technical Product-Manager, AI". With no keywords, it reports every role.
- Reports new roles, roles that disappeared and roles marked filled, and says how many new roles did not match your keywords.
- Includes a posting date and a link for every role. It checks each link before sending and flags ones that did not load. Pages that need JavaScript are marked "manual inspection needed". A missing date reads "date not listed by the company", and it never substitutes today's date.
- Sends a daily email at the time you choose, when your plan has scheduled tasks and a connected email app. Otherwise it runs on demand and shows the report in chat.
- 
## How it stays honest
 
Every company gets one status per run: read in full, partly read, or could not read. The diff script checks the evidence behind each status and downgrades any it cannot support. A response cut off mid-record, a source that states more roles than were read, a feed stopped at its limit, an empty list from an unconfirmed board name, and a page asking for JavaScript all end up under "Needs a look". A company that could not be read keeps its old snapshot, so one bad day doesn't erase the baseline.
 
## Install
 
1. Download `hirewire.zip` from this repo.
2. In Claude, go to Customize > Skills, click "+", then "+ Create skill", and choose "Upload a skill".
3. Turn on "Code execution and file creation" under Settings > Capabilities. Skills need it.
Then start a chat and say something like:
 
> Use Hirewire to track Oxide, QuestDB and Apple for technical product manager roles. Email me at you@example.com at 8 AM Pacific.
 
Run it inside a Claude Project if you can. Hirewire saves `hirewire/snapshot.json` and `hirewire/config.json` to the project so the next check knows what you have already seen. Without a project, attach `snapshot.json` yourself at the start of each check.
 
## What your plan allows
 
| Scheduled tasks | Email app | What you get |
| --- | --- | --- |
| Yes | Yes | A daily email at the chosen time |
| No | Yes | An on-demand check that emails the report |
| Yes | No | A daily check with the report in the session |
| No | No | An on-demand check with the report in chat |
 
Note: The free plan doesn't include scheduled tasks, so it falls in the bottom row unless you have an email app connected.
 
## Repo layout
 
```
hirewire/
  SKILL.md                      Instructions Claude follows
  scripts/check_config.py       Validates the email, time, timezone, keywords and companies
  scripts/diff_roles.py         Compares reads with the snapshot and builds the report and email
  references/sources.md         How to read each kind of careers page, with notes from the fetch tests
  references/setup-and-email.md Scheduled task prompt and email rules
```
 
## Sources it can read
 
Plain careers pages, plus the job feeds behind Greenhouse, Lever, Ashby and Eightfold. I tested these through web fetch on Oxide, QuestDB, Apple, Stripe, OpenAI, Spotify, Mistral and Netflix. Two read in full, two were partial with a known size, two were cut off with no total, and two looked empty. Details are in `references/sources.md`.
 
## Known limits
 
- Sites that load their jobs with JavaScript, such as Apple's, read as empty. Hirewire reports them as "could not read" and asks you to check by hand.
- Large feeds, such as Netflix's 455 roles, are read in part. Hirewire marks them "partly read" and does not claim to have seen everything.
- Link checking relies on Claude fetching each page, so pages that need JavaScript come back as "manual inspection needed".
- The guardrails are instructions, not a technical lock. The skill tells Claude to email only your configured address, to edit only `hirewire/config.json` and `hirewire/snapshot.json`, and to ignore instructions found on careers pages. That makes a mistake much less likely, but nothing blocks a bad send. If you want a hard stop, connect an email app that can only create drafts, so you click send yourself.

## What I haven't tested
 
I tested the scripts against fixtures built from the fetch results: first runs, new, removed and filled roles, partial and empty reads, keyword matching, missing dates and links, and broken links. 

I have not tested these end to end: 
- Sending the email. I don't want to connect my email to Claude
- Creating and running the daily scheduled task.
- Saving and loading the snapshot from a Claude Project across separate chats and scheduled runs.
- Link checking against live job pages.
If you try one of these and something breaks, please send me a message on [LinkedIn](https://www.linkedin.com/in/gayatri-rao-pm/).
