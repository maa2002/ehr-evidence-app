# 0020. The Interface as One Page Over the API (Phase 8)

Status: accepted (2026-09-29). Amends points 1 and 7 of
[ADR 0019](0019-api-and-interface.md).

## Context

The first interface was a Streamlit page (ADR 0019). In use it froze when
a view other than the questions was opened, for two reasons:

- The launcher asked each process it started to stop by closing that
  process's standard input, and each process kept a thread blocked
  reading it. On Windows every other use of a pipe waits behind a read
  left pending on it, a native library's start-up included, so the first
  library a process loaded after the watch began stalled the whole
  process. Streamlit loaded NumPy, through pandas, the first time it drew
  a table, which the explanation and evidence views do.
- Streamlit runs the whole page again on every interaction, in one
  thread per session, so a question the model takes 20 to 80 seconds
  over held the page until it was answered.

The page also looked like a form rather than a conversation, and I
wanted a conversation layout: the patients in a sidebar, questions and
answers as a thread, and the evidence beside them. A page shared through
the tunnel cannot wait on the model through one request either: the
tunnel's edge ends a request that has not been answered in 100 seconds.

## Decision

1. **One page.** The interface process serves one page, HTML, CSS and
   JavaScript without a build step or a library, with Starlette, which
   FastAPI already brings. Streamlit, and the 16 packages only it needed,
   leave the lock. The processes, the launcher, the API and its boundary
   are unchanged.
2. **The page's operations.** The page calls the interface's own
   operations under `/api`, which reach the API only through
   `interface.client`, as before. They return what the page shows
   (`interface.views`): every word about a value is chosen, and tested, in
   Python; the page lays values out as text and never parses a string as
   markup.
3. **Jobs.** A question or an explanation runs in the background; the page
   is answered at once and asks after the job about once a second, so
   that nothing waits on the model and no request through the tunnel
   outlives its limit. A job belongs to the session that started it.
4. **The conversation (decisions D4 to D6 kept).** The sidebar selects the
   cohort and the patient; the conversation holds the selected case's
   questions and responses. Each response shows its outcome, its text
   with each claim's references at the line that states it, the scope it
   answered, who wrote it and the seconds it took. A case change starts a
   new conversation; a scope change greys the earlier responses and
   labels them with the scope they answered. The explanation shows the
   prepared prediction with its score's fixed meaning before its text,
   the note that attribution scores are unsigned after it, and the
   conditions recorded in the pre-target history in a block of their own.
   The evidence opens in a panel beside the conversation, on what a
   reference names.
5. **Names.** A case set is shown as a cohort ("Development Cohort",
   "Pilot Cohort"), and no case set's label reaches the page. A patient is
   shown by a pseudonym, as case reports name one: a given name used for
   any sex and an initial, fixed by the case set's label and the case's
   number alone, never drawn from the records. The page says that the
   names are pseudonyms, and keeps each case's number beside its name.
   Labels, names and headings are in title case, the names of concepts in
   a response's text included; the runtime record keeps the text as
   released.
6. **Security.** Every response carries a content security policy that
   admits the page's own script, style sheet and icon only, with no inline
   code, and requires Trusted Types. A request that changes anything must
   come from the page's own origin. No websocket is served.
7. **Stopping with the launcher.** A process the launcher started polls
   its standard input's pipe for the launcher's end to close, and never
   reads it.
8. **Tests.** The views, the jobs, the names, the gate and the
   application over real loopback connections are tested on invented
   cases, and the page's files are checked for inline code, other sites
   and markup parsing. The default mode of `interface.app` makes every
   operation of the page over loopback on the invented case set, in CI
   with the other default modes.

## Consequences

- Opening any view never waits on the model or stalls a process: the
  launcher's watch no longer holds the pipe, and the page reads a job's
  state instead of waiting on it.
- The lock pins 40 packages instead of 56; Starlette, Requests and NumPy,
  which the code imports, are declared directly.
- The page's script is not run by the test suite in a browser engine: it
  is kept thin over the tested views and checked statically, and it was
  checked by hand on the invented case set.
- The pseudonyms make the page easier to follow and identify no one; the
  report states that they are invented.
- The publication audit admits HTML, CSS, JavaScript and SVG in the page's
  own directory only.
