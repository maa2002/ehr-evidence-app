# API and Interface

Phase 8 puts the application in front of a person
([ADR 0019](decisions/0019-api-and-interface.md)). A local API serves the
development case sets: it answers questions about a selected case and
scope, explains a case's prepared prediction and shows the evidence
behind both. The interface, one page served by a separate process,
reaches it only over HTTP, and one launcher starts both on 127.0.0.1.
Nothing in the
evidence, the generation or the release gate changes: the API returns the
gate's response exactly, and nothing the gate withheld. Command examples
assume the session set-up in
[development.md](development.md#session-set-up).

## The Service

`ehr_evidence_app.api.service` holds what the application answers from:
for `dev-30-v1` and `pilot-10-v1`, the pinned QA bundle set, the
explanation bundles and the admitted cases, each verified against its pin
when read; the condition vocabulary; the execution limits; and the
recorder of the protected runtime records. `eval-100-v1` is not served,
so that its cases are not seen before the final evaluation.

A question goes through `qa.answer.answer` and an explanation through
`validation.explanation`, as in 0.9.0, and each writes its protected
runtime record ([generation.md](generation.md#runtime-records)). A request
returns the gate's response with who wrote it: the model, or the template
with the fallback and its category, and the gate's reason codes when it
withheld the model's candidate.

The model loads in the background when the API starts. Until it has
loaded, or when it cannot load, the templates write every response as a
recorded fallback (`no_model`), and `GET /health` says whether the model
is loading, ready or unavailable. The model writes one request at a time;
a second request meanwhile gets the template's response at once
(`busy`).

## The Routes

| Route | Returns |
| :--- | ---: |
| `GET /health` | The Model's State, the Served Case Sets and the Versions; Open Without the Token |
| `GET /case-sets` | The Served Case Sets and Their Numbers of Admitted Cases |
| `GET /case-sets/{case_set}/cases` | The Admitted Cases, With Their Numbers of Visits and Whether Each Has a Prepared Prediction |
| `GET /case-sets/{case_set}/cases/{case_number}` | The Case's Scopes (the Pre-Target History, Each Visit, All Case Visits) and Its Prepared Prediction |
| `GET /case-sets/{case_set}/cases/{case_number}/evidence?kind=…&visit=…` | The Evidence of a Scope (`kind` a Scope Kind, With `visit` for One Visit), or of the Prediction (`kind=prediction`) |
| `POST /questions` | The Response to a Question About a Case in a Scope: `{"case_set", "case_number", "scope", "question"}` |
| `POST /explanations` | The Explanation of a Case's Prepared Prediction: `{"case_set", "case_number"}` |

The evidence of a prediction also holds `in_record`: the final
admission's visit number, the coverage of its records, and those of its
records that match the predicted diagnosis, with how, by the condition
rules' matching ([ADR 0025](decisions/0025-prediction-against-the-record.md)).

The evidence of a scope holds its visits (reference, number, role and
dates), its records (reference, visit number, domain, concept, source
code and dates), its coverage, with the references of the records without
a standard concept, and its provenance, the digest of every input. The
evidence of the prediction holds the prediction with its score's fixed
meaning, every original attribution with its score, attachment and link
status, the linked history records, the final admission's records as
context, the case's restrictions and the provenance. The native database
identifiers of visits and records are left out: references and visit
numbers trace every claim. When the case set's record bundles are
published, the evidence of a scope also holds its whole record
(`record`): the person, each visit with its admission, every record with
its visit's number and the part of its kind, the coverage of every kind
and the provenance, again without native identifiers.

A request the service cannot serve is answered with a bare code:
`case_set_not_served`, `case_unknown`, `scope_unknown`, `scope_invalid`,
`prediction_missing`, or `request_invalid` with the names of the fields
at fault, never their values. A body that cannot be read as JSON (not
UTF-8, or nested too deeply) is `request_invalid` too, an unknown route
`not_found` and a method a route does not take `method_not_allowed`.

## The Boundary

`ehr_evidence_app.api.boundary` wraps the API:

- it listens on 127.0.0.1 only, and a request must name that host and the
  port, so that a web page cannot reach it through a domain name rebound
  to this machine;
- a request carrying an `Origin` header, as every cross-origin request
  from a browser does, is refused, and no cross-origin header is sent;
- every route but `/health` needs the launch token, a random value made
  at each launch and handed to the interface in its process environment
  (`EHR_API_TOKEN`), sent as `Authorization: Bearer <token>`, compared in
  constant time and never logged;
- a request naming no host, or two, is refused like one naming
  another host;
- a body is read whole and bounded (16 KiB) before the API sees it;
- every response, a refusal included, is marked `Cache-Control: no-store`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and
  `X-Frame-Options: DENY`;
- an exception inside the API is answered with a bare code and logged by
  its type name only;
- the server's own access log, which would hold every path and query, is
  switched off.

The access log, `outputs\logs\api\api-<date>.log` in the workspace, holds
each request's method, route template (such as
`/case-sets/{case_set}/cases/{case_number}`), status and milliseconds,
never a value from the path or the query, a question or a response. The
log audit reads it with the other routine logs
(`generation.records --operator-real --audit-logs`).

## The Interface

`ehr_evidence_app.interface` is one page, HTML, CSS and JavaScript without
a build step, served by the interface's own process
([ADR 0020](decisions/0020-interface-as-one-page.md)). The page calls the
interface's operations under `/api`, which reach the API only through
`interface.client`, on 127.0.0.1 with the launch token, ignoring proxy
settings so that no request leaves the machine. What the operations
return is what the page shows (`interface.views`), every word about a
value chosen there; the page builds everything it shows as text.

- **The patients** (work item 49). The sidebar selects the cohort, a case
  set by its plain name ("Development Cohort" for `dev-30-v1`, "Pilot
  Cohort" for `pilot-10-v1`; no case set's label reaches the page), and
  the patient. A patient is shown by a pseudonym, as case reports name
  one: a given name used for any sex and an initial, such as "Jordan K.",
  fixed by the case set's label and the case's number alone and never
  drawn from the records (`interface.names`). No given name comes twice
  among a case set's first sixty cases. The case's number stays beside the
  pseudonym, a tag and the sidebar say that the names are pseudonyms, and
  a patient can be found by either.
- **The conversation.** The questions and responses about the selected
  case form a thread; the composer below it holds the question, the scope
  and the model's state. The scope's menu groups Prior History (the
  pre-target history: the visits before the final admission) and All
  Visits apart from the single visits, each listed with its date and the
  final admission marked. A question is answered for the selected case and
  scope only, and
  nothing carries over from an earlier one. Selecting another patient
  starts a new conversation. Changing the scope keeps the earlier
  responses, greyed and labelled with the scope they answered, never shown
  as current.
- **Questions** (work item 50). Each response shows its outcome (answer,
  not recorded, clarification needed, abstained, case rejected, withheld
  by validation or operational error), its text with the references of
  each claim at the line that states it, the scope it answered and the
  seconds it took. Who wrote it, the model or a template, is not shown;
  its runtime record names the writer.
  Choosing a reference opens its record in the evidence panel, in the
  scope the response answered. A line's references stay together after
  its last word and wrap within the line when there are more than it
  holds, and each can be pressed a little beyond its edge.
- **Help.** Questions are read by fixed rules, so they have to ask for
  one of the kinds of information the rules read. Typing `/help` (with or
  without words after it), "help" or "what can I ask?" shows,
  in the conversation and without asking the API anything, that the
  questions are not a fixed list but must ask for one of the kinds below,
  then each kind of information with example questions, the predicted
  diagnosis and notes on the scope, other wordings and what is not
  answered. Selecting an example puts it in the box to change and send.
  Every example is read by the rules as its kind's question and answered
  on the invented record (`tests/interface/test_views.py`). Any other
  message that begins with a slash is answered as not a command; neither
  is sent as a question or recorded. The welcome names `/help`, and so
  does every response that asks back or declines.
- **Keyboard and failures.** Escape closes an open menu, then the
  evidence panel or the patient list; closing the panel from within it
  gives the focus back to the Evidence button. Copying a response is
  announced to screen readers. Until a patient has opened, the question
  box, the suggestions and the buttons that need a patient are disabled.
  A failure offers Try Again, except an ended session, whose link must be
  opened again.
- **The explanation** (work item 51). Generate the Predicted Diagnosis
  first says what it is, in plain words: a GNN model that predicts
  diagnoses read the record and predicted a diagnosis for the final
  admission, and the page shows that prediction and the recorded
  concepts its score was sensitive to; a model's output to inspect, not
  a diagnosis of the patient and not advice. The predicted diagnosis
  follows: its label; where it applies, the final admission after the
  earlier visits, with their numbers and dates; what it is, a prediction
  of a diagnosis recorded for the final admission, the one of the 275
  diagnoses the GNN model scores that it scored highest, made from the
  earlier visits and the final admission's other records but not its
  diagnoses; In the Record, whether it is recorded for the final
  admission, which the API checks in that visit's records with the
  condition rules' matching (the diagnosis itself, a record under an ICD
  code whose description names it, or a more specific diagnosis; "not
  recorded" only when that visit's records are complete, and never as
  clinical absence), apart from the explanation and never given to the
  language model; its score and logit, with the score in plain words and its
  fixed meaning, "Uncalibrated sigmoid of the logit; not a clinical
  risk", never as a percentage. Then
  the explanation of the selected drivers with its references, its fixed
  notes apart under Notes (among them that attribution scores are
  unsigned and not causal), and How to Read This, a folded glossary of
  its parts in plain words, each keeping the wording rules of the
  responses. The response's text is released and copied whole; only the
  page arranges it. The conditions recorded before the final admission
  stand in a block of their own, as records rather than the model's
  prediction or drivers.
- **The evidence** (work item 52). A panel beside the conversation shows,
  for a scope, its coverage, with the records without a standard concept
  and the final-admission records left out, its visits and records, and
  its provenance; for the prediction, every original attribution with its
  unsigned score, attachment and link status, a warning naming how many
  have no verified link to a record, the linked history records, the
  final admission's records as context, the case's restrictions, the
  coverage, the visits and the provenance. A reference opened from a
  response is found and marked; one the evidence does not hold is said to
  be missing. With the whole record, a scope's evidence also shows the
  person, each visit's admission, and a table for each kind of record
  (conditions as answers about the record cite them, procedures, ICU
  procedures, medications, laboratory results, microbiology, vital
  signs, transfers and ICU stays), long tables folded, and the coverage
  of every kind. The panel is resized by its edge, or its handle's arrow
  keys, and the browser remembers its width.

Labels, names and headings are in title case and sentences in sentence
case. The names of concepts are shown in title case wherever they appear,
in a response's text too, the rest of which is shown as released; the
runtime record keeps the text exactly. Dates are shown as dates ("2 Jan
2100") and the evidence's codes as words ("Final Admission").

A question or an explanation is a job (`interface.jobs`): the page is
answered at once, shows Thinking with the seconds so far, and asks after
the job about once a second, every other part of the page staying usable
meanwhile. A session reads only the jobs it started, and a finished job
is kept for a quarter of an hour. A refusal is shown in words.

The page is made of glass panels, translucent, blurred and rimmed with
light, floating over a soft backdrop; the conversation blurs and fades
where it scrolls under the bars. It follows the system's light or dark
setting; when the system asks for less transparency the glass turns
frostier, and where a browser cannot blur it is solid. It fits a phone's
screen.

| Route | Returns |
| :--- | ---: |
| `GET /api/state` | The Model's State, the Cohorts, the Question Limit and the Version |
| `GET /api/help` | What Can Be Asked, Shown When `/help` Is Typed: Why Questions Follow Set Patterns, Each Kind of Information With Example Questions, and Notes |
| `GET /api/case-sets/{case_set}/cases` | The Cohort's Patients, by Pseudonym, With Each Case's Number and Visits |
| `GET /api/case-sets/{case_set}/cases/{case_number}` | The Case: Its Pseudonym and Greeting, Its Scopes With Their Visits' Dates, Its Evidence and Its Predicted Diagnosis |
| `GET /api/case-sets/{case_set}/cases/{case_number}/evidence?kind=…&visit=…` | The Evidence of a Scope, or of the Prediction, in Sections of Facts, Tables and Notes |
| `POST /api/questions` | Starts Answering a Question: `{"case_set", "case_number", "scope", "question"}`; Returns the Job |
| `POST /api/explanations` | Starts Explaining the Prediction: `{"case_set", "case_number"}`; Returns the Job |
| `GET /api/jobs/{job}` | The Job's State, and Its Result Once Done |

What the page could not have sent is refused with a code and what it
means (`{"error", "message"}`), an unknown route or file as `not_found`
and a method a route does not take as `method_not_allowed`.

`interface.serve` serves the page and its operations on 127.0.0.1 behind
the interface's access checks: no access log, no server or date header,
no websocket, and a failure of an operation logged by its type only.

## The Access Checks of the Interface

`ehr_evidence_app.interface.gate` stands in front of the page and its
operations, in the same process:

- a request must name one host the interface answers to: 127.0.0.1 or
  localhost at its port and, when the interface is shared, the tunnel's
  public host;
- a request that changes anything (any method but GET and HEAD) must come
  from the page itself: its `Origin` must be the host it was sent to, so
  that another site cannot make a browser send it;
- every response carries a content security policy that admits the
  page's own script, style sheet and icon only, with no inline code, and
  requires Trusted Types; it is marked `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
  `Cross-Origin-Opener-Policy: same-origin` and
  `X-Robots-Tag: noindex, nofollow`, and every one but the page's static
  files `Cache-Control: no-store`; the static files are named by a digest
  of their content and checked again before each use;
- no websocket opens;
- the server's access log is switched off, and the gate's own log,
  `outputs\logs\interface\interface-<date>.log`, names only what it
  decided, never a path, a query or a key.

When the interface is shared, a request from the tunnel (it names the
public host, or carries Cloudflare's headers) is let in only with a
session. The first visit opens the secret link, whose key, the query
parameter `k`, is checked against its SHA-256 in constant time; the gate
sets a session cookie (`HttpOnly`, `Secure`, `SameSite=Lax`, because the
link is opened from another site) and redirects to the page without the
key. Anything else is answered as not found. Sessions end after a day,
or when the interface stops; a job started through the tunnel belongs to
its session.

## Sharing Through the Tunnel

`ehr_evidence_app.interface.share` keeps the parts of the sharing, all in
the workspace's `sharing` directory or the runtime root and none in the
repository:

- the secret link's record, `sharing\share-link.json`: the public host,
  the SHA-256 of the link's key and the date. `--new-link HOST` makes a new
  key, keeps its digest only, and prints the link,
  `https://HOST/?k=<key>`, once; a new link retires the one before it;
- the tunnel's token, `sharing\tunnel-token.txt`, saved by the owner and
  read by cloudflared alone (`--token-file`); this project checks it with
  `lstat` only and never reads, prints or copies it;
- cloudflared, the tunnel's connector, version 2025.4.0 or later, at
  `tools\cloudflared.exe` under the runtime root.

`--check` checks the three and prints the host and findings only. The
launcher's `--share` checks them too, gives the interface the host and
the key's digest, and starts cloudflared (`tunnel --no-autoupdate
--loglevel warn run --token-file …`) in an environment without the launch
token; it then checks that the public address answers as the gate
answers a visit without the key. The tunnel stops first when the launcher
stops.

Setting the tunnel up is the owner's, once, in Cloudflare's dashboard:

1. Put a domain on Cloudflare: Domains, Onboard a domain, the Free plan,
   and the registrar's nameservers replaced by the two that Cloudflare
   names. The domain is ready when Cloudflare shows it as Active.
2. Under Networking, Tunnels, create a tunnel of the cloudflared kind. Do
   not run the install command that the dashboard shows: it would install
   cloudflared as a Windows service that runs whenever the laptop is on,
   where the launcher runs it only while the interface is shared (a
   service installed that way is removed, as administrator, with
   `cloudflared.exe service uninstall`). Save the command's token, the
   string beginning `eyJ`, alone, in `sharing\tunnel-token.txt` in the
   workspace. The tunnel's Overview shows the command again (Add a
   replica), and Refresh token replaces a token that has been exposed.
3. On the tunnel's Routes tab, add a published application: the subdomain
   `ehr` (one level, which Cloudflare's free certificate covers), the
   domain, no path, and the service URL `http://127.0.0.1:8501`, leaving
   the other settings, the HTTP Host header among them, as they are.
   Cloudflare adds the DNS record itself.
4. Turn on Always Use HTTPS (the domain's SSL/TLS, Edge Certificates), so
   that an address typed with `http://` is sent on to `https://` before it
   reaches the tunnel.
5. Save `cloudflared.exe` from Cloudflare's releases in `tools` under the
   runtime root.
6. Check the parts, make the link and share:

   ```powershell
   & $AppPython -m ehr_evidence_app.interface.share --check
   & $AppPython -m ehr_evidence_app.interface.share --new-link ehr.example.org
   & $AppPython -m ehr_evidence_app.api.launch --operator-real --share
   ```

7. Give the link to the supervisor privately, in an issue of the
   private repository or a direct message, never in a committed file, and
   make a new link once the marking is over. The link stays the same from
   one launch to the next until then. The supervisor must hold MIMIC-IV
   credentialed access.

## Starting the Application

`ehr_evidence_app.api.launch` starts the API and the interface as two
processes on 127.0.0.1, with a fresh launch token that each receives in
its process environment only; waits until the API answers, then the
interface; prints the interface's address; and stops both together on
Ctrl+C, or when either stops. Each runs in a process group of its own,
so that Ctrl+C reaches the launcher only. The launcher holds each one's
standard input open and asks it to stop by closing it; a process watches
for that by polling the pipe, never reading it, because on Windows a read
left waiting on a pipe holds up every other use of it, the start-up of
the next native library a process loads included. A process also stops
when the launcher ends in any other way, the API stopping the model
worker as it does, and every process is in a Windows job that ends it
when the launcher ends, however it ends. Their own output is discarded.
The development case sets take about 20 seconds to load and verify, and
the model about 16 seconds more.

By default the API serves the invented case set with its scripted model,
reading and writing nothing: a demonstration without data.
`--operator-real` serves the development case sets with the model, and
`--no-model` with the templates only.

```powershell
& $AppPython -m ehr_evidence_app.api.launch
& $AppPython -m ehr_evidence_app.api.launch --operator-real
```

## Operator Run

Phase 8 closed with an operator run on the final code: the real API on
`dev-30-v1` with the model, driven over loopback, for the first 3
admitted cases. It made 24 requests, for each case: a list in the
pre-target history and in all case visits, a list asked politely, two
presence checks, two questions about the whole record and the
explanation. The model wrote 6 of the 6 presence checks, 5 of the 9
lists (the other 4 were longer than the output limit or cut off) and 1
of the 3 explanations (the gate withheld 2); the templates wrote the
answers about the record. No case or scope changed, no withheld outcome
released text, and every request wrote its runtime record. The record
check found each prediction's diagnosis recorded for the final
admission. The median request took 13.6 s, the slowest 62.8 s. The log
audit (`generation.records --operator-real --audit-logs`) found nothing
but routes and statuses in the API's log.

## Commands

The default mode serves the invented case set on a free loopback port,
with a scripted model, and checks every route and the boundary over a
real connection; nothing is written:

```powershell
& $AppPython -m ehr_evidence_app.api.app
```

The interface's default mode serves the invented case set through the API
and the page's operations on free loopback ports, and makes every
operation as the page makes it, the gate's refusals included; nothing is
written:

```powershell
& $AppPython -m ehr_evidence_app.interface.app
```

`--serve` serves on 127.0.0.1, taking the launch token from
`EHR_API_TOKEN`: the invented case set, or with `--operator-real` the
development case sets with the model. The interface takes the API's
address and the token from `EHR_API_URL` and `EHR_API_TOKEN`. The
launcher sets all three; the processes can be started by hand only for a
diagnosis:

```powershell
& $AppPython -m ehr_evidence_app.api.app --serve --port 8601 --operator-real
& $AppPython -m ehr_evidence_app.interface.serve --port 8501
```
