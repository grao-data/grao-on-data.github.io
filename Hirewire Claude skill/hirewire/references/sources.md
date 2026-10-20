# Sources and the current.json shape

Contents: finding the source, the company record, the role record, then notes on each kind of source from the first tests.

## Finding the source

1. Fetch the careers page with web fetch. Save the final address after redirects in `final_url`.
2. Look on the page for a link to a job board. The board name in the link is the confirmed name, so set `source_confirmed` to true. Common forms:
   - `boards.greenhouse.io/<board>` or `job-boards.greenhouse.io/<board>`
   - `jobs.lever.co/<board>`
   - `jobs.ashbyhq.com/<board>`
   - an address ending in `.eightfold.ai`, or a page like `explore.jobs.<company>.net/careers`
3. Look for a feed link on the page too. Oxide's careers page links a feed at `/careers/feed`. That feed was not fetched in the tests.
4. With no board link, read the page itself.
5. If the page lists no roles and shows a notice asking for JavaScript, or says "no results", stop and mark the company `could_not_read`.

## The company record in current.json

```json
{
  "checked_at": "2026-10-12T08:00:00-07:00",
  "companies": [
    {
      "name": "Example Co",
      "careers_url": "https://example.com/careers",
      "final_url": "https://example.com/careers",
      "method": "page",
      "status": "read",
      "reason": "",
      "roles": [
        {"id": "abc123", "title": "Staff Engineer", "location": "Remote", "team": "Platform",
         "url": "https://example.com/careers/abc123", "posted_at": "2026-10-07", "open": true}
      ]
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `name` | The user's name for the company. It is the snapshot key. |
| `careers_url`, `final_url` | The address given and the address after redirects. |
| `method` | `page` or `feed`. |
| `status` | `read`, `partial` or `could_not_read`. |
| `reason` | Why, required unless the status is `read`. |
| `roles` | The role records. |
| `total_stated` | The total the source says it has, when it states one. |
| `limit_used` | The limit you asked a feed for. |
| `closed_cleanly` | `false` when the response ended mid-record or lacks its closing bracket. |
| `page_signals` | Warning signs, such as `javascript_notice`, `zero_results_with_filter` or `login_wall`. |
| `confirmed_empty` | `true` only when the source itself says there are no open roles. |
| `source_confirmed` | `true` when the board name came from a link on the careers page. |

## The role record

| Field | Meaning |
| --- | --- |
| `id` | The source's own ID. Required. With no ID, use the role link. |
| `title` | Required. |
| `location`, `team` | As the source gives them. |
| `url` | The role's own page. Required for the report. Relative links are resolved against the careers page. |
| `posted_at` | The posting date, as an ISO date, text such as "Oct 7, 2026", or an epoch number in seconds or milliseconds. Leave empty when the source gives none. Never fill it with today's date. |
| `open` | `false` when the source marks the role closed. Defaults to true. |

## What the first tests showed

Eight companies were tested through web fetch. Two read in full, two were partial with a known size, two were cut off with no total, and two looked empty.

### Own careers pages

- Oxide listed 13 roles with titles only, no team or location. It read in full.
- QuestDB listed title, team and location, and kept filled roles on the page with a "Filled" label. Mark those `"open": false`. It read in full.
- Apple's jobs site loads its roles in the browser. Web fetch saw "There are no results that match your search" and a notice asking for JavaScript, on both tries. Mark it `could_not_read`, with `page_signals` set to `javascript_notice` and `zero_results_with_filter`. Tell the user to check that site by hand.

### Greenhouse

- Address: `https://boards-api.greenhouse.io/v1/boards/<board>/jobs`
- Map `id`, `title`, `location.name`, `absolute_url` and `first_published` (as `posted_at`).
- The Stripe board stopped mid-record after several hundred roles, with no total in view. Mark it `partial` with `closed_cleanly` false.
- `updated_at` was nearly identical across the whole board, so it looked like a board-wide refresh time. Do not use it.
- There is no status field, so a closed role simply disappears. No limit option was tested.

### Lever

- Address: `https://api.lever.co/v0/postings/<board>?mode=json&limit=<n>`
- Map `id`, `text` (title), `categories.location`, `categories.team`, `hostedUrl` and `createdAt` (milliseconds, as `posted_at`).
- A limit of 5 on Spotify returned exactly 5 roles and closed cleanly. Set `limit_used` so the script marks it partial.
- A board name that does not exist or has no roles returns an empty list with no error. Mistral returned `[]` and the cause could not be told. This is why board names must be confirmed.
- Paging through the rest of a board was not tested.

### Ashby

- Address: `https://api.ashbyhq.com/posting-api/job-board/<board>`
- Map `id`, `title`, `team`, `location`, `jobUrl`, `publishedAt` (as `posted_at`) and `isListed` (as `open`).
- Each record carries the full job description, so responses are large. The OpenAI board stopped after 10 roles with no closing bracket. Mark it `partial` with `closed_cleanly` false.

### Eightfold

- Address: the careers page itself, for example `https://explore.jobs.netflix.net/careers`.
- The first batch of roles is embedded in the page as JSON: a `positions` list and a `count` total. Map `id`, `name` (title), `location`, `department` (team), `canonicalPositionUrl` and `t_create` (seconds, as `posted_at`).
- Netflix returned 10 of a stated 455, newest first. Set `total_stated` to 455 so the script marks it partial. Paging through the rest was not tested.
