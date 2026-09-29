# API and interface

Phase 8 puts the application in front of a person
([ADR 0019](decisions/0019-api-and-interface.md)). A local API serves the
development case sets: it answers questions about a selected case and
scope, explains a case's prepared prediction and shows the evidence
behind both. Nothing in the evidence, the generation or the release gate
changes: the API returns the gate's response exactly, and nothing the gate
withheld. Command examples assume the session set-up in
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

The evidence of a scope holds its visits (reference, number, role and
dates), its records (reference, visit number, domain, concept, source
code and dates), its coverage, with the references of the records without
a standard concept, and its provenance, the digest of every input. The
evidence of the prediction holds the prediction with its score's fixed
meaning, every original attribution with its score, attachment and link
status, the linked history records, the final admission's records as
context, the case's restrictions and the provenance. The native database
identifiers of visits and records are left out: references and visit
numbers trace every claim.

A request the service cannot serve is answered with a bare code:
`case_set_not_served`, `case_unknown`, `scope_unknown`, `scope_invalid`,
`prediction_missing`, or `request_invalid` with the names of the fields
at fault, never their values.

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
- a body is read whole and bounded (16 KiB) before the API sees it;
- every response, a refusal included, is marked `Cache-Control: no-store`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and
  `X-Frame-Options: DENY`;
- an exception inside the API is answered with a bare code and logged by
  its type name only.

The access log, `outputs\logs\api\api-<date>.log` in the workspace, holds
each request's method, route template (such as
`/case-sets/{case_set}/cases/{case_number}`), status and milliseconds,
never a value from the path or the query, a question or a response. The
log audit reads it with the other routine logs
(`generation.records --operator-real --audit-logs`).

## Commands

The default mode serves the invented case set on a free loopback port,
with a scripted model, and checks every route and the boundary over a
real connection; nothing is written:

```powershell
& $AppPython -m ehr_evidence_app.api.app
```

`--serve` serves the development case sets with the model on
127.0.0.1, taking the launch token from `EHR_API_TOKEN`; the launcher
sets it:

```powershell
& $AppPython -m ehr_evidence_app.api.app --serve --port 8601
```
