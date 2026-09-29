# 0019. The API and the interface (Phase 8)

Status: accepted (2026-09-29).

## Context

Phase 8 puts the application in front of a person (work items 47 to 52):
an API for case and scope selection, questions, explanations and evidence
inspection, an access boundary, and an interface with a case and scope
selector, a question panel, an explanation panel and an evidence viewer.
The requirements are the brief's: case selection, scoped questions,
explanations and inspection of status and evidence (RF-08); patient data
limited to authorised users, with authentication and encrypted transport
when it is reached over a network, and no patient content or secret in a
routine log (RN-02); readable evidence, the active case, scope and
status, and facts, predictions and unsigned attribution kept apart
(RN-07); and the rules of identity and history, such as never changing
the case or the scope by a conversational reference and clearing earlier
responses when the case changes. The model writes on the development
laptop's GPU, and the project's supervisor, who marks it, must be able
to open the application through a link. The project owner decided each
point on 2026-09-29.

## Decision

1. **Two local processes.** The API service owns the evidence, the model
   worker and the runtime records; the Streamlit interface is a separate
   process that reaches it only over HTTP. One launcher starts both. The
   model loads in the background; until it has loaded, or when it cannot
   load, the templates write every response as a recorded fallback
   (`no_model`), and the model writes one request at a time, a second one
   meanwhile getting the template's response at once (`busy`).
2. **The access boundary.** Both processes listen on 127.0.0.1 only. Every
   API operation but the health check needs a random token made at each
   launch, which the launcher hands to the interface in its process
   environment; a request must name the loopback host, cross-origin
   requests are refused, no response is cached, and Streamlit's usage
   statistics are switched off. To share the interface, a Cloudflare
   named tunnel gives it an address on the owner's domain; the API is
   never reachable through it. Access checks inside the interface's
   process then admit a browser only with a secret link's key, a long
   random value kept only as its digest, or the session cookie set on the
   first visit (HttpOnly, Secure, SameSite=Lax, since the link is opened
   from another site), and answer anything else as not found. The secret
   link is the authentication and Cloudflare's HTTPS the encrypted
   transport. The link is shared with the supervisor through an issue in
   the private repository, never in a committed file; the supervisor
   holds MIMIC-IV credentialed access; the tunnel runs only while the
   application is served, and a new key retires the link.
3. **Development case sets only.** The API serves `dev-30-v1` and
   `pilot-10-v1` and refuses `eval-100-v1`, so that its cases are not seen
   before the final evaluation.
4. **Each question on its own.** A question is answered for the selected
   case and scope only; nothing carries over from an earlier one. A case
   change clears every earlier response; a scope change keeps them,
   greyed and labelled with the scope they answered, never as current.
   Each response shows its outcome, text, scope, references, who wrote it
   and how long it took.
5. **The prediction, the drivers and the diagnoses apart.** The
   explanation panel shows the prepared prediction with its score's fixed
   meaning ("uncalibrated sigmoid of the logit; not a clinical risk"),
   never as a percentage; the model's explanation of the selected drivers
   with each attribution score labelled unsigned and not causal; and the
   case's recorded diagnoses in a block of their own.
6. **The canonical evidence without native identifiers.** The evidence
   viewer shows a scope's visits, records, coverage and provenance, and
   the prediction's every original attribution with its link status, so
   that missing linkage is visible; the native database identifiers of
   visits and records, and the model's structured view, are left out.
7. **Records, logs and tests.** Every question and explanation through
   the API writes its protected runtime record; the API's routine log
   holds each request's route, status and duration only, and the log
   audit reads it; the API is tested over a real loopback connection with
   the locked `requests` library, and the interface with Streamlit's test
   harness, on invented cases only. Phase 8 closes with an operator run on
   `dev-30-v1` with the model and release 0.10.0.

## Consequences

- Nothing in the evidence, the generation or the release gate changes:
  the API returns the gate's response exactly, and nothing it withheld.
- Patient data leaves the laptop only through the tunnel, over HTTPS, to
  a browser holding the secret link or its cookie. Cloudflare decrypts
  the traffic at its edge to forward it; every response is marked not to
  be cached.
- Anyone holding the link could open the application while it is
  served: it is kept to the private repository's issue, and a new key
  retires it after marking.
- The laptop must be on, awake and online while the link is in use.
