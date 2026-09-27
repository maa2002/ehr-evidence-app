# 0015. The generation configuration (Phase 7)

Status: proposed (2026-09-27): decisions 1 and 2 are in force; the limits
are set, and K and the limits frozen, by the rules of decisions 3 and 4
once the rerun they name is measured. Amends
[ADR 0014](0014-structured-evidence.md), decision 3: the worked examples.

## Context

The comparison fixed in advance by ADR 0014 (decision 9) wrote all 430
requests of `dev-30-v1` in BF16 with the mixed GPU/CPU placement and in
4-bit NF4 at `20e02a0` (`projection-v4`, `prompt-v7`), under the
configured limits of 16,384 prompt tokens, 768 output tokens and 120
seconds. BF16 released 314 as the model wrote them (73.0%) and NF4 172
(40.0%): NF4 minus BF16 -33.0 percentage points, 95% interval -37.4 to
-28.4 (Newcombe's method 10), exact McNemar p below 0.001. BF16 released
144 requests that NF4 did not, and NF4 2 that BF16 did not.

The run showed two more things.

- **Every "not recorded" disclosed unmapped records.** In both
  precisions the same 55 of the 135 presence checks answered "not
  recorded" were withheld, and for their reason codes and their
  disclosure of records without a standard concept alone. 80 of the 135
  have such records within their scope and 55 have none; both precisions
  released exactly the 80, and every "not recorded" either wrote was 86
  to 88 tokens long, the length of the reply with the disclosure (the
  shortest correct one is 46). The model disclosed unmapped records
  whether the scope had any or not, as every worked example it was shown
  did. The earlier reading of the 33 sampled requests, that such replies
  left their disclosure out, had the direction wrong.
- **Fifteen BF16 requests fell back at a limit.** 10 lists whose correct
  document alone exceeds 768 tokens were never written, 4 lists were cut
  off at 768 tokens and 1 explanation ran past 120 seconds. Measured again
  at this commit with the model's tokenizer, the correct documents of
  `dev-30-v1` need up to 1,950 output tokens for lists (90th percentile
  652), 154 for presence checks and 580 for explanations with K = 5; those
  of `pilot-10-v1` up to 1,124, 108 and 637. The largest prompt is 4,695
  tokens. BF16 wrote lists at 8.2 to 8.8 tokens a second and explanations
  at 5.4 to 8.7, so an explanation reaches 768 tokens in 142 seconds at
  the slowest measured rate: the time limit, not the output limit, ends
  it.

## Decision

1. **BF16 with the mixed placement.** NF4 is not non-inferior at the
   margin of ADR 0014, so the final evaluation runs BF16 with the mixed
   GPU/CPU placement, as configured.
2. **Worked examples that match the request (`prompt-v8`).** A list or
   presence example discloses a record without a standard concept exactly
   when the request's own view has one (`records_without_standard_concept`
   above zero), as an explanation's example leaves drivers out exactly
   when the request does (ADR 0013, decision 6). The examples are
   otherwise the same invented requests, and a presence check is still
   shown both its answer and its "not recorded". The prompt of every
   request whose view has such records is unchanged byte for byte (240 of
   240 in `dev-30-v1`, 93 of 93 in `pilot-10-v1`); the other list and
   presence prompts (164 and 79) show examples without a disclosure. The
   view states the number either way and the model still decides whether
   to disclose; this replaces the rule of ADR 0014, decision 3, that
   every list and presence example includes a disclosure.
3. **Limits by a rule fixed before the rerun.** BF16 writes every request
   of `dev-30-v1` again with `prompt-v8` under 2,048 output tokens and 300
   seconds, and the same outputs are counted under 768 tokens and 180
   seconds (`--within`; greedy decoding writes the same tokens under any
   output limit they fit). The time limit becomes 180 seconds, so that
   output within 768 tokens is not ended by time at the slowest measured
   rate. The output limit rises to 2,048 tokens, which holds every
   development list's correct document with 5% to spare, and the time
   limit to 300 seconds (2,048 tokens at 8.2 a second take 250), only if
   that releases at least 2 percentage points more of `dev-30-v1` than 768
   tokens and 180 seconds do; otherwise a reply longer than 768 tokens,
   the longest tenth of lists, is not worth up to four minutes of waiting
   when its template answers at once, and it falls back as
   `limit_exceeded`, a failure of the model. The prompt limit stays at
   16,384 tokens, more than three times the largest development prompt.
4. **K = 5, frozen with the limits.** An explanation describes at most 5
   drivers. Every development explanation's correct document fits the
   output limit at K = 5 (largest 637 tokens); in the small sweep of
   candidate values fewer drivers were released more often; and more
   drivers would lengthen the explanations, already the slowest replies.
5. **Diagnostics that name their direction.** A benchmark names a field,
   notice or reason code as added or left out, and splits each list and
   presence stratum by whether its scope has records without a standard
   concept, so that a failure of this kind is read the right way round.

## Consequences

- The precision is settled for Phase 9. NF4 was no faster on the whole
  set for lists (median 33.0 seconds against 32.9) and slower for
  presence checks (12.7 against 11.3), faster for explanations only (50.9
  against 64.8), and used 3.6 to 3.8 GiB of GPU memory against 6.3 to 6.6.
- The rerun's results, the limits the rule gives and the frozen
  configuration are recorded here when it is measured; until then the
  configured limits and the configuration of K are unchanged.
