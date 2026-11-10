# AI Format Verification & Review Notes

This document records the verification checklist for `FORMAT=WEIBO_AI_2` (`weibo_archive/ai_format.py`) and its post-export variant generator (`weibo_archive/exporter.py`).

## Invariants Verified by Automated Tests

1. **Lossless Repost Chain Splitting**
   - Splitting top-level repost commentary at `//@` preserves all non-default text; re-joining with `//@` matches the normalized input.
   - Platform-default repost placeholders (`转发微博`, `Repost`, `轉發微博`) map to `NO_COMMENT` only on repost records; identical text in an original post remains ordinary authored text.

2. **Line-Boundary Escaping & Structural Disambiguation**
   - Unicode line separators (`\r`, `\n`, `\u0085`, `\u2028`, `\u2029`) are normalized before prefixing `RT`, `CHAIN`, or body lines, preventing embedded newlines from spoofing structural headers (`W<n>`, `RT`, `END`, `REF=`).

3. **Self-Contained References & Part Splits**
   - `REF=W<n>.OWN` and `REF=W<n>.RT` never point forward to later records and never cross multi-part file boundaries.
   - Shortened bodies (`CUT=kept/original`) are never reused to satisfy a later full `SELF` body reference.

4. **Deterministic Instant Ordering**
   - Records with known UTC offsets sort by exact instant; offset-free records borrow the archive's most common offset (with lexical tie-breaking) for ordering while retaining `TZ?` in displayed output.

5. **Header Aggregates Match Included Records**
   - Yearly/monthly counts, `OWN_TEXT` character totals, reposted-author tallies (UID-grouped vs. unverified name-only), and check-in/IP location summaries reflect only the records included in that specific output file or part.
