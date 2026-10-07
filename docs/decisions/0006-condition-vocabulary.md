# 0006. Condition Vocabulary From the Athena Release

Status: accepted (2026-09-26).

## Context

Phase 5 answers questions such as "Is heart failure recorded?". The typed
term has to be resolved to OMOP condition concepts, and a record of
congestive heart failure should count as heart failure. That needs the
standard condition concepts with their English synonyms, the SNOMED
ancestor hierarchy and the ICD-to-standard mappings: the whole vocabulary,
not only the concepts that appear in a snapshot.

The Athena release loaded into the OMOP database is on the cold-archive
disk. [ADR 0001](0001-single-volume-storage.md) keeps it there, and the
fixed storage policy allowed reads only under the policy anchor and from
the legacy export directory. A check against all three source snapshots
showed that it is the same release as the database: 5,456 of their 5,459
concepts are identical. The other 3 are names with non-ASCII characters
that the database copy holds double-encoded, so the Athena text is also the
cleaner source.

## Decision

1. The storage policy gains one more read-only source exception,
   `VOCABULARY_SOURCE_DIR` (the Athena directory on the archive disk),
   checked by `vocabulary_source_problem`. It admits plain files directly
   in that directory, reached without links; nothing is ever written there.
   The legacy-export stager does not accept it.
2. `qa.vocabulary` builds a versioned condition vocabulary from four pinned
   Athena files (concepts, synonyms, ancestors, relationships). Each file
   is read once, and its SHA-256 is checked over exactly the bytes parsed.
   The build publishes standard condition concepts, ICD-9-CM and ICD-10-CM
   concepts, English synonyms, ancestor pairs between standard conditions
   and ICD-to-standard mappings. It holds no patient data.
3. The vocabulary is licensed content (SNOMED CT through Athena), so it
   lives in the private workspace under
   `protected-data\vocabularies\condition-vocabulary-athena-20260831-v1`.
   Git holds the code and the pinned digest of its manifest only.
4. Term resolution (`qa.resolve`) follows the approved Phase 5 rules. Terms
   are resolved by exact name, synonym or ICD code. Records match exactly,
   as more specific concepts or through ICD codes. Uncoded records match on
   their ICD description. Ambiguous and too-broad terms are never guessed,
   and unknown terms come with suggestions.

## Consequences

- Questions resolve without a database login, from a pinned vocabulary.
- Every match states how it was made, so an answer can be checked against
  the hierarchy and the descriptions it used.
- A new Athena release is a new vocabulary version; answers record the
  vocabulary digest they used.
