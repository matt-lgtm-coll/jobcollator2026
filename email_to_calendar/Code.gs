/**
 * Email → Google Calendar
 *
 * Every few minutes, looks for new mail sent to your "calendar" address
 * (a Gmail plus-address such as you+cal@gmail.com), asks Claude to pull out
 * any appointments/events, and adds them to your Google Calendar.
 *
 * Setup (see README.md for the full walkthrough):
 *   1. Paste this file into a new project at https://script.google.com
 *   2. Project Settings → Script Properties → add ANTHROPIC_API_KEY
 *   3. Run `setup` once from the editor and approve the permissions
 */

// ---------------------------------------------------------------------------
// Configuration. Any of these can be overridden with a Script Property of the
// same name (Project Settings → Script Properties), so you never have to edit
// the code itself.
// ---------------------------------------------------------------------------
const DEFAULTS = {
  // Address you send/forward mail to. Empty = "<your address>+cal@<domain>".
  CALENDAR_ADDRESS: '',
  // Calendar to add events to: 'primary' or a calendar ID from its settings page.
  CALENDAR_ID: 'primary',
  // Comma-separated extra senders allowed to add events. Your own address is
  // always allowed; mail from anyone else is ignored so strangers can't write
  // to your calendar.
  ALLOWED_SENDERS: '',
  // Reply to each email with what was added (or why nothing was).
  SEND_CONFIRMATION: 'true',
  // Length used when an event has a start time but no end time.
  DEFAULT_DURATION_MINUTES: '60',
  // How often the checker runs (1, 5, 10, 15 or 30 minutes).
  CHECK_EVERY_MINUTES: '5',
  MODEL: 'claude-opus-5-5',
  // Apps Script's UrlFetch gives up after roughly a minute, so keep this
  // modest; event extraction doesn't need deep reasoning.
  EFFORT: 'low',
};

const LABEL_ADDED = 'cal-added';
const LABEL_FAILED = 'cal-failed';
const PROCESSED_KEY = 'PROCESSED_MESSAGE_IDS';
const MAX_REMEMBERED_IDS = 300;
const MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024;

function config_(name) {
  const value = PropertiesService.getScriptProperties().getProperty(name);
  return value !== null && value !== '' ? value : DEFAULTS[name];
}

function ownerEmail_() {
  return Session.getEffectiveUser().getEmail().toLowerCase();
}

function calendarAddress_() {
  const configured = config_('CALENDAR_ADDRESS');
  if (configured) return configured.toLowerCase();
  const [local, domain] = ownerEmail_().split('@');
  return `${local}+cal@${domain}`;
}

// ---------------------------------------------------------------------------
// One-time setup
// ---------------------------------------------------------------------------

/** Run once from the editor: checks config, creates labels, installs the timer. */
function setup() {
  if (!PropertiesService.getScriptProperties().getProperty('ANTHROPIC_API_KEY')) {
    throw new Error('Add ANTHROPIC_API_KEY under Project Settings → Script Properties first.');
  }
  calendar_(); // fails early if CALENDAR_ID is wrong
  GmailApp.getUserLabelByName(LABEL_ADDED) || GmailApp.createLabel(LABEL_ADDED);
  GmailApp.getUserLabelByName(LABEL_FAILED) || GmailApp.createLabel(LABEL_FAILED);

  ScriptApp.getProjectTriggers()
    .filter((t) => t.getHandlerFunction() === 'checkInbox')
    .forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('checkInbox')
    .timeBased()
    .everyMinutes(Number(config_('CHECK_EVERY_MINUTES')))
    .create();

  Logger.log(`Ready. Email or forward anything to ${calendarAddress_()} ` +
             `and it will land in "${calendar_().getName()}".`);
}

/** Removes the timer, e.g. to pause the tool. */
function teardown() {
  ScriptApp.getProjectTriggers()
    .filter((t) => t.getHandlerFunction() === 'checkInbox')
    .forEach((t) => ScriptApp.deleteTrigger(t));
  Logger.log('Stopped checking for calendar emails.');
}

// ---------------------------------------------------------------------------
// Main loop (runs on the timer)
// ---------------------------------------------------------------------------

