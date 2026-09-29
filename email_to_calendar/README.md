# Email → Google Calendar

Email or forward something to **`you+cal@gmail.com`** and it shows up in your
Google Calendar a few minutes later: booking confirmations, appointment
letters (including PDF attachments), screenshots of tickets, `.ics` invites,
or a quick note you type yourself like

> Dentist next Tuesday 10:30, Nørrebrogade 12, bring insurance card

It runs as a [Google Apps Script](https://script.google.com) inside your own
Google account. You don't need a server, and Google doesn't charge for it.
Claude reads each email and pulls out the title, date/time, timezone, location
and useful details. You get a reply saying what was added.

`+cal` is a Gmail plus-address. Anything sent to `you+cal@gmail.com` lands in
your normal inbox, so you don't need a new account.

## Setup (about 5 minutes)

1. **Get a Claude API key** at <https://console.anthropic.com/settings/keys>.
2. Go to <https://script.google.com> → **New project**. Name it e.g. "Email to Calendar".
3. Delete the placeholder code in `Code.gs` and paste in the contents of
   [`Code.gs`](Code.gs) from this folder. Save.
4. **Project Settings** (gear icon) → **Script Properties** → **Add script property**:
   - `ANTHROPIC_API_KEY` = your key
5. Back in the **Editor**, pick `setup` in the function dropdown and click **Run**.
   Google will ask you to authorize access to Gmail and Calendar. Click
   *Advanced → Go to Email to Calendar (unsafe)*. The warning appears because
   you wrote the script yourself and haven't had Google review it.
6. The log prints the address to use (`you+cal@gmail.com`). Send it a test email.

To try it without creating anything, send an email, then run
`testLatestEmail`. The log shows what Claude extracted.

To stop, run `teardown`.

## Options

Set any of these as extra **Script Properties**. You don't need to edit the code.

| Property                   | Default                    | What it does |
|----------------------------|----------------------------|--------------|
| `CALENDAR_ADDRESS`         | `<you>+cal@<your domain>`  | The address to send to. Any plus-tag works, e.g. `you+appointments@gmail.com`. |
| `CALENDAR_ID`              | `primary`                  | Add to another calendar. Find the ID under the calendar's *Settings → Integrate calendar*. |
| `ALLOWED_SENDERS`          | *(just you)*               | Comma-separated extra addresses allowed to add events, e.g. a partner or your work email. Mail from anyone else is ignored. |
| `SEND_CONFIRMATION`        | `true`                     | Set to `false` to skip the "Added to your calendar" reply. |
| `DEFAULT_DURATION_MINUTES` | `60`                       | Length used when an email gives a start time but no end. |
| `CHECK_EVERY_MINUTES`      | `5`                        | 1, 5, 10, 15 or 30. Re-run `setup` after changing it. |
| `MODEL`                    | `claude-opus-5-5`          | Claude model used for extraction. |
| `EFFORT`                   | `low`                      | Claude's reasoning effort (`low`/`medium`/`high`). Kept low because Apps Script stops waiting for a web request after about a minute. |

## How it behaves

- **Only you can add events.** The script only acts on emails from your own
  address (plus any `ALLOWED_SENDERS`), so someone who learns the address
  can't put things in your calendar.
- **No invitations go out.** Events are created on your calendar only. Other
  people named in an email are never added as guests.
- **Each email is processed once.** Processed threads get a Gmail label:
  `cal-added` if events were created, `cal-failed` if something went wrong
  (you'll also get a reply explaining why). If the Claude API is briefly
  unavailable, the script leaves the email alone and tries again on the next run.
- **Timezones:** times are read in your calendar's timezone unless the email
  says otherwise. For example, a flight departing "14:05 local time" from New
  York is entered at the New York time.
- **One email can hold several events.** A trip itinerary becomes several
  events, and a date range such as a holiday becomes an all-day event covering
  those days.
- Every event's description links back to the email it came from.

## Cost

Each email is one Claude API call. For a typical confirmation email that's
a cent or two, a bit more with a multi-page PDF attached. You can see usage
at <https://console.anthropic.com>.
