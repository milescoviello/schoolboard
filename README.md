# schoolboard

A glanceable school board for the Mac mini at my Northeastern Oakland dorm:
today's classes, what's due, and course mail on one page.

Runs on a 2009 Core2Duo with 3.6 GB of RAM, so it is **stdlib-only** — no pip
packages, no JS framework, no webfonts. That CPU is x86-64-v1, which means every
dependency avoided is a wheel that cannot fail to build.

## Running it

```
schoolboard serve      # the web board (sysvinit service on the mini)
schoolboard today      # today's classes in the terminal
schoolboard sync       # pull Canvas + ingest mail now
schoolboard status     # config, sync state, and both clocks
schoolboard connect --token-file FILE    # add a Canvas token
```

Listens on `127.0.0.1` and the tailnet address only — never `0.0.0.0`, because
the dorm /19 passes unicast between clients.

## Where the data comes from

| Source | Freshness | Notes |
|---|---|---|
| Class timetable | static | `schedule.json`, transcribed from Student Hub |
| Canvas | 15 min | assignments, quizzes, discussions, announcements |
| Course mail | 30 min | pushed from another host; see below |
| Academic calendar | static | NU University-Wide Academic Calendar 2026–27 |

**Canvas** uses a personal access token. Student Hub sits behind SSO + Duo, and
scraping a push-MFA login is fragile in a way a token is not.

**Mail** is Microsoft Graph, but the OAuth credentials live on another host and
stay there — a Graph refresh token rotates on every refresh, so two clients
using one will invalidate each other. That host runs `schoolboard-mail-push`,
which exports recent metadata and drops `mail.json` here. Relevance is decided
on this side, against `schedule.json`, so it tunes without touching the other
machine.

## Two things it is deliberately careful about

**The host clock is wrong.** The mini is set to `America/New_York` while sitting
in Oakland. Every time on the board is computed in the campus timezone from
config; `schoolboard status` prints both so the drift is visible.

**An arrival time is not a deadline.** Announcements and mail carry a timestamp,
but it is when they showed up, not when anything is owed. `store.NON_WORK_KINDS`
keeps them out of "Due soon" — conflating the two renders every announcement as
overdue.

## Not in git

`config.json` (Canvas token), `mail.json` (real mail) and `*.db` are ignored.