function checkInbox() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return; // previous run still going

  try {
    const address = calendarAddress_();
    const processed = loadProcessed_();
    const threads = GmailApp.search(`(to:${address} OR deliveredto:${address}) newer_than:3d`, 0, 20);

    for (const thread of threads) {
      for (const message of thread.getMessages()) {
        const id = message.getId();
        if (processed.includes(id) || !isAddressedTo_(message, address)) continue;

        const outcome = handleMessage_(message);
        if (outcome === 'retry') continue; // transient API trouble; try next run
        processed.push(id);
        saveProcessed_(processed);
        const label = { added: LABEL_ADDED, failed: LABEL_FAILED }[outcome];
        if (label) thread.addLabel(GmailApp.getUserLabelByName(label));
      }
    }
  } finally {
    lock.releaseLock();
  }
}

/** @return {'added'|'none'|'failed'|'retry'|'ignored'} */
function handleMessage_(message) {
  const sender = extractEmail_(message.getFrom());
  if (!allowedSenders_().includes(sender)) {
    Logger.log(`Ignoring mail from non-allowed sender ${sender}: "${message.getSubject()}"`);
    return 'ignored';
  }

  let result;
  try {
    result = extractEvents_(message);
  } catch (err) {
    if (err instanceof TransientError) {
      Logger.log(`Will retry "${message.getSubject()}": ${err.message}`);
      return 'retry';
    }
    Logger.log(`Failed on "${message.getSubject()}": ${err.stack || err}`);
    confirm_(message, `Sorry, I couldn't process this email:\n\n${err.message}`);
    return 'failed';
  }

  if (!result.events.length) {
    confirm_(message, `I didn't find anything to add to your calendar.` +
                      (result.note ? `\n\n${result.note}` : ''));
    return 'none';
  }

  const created = [];
  const problems = [];
  for (const ev of result.events) {
    try {
      created.push(createEvent_(ev, message));
    } catch (err) {
      problems.push(`• "${ev.title}": ${err.message}`);
    }
  }

  let body = '';
  if (created.length) body += `Added to your calendar:\n\n${created.join('\n')}`;
  if (problems.length) body += `\n\nCouldn't add:\n${problems.join('\n')}`;
  if (result.note) body += `\n\nNote: ${result.note}`;
  confirm_(message, body.trim());
  return created.length ? 'added' : 'failed';
}

// ---------------------------------------------------------------------------
// Claude
// ---------------------------------------------------------------------------

class TransientError extends Error {}

const EVENT_SCHEMA = {
  type: 'object',
  properties: {
    events: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string', description: 'Short calendar title, e.g. "Dentist – Dr. Jensen".' },
          all_day: { type: 'boolean' },
          start: { type: 'string', description: 'Timed: YYYY-MM-DDTHH:MM (24h, wall-clock in `timezone`). All-day: YYYY-MM-DD.' },
          end: { type: 'string', description: 'Same format as start. All-day: last day of the event (inclusive). Empty string if not stated.' },
          timezone: { type: 'string', description: 'IANA zone the times are in, e.g. "Europe/Copenhagen". Empty string = the calendar\'s own zone.' },
          location: { type: 'string', description: 'Address, room or video-call link. Empty string if none.' },
          description: { type: 'string', description: 'Useful details: what to bring, booking reference, contact, agenda. Empty string if none.' },
        },
        required: ['title', 'all_day', 'start', 'end', 'timezone', 'location', 'description'],
        additionalProperties: false,
      },
    },
    note: {
      type: 'string',
      description: 'Anything the user should know: assumptions made, ambiguities, or why no event was found. Empty string if nothing to say.',
    },
  },
  required: ['events', 'note'],
  additionalProperties: false,
};

