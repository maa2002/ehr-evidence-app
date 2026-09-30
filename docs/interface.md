# API and interface

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

## The service

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

## The routes

| Route | Returns |
| --- | --- |
| `GET /health` | The model's state, the served case sets and the versions; open without the token |
| `GET /case-sets` | The served case sets and their numbers of admitted cases |
| `GET /case-sets/{case_set}/cases` | The admitted cases, with their numbers of visits and whether each has a prepared prediction |
| `GET /case-sets/{case_set}/cases/{case_number}` | The case's scopes (the pre-target history, each visit, all case visits) and its prepared prediction |
| `GET /case-sets/{case_set}/cases/{case_number}/evidence?kind=…&visit=…` | The evidence of a scope (`kind` a scope kind, with `visit` for one visit), or of the prediction (`kind=prediction`) |
| `POST /questions` | The response to a question about a case in a scope: `{"case_set", "case_number", "scope", "question"}` |
| `POST /explanations` | The explanation of a case's prepared prediction: `{"case_set", "case_number"}` |

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

## The boundary

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

## The interface

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
  each claim at the line that states it, the scope it answered, who wrote
  it (the model, or the template and why) and the seconds it took.
  Choosing a reference opens its record in the evidence panel, in the
  scope the response answered.
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
| --- | --- |
| `GET /api/state` | The model's state, the cohorts, the question limit and the version |
| `GET /api/case-sets/{case_set}/cases` | The cohort's patients, by pseudonym, with each case's number and visits |
| `GET /api/case-sets/{case_set}/cases/{case_number}` | The case: its pseudonym and greeting, its scopes with their visits' dates, its evidence and its predicted diagnosis |
| `GET /api/case-sets/{case_set}/cases/{case_number}/evidence?kind=…&visit=…` | The evidence of a scope, or of the prediction, in sections of facts, tables and notes |
| `POST /api/questions` | Starts answering a question: `{"case_set", "case_number", "scope", "question"}`; returns the job |
| `POST /api/explanations` | Starts explaining the prediction: `{"case_set", "case_number"}`; returns the job |
| `GET /api/jobs/{job}` | The job's state, and its result once done |

What the page could not have sent is refused with a code and what it
means (`{"error", "message"}`), an unknown route or file as `not_found`
and a method a route does not take as `method_not_allowed`.

`interface.serve` serves the page and its operations on 127.0.0.1 behind
the interface's access checks: no access log, no server or date header,
no websocket, and a failure of an operation logged by its type only.

## The access checks of the interface

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

## Sharing through the tunnel

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

Setting the tunnel up is the owner's, once:

1. Put a domain on Cloudflare.
2. In Cloudflare's dashboard, create a tunnel of the cloudflared kind,
   skip installing its connector, and save the token from the install
   command, alone, in `sharing\tunnel-token.txt` in the workspace.
3. Give the tunnel a public hostname on the domain, such as `ehr`, with
   the service `http://127.0.0.1:8501`, leaving the HTTP Host header as it
   is.
4. Save `cloudflared.exe` from Cloudflare's releases in `tools` under the
   runtime root.
5. Check the parts, make the link and share:

   ```powershell
   & $AppPython -m ehr_evidence_app.interface.share --check
   & $AppPython -m ehr_evidence_app.interface.share --new-link ehr.example.org
   & $AppPython -m ehr_evidence_app.api.launch --operator-real --share
   ```

6. Put the link in an issue of the private repository for the
   supervisor, never in a committed file, and make a new link once the
   marking is over. The supervisor must hold MIMIC-IV credentialed access.

## Starting the application

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
