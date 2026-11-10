# AI Export Format Optimization Notes (`WEIBO_AI_2`)

This document summarizes the design rationale behind `FORMAT=WEIBO_AI_2` (`weibo_archive/ai_format.py`), which replaced `WEIBO_AI_1` in v0.7.0.

## Key Problems Solved

1. **Authorship & Repost Chain Separation**
   - In Weibo timelines, repost comments frequently chain multiple intermediate users via `//@username:comment`.
   - `WEIBO_AI_2` splits top-level repost text at the first `//@`: text before it is emitted as the account's own comment (`COMMENT:` or `NO_COMMENT` for platform default reposts), intermediate segments are emitted as `CHAIN:` lines with unverified name labels, and the reposted original post is emitted in a dedicated `RT` block.
   - Joining the comment and chain segments with `//@` losslessly reconstructs the normalized top-level text.

2. **Token Efficiency & Deduplication**
   - Repeated file-wide constants are moved to the document header (`PROFILE`, `SUMMARY`, `READING_GUIDE`).
   - Device, IP location, and engagement counts of other accounts' reposted posts are omitted from AI output.
   - Repeated source bodies or self-quoted posts are deduplicated via `REF=W<n>.RT` and `REF=W<n>.OWN` while keeping each occurrence's timestamp, media count, and completeness state.

3. **Chronological Ordering & Citation Stability**
   - Records are ordered oldest-first by UTC instant (using the archive's modal known UTC offset as a sorting fallback for offset-free timestamps while preserving `TZ?` in output).
   - Records are numbered densely per file (`W1..Wn`) with year headers and a closing `END` marker carrying the total record count so truncated contexts are immediately detectable.

4. **Post-Export Budget-Limited & Multi-Part Copies**
   - From the completion dialog, users can generate smaller or split AI copies directly from the in-memory `Archive` snapshot without another network fetch.
   - Non-`SELF` complete source bodies can be capped (`CUT=kept/original`) or omitted (`OMITTED`), while account-authored text, `//@` chains, UID-verified `SELF` repost bodies, and `PREVIEW_ONLY` snippets remain intact.
   - Multi-part outputs split along month boundaries (or record boundaries for oversized months) with self-contained headers, local `REF` targets, and per-part `END` lines.