function extractEvents_(message) {
  const calendarTz = calendar_().getTimeZone();
  const received = Utilities.formatDate(message.getDate(), calendarTz, "EEEE yyyy-MM-dd HH:mm");

  const system =
    'You turn emails into Google Calendar events. The user sends or forwards emails to a ' +
    'dedicated address when they want what\'s in them on their calendar: booking ' +
    'confirmations, appointment letters, invitations, tickets, or a quick note they wrote ' +
    'themselves like "lunch with Anna Friday 12:30 at Café Norden".\n\n' +
    'Extract every distinct event the user would want on their calendar. For a forwarded ' +
    'email, the user\'s own note at the top (if any) takes precedence over the forwarded ' +
    'content. Resolve relative dates ("tomorrow", "next Tuesday") against the date the ' +
    'email was sent. If a time is given without a timezone, it is in the calendar\'s ' +
    'timezone unless the email makes clear it\'s somewhere else. If there is a start time ' +
    'but no end or duration, leave end empty. Don\'t invent details that aren\'t in the ' +
    'email. For flights or trains, use departure/arrival times in their local zones. ' +
    'Ignore marketing, signatures and legal boilerplate. If the email has no event in it, ' +
    'return an empty events list and say why in note.';

  const header =
    `Calendar timezone: ${calendarTz}\n` +
    `Email sent: ${received} (${calendarTz})\n` +
    `From: ${message.getFrom()}\n` +
    `Subject: ${message.getSubject()}\n\n` +
    message.getPlainBody();

  const content = attachmentBlocks_(message);
  content.push({ type: 'text', text: header });

  const response = callClaude_({
    model: config_('MODEL'),
    max_tokens: 16000,
    output_config: {
      effort: config_('EFFORT'),
      format: { type: 'json_schema', schema: EVENT_SCHEMA },
    },
    // If a safety classifier declines, retry on Anthropic's recommended fallback model.
    fallbacks: 'default',
    system: system,
    messages: [{ role: 'user', content: content }],
  });

  if (response.stop_reason === 'refusal') {
    throw new Error('Claude declined to process this email.');
  }
  if (response.stop_reason === 'max_tokens') {
    throw new Error('The response was cut off; the email may contain too many events.');
  }
  const text = response.content.find((b) => b.type === 'text');
  if (!text) throw new Error('Claude returned no answer.');
  return JSON.parse(text.text);
}

function callClaude_(payload) {
  const apiKey = PropertiesService.getScriptProperties().getProperty('ANTHROPIC_API_KEY');
  if (!apiKey) throw new Error('ANTHROPIC_API_KEY is not set in Script Properties.');

  let res;
  try {
    res = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
      method: 'post',
      contentType: 'application/json',
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'anthropic-beta': 'server-side-fallback-2026-07-01',
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
    });
  } catch (err) {
    throw new TransientError(`network error: ${err.message}`); // includes UrlFetch timeouts
  }

  const status = res.getResponseCode();
  const body = res.getContentText();
  if (status === 200) return JSON.parse(body);

  let detail = body;
  try { detail = JSON.parse(body).error.message; } catch (e) { /* keep raw body */ }
  if (status === 429 || status === 529 || status >= 500) {
    throw new TransientError(`API ${status}: ${detail}`);
  }
  throw new Error(`Claude API error ${status}: ${detail}`);
}

