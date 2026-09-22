// api/sseFraming.ts
//
// An incremental parser for the `text/event-stream` wire format (WHATWG
// HTML's "Server-sent events" / "Interpreting an event stream"), used by
// api/sseFetchReader.ts to turn a fetch() ReadableStream's raw bytes into
// discrete `{event, data, id}` frames. Round 4: this replaces the browser's
// native EventSource parser, which round 4's report showed cannot
// authenticate this stream off-loopback/off-https (see
// sseFetchReader.ts's module docstring for the full story) -- so this
// dashboard now parses the wire itself.
//
// Deliberately NOT a full implementation of every field the spec defines:
// `retry:` is read and discarded (this app manages its own reconnect
// backoff explicitly, in sseFetchReader.ts, rather than trusting a
// server-suggested value), and BOM stripping / bad-UTF-8 handling is left
// to the caller's TextDecoder. What IS implemented: `event:`, `data:`
// (repeatable, joined with `\n`), `id:` (an id containing a NUL byte is
// ignored, per spec -- it never becomes a Last-Event-ID a broken/hostile
// server could poison), comment lines (`:` prefix), CRLF/LF/CR line
// endings including a terminator split across two `push()` calls, and a
// frame with no `data:` line at all is never dispatched (matches the
// spec's own "if the data buffer is an empty string, set it to empty and
// return" -- but this app's fixtures always send at least an empty `data:`,
// so "never dispatched" in practice means "no `data:` line appeared").

export interface ParsedSSEEvent {
  /** Defaults to "message" (the spec's own default) when no `event:` line
   *  appeared in the frame. embodiment/bus.py's projection always sends
   *  one -- see tests/fixtures/events/schema.json's `kind` field -- so
   *  "message" is never actually seen against the real wire. */
  event: string;
  data: string;
  id?: string;
}

export class SSEFrameParser {
  private buffer = "";
  private eventType = "";
  private dataLines: string[] = [];
  private lastId: string | undefined;

  /** Feed one chunk of already-decoded text. Returns every event that
   *  became complete (terminated by a blank line) as a result. Never
   *  throws: a malformed line is either ignored (unknown field name) or
   *  treated as a field with an empty value (no colon at all) -- there is
   *  no input shape this can reject outright. */
  push(chunk: string): ParsedSSEEvent[] {
    // Normalize on the FULL accumulated buffer, not just the new chunk, so
    // a CRLF terminator split exactly at the \r|\n boundary across two
    // push() calls still normalizes to one line ending rather than two.
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, "\n").replace(/\r/g, "\n");

    const events: ParsedSSEEvent[] = [];
    let newlineIdx: number;
    while ((newlineIdx = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, newlineIdx);
      this.buffer = this.buffer.slice(newlineIdx + 1);

      if (line === "") {
        const dispatched = this.dispatch();
        if (dispatched) events.push(dispatched);
        continue;
      }
      if (line.startsWith(":")) continue; // comment line, ignored

      const colonIdx = line.indexOf(":");
      let field: string;
      let value: string;
      if (colonIdx === -1) {
        field = line;
        value = "";
      } else {
        field = line.slice(0, colonIdx);
        value = line.slice(colonIdx + 1);
        if (value.startsWith(" ")) value = value.slice(1); // exactly one leading space, per spec
      }

      switch (field) {
        case "event":
          this.eventType = value;
          break;
        case "data":
          this.dataLines.push(value);
          break;
        case "id":
          // eslint-disable-next-line no-control-regex
          if (!/\u0000/.test(value)) this.lastId = value; // NUL-bearing id: ignored, per spec
          break;
        default:
          break; // "retry:" and any unrecognized field: ignored
      }
    }
    return events;
  }

  private dispatch(): ParsedSSEEvent | null {
    const hasData = this.dataLines.length > 0;
    const event = hasData
      ? { event: this.eventType || "message", data: this.dataLines.join("\n"), id: this.lastId }
      : null;
    this.eventType = "";
    this.dataLines = [];
    return event;
  }
}