/** PDFs, images and .ics files attached to the email, as Claude content blocks. */
function attachmentBlocks_(message) {
  const blocks = [];
  let total = 0;
  for (const att of message.getAttachments({ includeInlineImages: false })) {
    const type = (att.getContentType() || '').split(';')[0].toLowerCase();
    const name = att.getName() || '';
    const size = att.getSize();
    if (total + size > MAX_ATTACHMENT_BYTES) {
      Logger.log(`Skipping attachment ${name}: over the ${MAX_ATTACHMENT_BYTES} byte budget`);
      continue;
    }

    if (type === 'text/calendar' || /\.ics$/i.test(name)) {
      blocks.push({ type: 'text', text: `Attached calendar file ${name}:\n${att.getDataAsString()}` });
    } else if (type === 'application/pdf') {
      blocks.push({
        type: 'document',
        source: { type: 'base64', media_type: 'application/pdf', data: Utilities.base64Encode(att.getBytes()) },
      });
    } else if (['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(type)) {
      blocks.push({
        type: 'image',
        source: { type: 'base64', media_type: type, data: Utilities.base64Encode(att.getBytes()) },
      });
    } else {
      continue;
    }
    total += size;
  }
  return blocks;
}

// ---------------------------------------------------------------------------
// Calendar
// ---------------------------------------------------------------------------

function calendar_() {
  const id = config_('CALENDAR_ID');
  const cal = id === 'primary' ? CalendarApp.getDefaultCalendar() : CalendarApp.getCalendarById(id);
  if (!cal) throw new Error(`No calendar found with ID "${id}".`);
  return cal;
}

/** Creates one event and returns a human-readable line describing it. */
function createEvent_(ev, message) {
  const cal = calendar_();
  const calTz = cal.getTimeZone();
  const tz = ev.timezone || calTz;
  const title = ev.title || message.getSubject() || 'Event';
  const description = [
    ev.description,
    `Added from email "${message.getSubject()}": https://mail.google.com/mail/u/0/#all/${message.getId()}`,
  ].filter(Boolean).join('\n\n');
  const options = { description: description };
  if (ev.location) options.location = ev.location;

  if (ev.all_day) {
    const start = parseDate_(ev.start.slice(0, 10), 'yyyy-MM-dd', tz);
    const lastDay = ev.end ? parseDate_(ev.end.slice(0, 10), 'yyyy-MM-dd', tz) : start;
    const endExclusive = new Date(lastDay.getTime() + 24 * 3600 * 1000);
    cal.createAllDayEvent(title, start, endExclusive, options);
    const range = ev.end && ev.end.slice(0, 10) !== ev.start.slice(0, 10)
      ? `${ev.start.slice(0, 10)} – ${ev.end.slice(0, 10)}` : ev.start.slice(0, 10);
    return `• ${title} — ${range} (all day)${ev.location ? ` @ ${ev.location}` : ''}`;
  }

  const start = parseDate_(ev.start.slice(0, 16), "yyyy-MM-dd'T'HH:mm", tz);
  let end = ev.end ? parseDate_(ev.end.slice(0, 16), "yyyy-MM-dd'T'HH:mm", tz) : null;
  if (!end || end <= start) {
    end = new Date(start.getTime() + Number(config_('DEFAULT_DURATION_MINUTES')) * 60 * 1000);
  }
  cal.createEvent(title, start, end, options);
  const fmt = (d) => Utilities.formatDate(d, calTz, 'EEE d MMM yyyy HH:mm');
  const endFmt = Utilities.formatDate(end, calTz, 'HH:mm');
  return `• ${title} — ${fmt(start)}–${endFmt}${ev.location ? ` @ ${ev.location}` : ''}`;
}

function parseDate_(text, pattern, tz) {
  const d = Utilities.parseDate(text, tz, pattern);
  if (isNaN(d.getTime())) throw new Error(`couldn't read the date "${text}"`);
  return d;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function extractEmail_(from) {
  const m = String(from).match(/<([^>]+)>/);
  return (m ? m[1] : String(from)).trim().toLowerCase();
}

function allowedSenders_() {
  const extra = String(config_('ALLOWED_SENDERS') || '')
    .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
  return [ownerEmail_(), ...extra];
}

function isAddressedTo_(message, address) {
  const recipients = [message.getTo(), message.getCc(), message.getBcc()].join(',').toLowerCase();
  if (recipients.includes(address)) return true;
  // Mail sent by someone else to the alias shows it only in Delivered-To.
  const deliveredTo = message.getHeader('Delivered-To') || '';
  return deliveredTo.toLowerCase().includes(address);
}

function confirm_(message, text) {
  if (config_('SEND_CONFIRMATION') !== 'true') return;
  // Replies go to the sender (you), not the calendar address, so they can't loop.
  message.reply(text);
}

function loadProcessed_() {
  const raw = PropertiesService.getScriptProperties().getProperty(PROCESSED_KEY);
  return raw ? JSON.parse(raw) : [];
}

function saveProcessed_(ids) {
  PropertiesService.getScriptProperties()
    .setProperty(PROCESSED_KEY, JSON.stringify(ids.slice(-MAX_REMEMBERED_IDS)));
}

// ---------------------------------------------------------------------------
// Manual testing
// ---------------------------------------------------------------------------

/**
 * Dry run: shows what would be extracted from the most recent email to the
 * calendar address, without creating anything. Run it from the editor and
 * check the execution log.
 */
function testLatestEmail() {
  const address = calendarAddress_();
  const threads = GmailApp.search(`to:${address} OR deliveredto:${address}`, 0, 1);
  if (!threads.length) {
    Logger.log(`No emails to ${address} found yet — send one and try again.`);
    return;
  }
  const messages = threads[0].getMessages();
  const message = messages[messages.length - 1];
  Logger.log(`Subject: ${message.getSubject()}`);
  Logger.log(JSON.stringify(extractEvents_(message), null, 2));
}
