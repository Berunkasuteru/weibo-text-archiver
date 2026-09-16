# Weibo Image Archiver — read-only feasibility spike

Research date: 2026-09-16. Baseline: main, f45919b, tag v0.5.7. Initial working tree was clean. This document records observations and conditional design proposals, not an implementation specification approved for execution. No private response bodies, identities, ordinary post text, credentials, or full resource URLs are retained here.

## A. Verdict

DEFER

An independent image subsystem is architecturally feasible, and timeline responses directly expose useful image locators. However, this probe did not establish successful image retrieval: four selected Live Photo still-image URLs returned 403 to cookie-free, referer-free range GETs; one HEAD returned 403 too. Ordinary-photo and GIF resource retrieval were not tested. This does not establish a general CDN restriction or a requirement for cookies. It leaves a critical feasibility criterion unresolved.

Separately, nine observed nodes declared more than nine images while every returned pics list had at most nine entries. A future narrowly scoped product could save returned image assets, but cannot claim complete historical image preservation on this evidence. These limitations do not justify a NO-GO: no evidence establishes that browser automation, URL rewriting, or an invasive text-model change is required.

## B. Evidence source

- Static repository: inspected models.py, parser.py, client.py, network.py, credentials.py, exporter.py, markdown_v5.py, storage.py, app.py; relevant contracts in tests/run_tests.py and timeline fixture; docs/ARCHITECTURE.md, docs/DECISIONS.md, docs/SOURCE_NOTES.md, README.md. Fixture evidence is synthetic, not proof of live download behavior.
- Live probe: one run, owner's existing protected login, target selected from the most recently modified existing normalized archive. Four cached targets existed; no account names or identifiers were printed. Selection does not assert that the cached target owns the login.
- Pages inspected: 3; no extension or repeated timeline requests.
- Posts structurally inspected: 298 top-level occurrences, plus 90 nested RT occurrences. These are not asserted to be unique posts. Per-page top-level counts: 101, 98, 99. Requested count=100 is not a hard response-size bound.
- Image-resource probes: 5 requests across 4 distinct returned URLs: four GETs requesting bytes 0–65535, and one HEAD of the first URL. All selected URLs happened to be Live Photo still components. Selection prioritized Live Photo before GIF, which limits representativeness.
- Live requests added beyond normal timeline: one existing production preheat request and five image-resource requests. Total: 9 requests. No profile, long-text, detail, new API endpoint, or video request.
- Credential access used the existing store's read-only protected-read method, avoiding load_cookie_header(), whose normal behavior may migrate/delete legacy credentials. Before/after credential-file hashes matched; legacy credential remained absent. Hash values were not reported.
- API used production client UA, verified TLS, preheat and cancellation-aware page pacing. Retry budget was conservatively reduced to one attempt. CDN probes used a separate verified-TLS urllib opener, no cookie jar, no Cookie or Referer, 0.5-second cancellation-aware waits, 15-second timeout, maximum 64 KiB body read, and restricted redirects. No retries or bypass experiments.
- Raw responses and resource bytes existed only in process memory. No raw-response or image file was written. Temporary script lived outside the repository and was removed after the study.
- No regression suite was run: production and tests were unchanged. Verification consisted of static inspection, the bounded probe, credential integrity check, and final Git status.

## C. Observed raw image model

W means a card's mblog, including card_group traversal; RT means W.retweeted_status. Paths below are relative to that node. Unless stated otherwise, live confidence is high for existence/type in this sample only. Counts are occurrences, not globally unique assets. URLs and pids are not preserved by Post/Archive. The text parser deliberately retains media counts, not locators.

| context | field | type | meaning / current text use | example shape with private values redacted | confidence |
|---|---|---|---|---|---|
| W + RT | pics | list of dict | Ordered media slots; parser counts images/videos. Not all entries are still images. | [{pid: "<asset>", large: {...}}] | Live + fixture |
| W + RT | pics[].pid | str | Candidate asset identity, not a URL; ignored by text parser. Long-term stability unproven. | "<asset>" | Live + fixture |
| W + RT | pics[].url | str URL | Returned display image, observed /orj360/; ignored by text parser. | https://wxN.sinaimg.cn/orj360/<asset>.jpg | Live |
| W + RT | pics[].size | str | API size descriptor; interpretation not established, not necessarily bytes. Ignored. | "<descriptor>" | Live type only |
| W + RT | pics[].geo | dict | Display geometry container; ignored. | {width: <number>, height: <number>, croped: <bool>} | Live |
| W + RT | pics[].geo.width, .height | int or str | Dimension facts with mixed wire types; normalize conservatively. Ignored. | <integer-or-string> | Live |
| W + RT | pics[].geo.croped | bool | Source crop flag, spelling as returned; ignored. | <bool> | Live |
| W + RT | pics[].large | dict | Explicit larger-image candidate, not proof of upload-original bytes. Ignored. | {url: "<URL>", geo: {...}, size: "<descriptor>"} | Live |
| W + RT | pics[].large.url | str URL | /mw2000/ and some RT /large/ paths. Ignored by text parser. | https://wxN.sinaimg.cn/mw2000/<asset>.jpg | Live |
| W + RT | pics[].large.size | str | Size descriptor of unverified semantics; ignored. | "<descriptor>" | Live type only |
| W + RT | pics[].large.geo.width, .height | int or str | Larger-candidate dimension metadata; ignored. | <integer-or-string> | Live |
| W + RT | pics[].large.geo.croped | bool | Source crop flag; ignored. | <bool> | Live |
| W + RT | pics[].type | optional str | Absent on 935 slots; gifvideos on 16, video on 5, livephoto on 6. Text parser explicitly recognizes video and livephoto; gifvideos is counted as image by default. | "livephoto" | Live; fixture also uses "pic" |
| W + RT | pics[].videoSrc | str URL | Companion video locator; text parser tests presence only for livephoto. Not saved. | <video URL; query values omitted> | Live |
| RT | pics[].duration | int | Seen on 5 video slots, not a still-image dimension. Ignored. | <integer> | Live |
| W + RT | pic_num | int | Declared count; text parser increases image count when larger than counted images and no video counted. Not an enumeration of locators. | <integer> | Live + fixture |
| W + RT | pic_ids | list of str | Candidate identities, no URLs. Ignored by current text parser. | ["<asset>", "<asset>"] | Live |
| W + RT | thumbnail_pic | str URL | One node-level thumbnail URL, not one URL per image. Ignored. | https://wxN.sinaimg.cn/thumbnail/<asset>.jpg | Live |
| W + RT | bmiddle_pic | str URL | One node-level medium URL; HTTP in observed sample. Ignored. | http://wxN.sinaimg.cn/bmiddle/<asset>.jpg | Live |
| W + RT | original_pic | str URL | One node-level explicitly supplied /large/ URL; not a demonstrated original for every slot. Ignored. | https://wxN.sinaimg.cn/large/<asset>.jpg | Live |
| W + RT | page_info | dict | Linked card/video/article metadata; not necessarily ordinary attached images. Parser observes type/object_type. | {type: "<kind>", page_pic: {...}} | Live + code |
| W + RT | page_info.type / object_type | str / int | Card classification; parser stringifies both, checks video/article. | {type: "video", object_type: <integer>} | Live |
| W + RT | page_info.page_pic.url | str URL | Card cover, excluded from v1 attached-image backup. Ignored by text parser. | https://<observed-host>/<redacted>.jpg | Live |
| W + RT | page_info.page_pic.width, .height | str | Optional card-cover dimensions; ignored. | "<dimension>" | Live |
| RT | page_info.page_pic.pid | str | Optional cover identity; not an extra attached image. Ignored. | "<asset>" | Live |
| RT | page_info.page_pic.source, .is_self_cover, .type | str | Cover descriptors of unverified semantics; ignored. | "<descriptor>" | Live type only |
| RT | page_info.icon | str URL | UI icon, not post image. Ignored. | https://h5.sinaimg.cn/<redacted>.png | Live |
| RT | page_info.author.profile_image_url | str URL | Linked-content avatar, excluded; query keys Expires/KID/ssig. Ignored. | <avatar URL; values omitted> | Live |
| RT | page_info.media_info.stream_url, .stream_url_hd | str URL | Normal-video stream; excluded. Not consumed by text media parser. | <video URL; values omitted> | Live |
| RT | page_info.urls.mp4_720p_mp4, .mp4_hd_mp4, .mp4_ld_mp4 | str URL | Video variants, excluded. | <video URL; values omitted> | Live |
| W + RT | isLongText | bool | Text hydration flag; image lists can coexist. | true | Live + code |
| W + RT | edit_count / edit_at | int / str | Edit indicators; do not prove the edit changed an image. Text parser observes edit_count. | <count> / "<time>" | Live + code |

Other observed page_info keys included page_url, url_ori, page_title, content1, content2, object_id, title, video_orientation, play_count, media_info.duration and author.screen_name. They describe linked content/navigation/video or private text, not additional ordinary-image locators; values were neither reported nor retained. The field inventory covers media-bearing structures examined, not every image-like URL elsewhere in a user profile or HTML body. HTML img elements may be UI icons/location/emoticons and must not be promoted to ordinary photo attachments.

Raw-list totals: W 736 slots; RT 226 slots; total 962. Each slot had pid, url and large.url. Five slots were explicitly video, leaving 957 prospective image slots by observed type, not 957 verified downloadable or unique files. pic_ids contained 736 W entries and 226 RT entries in aggregate. The probe did not compare pid sets per node; equal aggregate lengths are not proof of per-node completeness.

Boundary findings from source:

- client.extract_mblogs passes full raw dictionaries onward; client does not strip image URLs before parsing.
- _merge_long_text shallow-copies a node and replaces only text. _hydrate_long_texts applies this independently to W/RT. Extra image data in a long-text/detail response is not merged into the node; existing timeline images survive.
- parse_post independently calculates media for W and one RT layer. MediaInfo stores images/videos/article only; Post has no image locator. Exporter synthesizes image:ordinal placeholders for the legacy renderer; these are not raw URLs.
- storage schema 4 serializes normalized text semantics only. The existing cache cannot be used as an image URL source.

## D. Coverage

| category | result | evidence / limit |
|---|---|---|
| single image | OBSERVED | W 75 single-slot lists, RT 33; slot counts alone can include video. Ordinary image entries exist, but original-versus-retweet W categories were not cross-tabulated. |
| multi image | OBSERVED | W 130 multi-slot lists, RT 31. |
| nested RT | OBSERVED | 90 nodes, 64 with pics lists. Attribution is structurally independent. |
| no image | OBSERVED | 93 W and 26 RT without nonempty pics; this is not proof of no linked card cover/video. |
| GIF | OBSERVED | 16 .gif locators with type=gifvideos and separate videoSrc. GIF bytes/animation were not verified. |
| Live Photo | OBSERVED | 6 RT slots with type=livephoto, still URL and videoSrc. Still resource requests returned 403. |
| >9 images | OBSERVED | 3 W and 6 RT nodes declared >9; maximum len(pics)=9 for both contexts. Full enumeration not observed. |
| long-text image | OBSERVED | 11 W and 24 RT nodes with isLongText and nonempty pics; no long-text request. |
| edited image post | OBSERVED | 4 W and 23 RT image-bearing nodes had edit indicators. Image revision history remains INCONCLUSIVE. |
| tombstone | OBSERVED | 8 RT nodes recognized by the existing conservative parser. No resurrection request. |

JPEG-like URLs were observed. PNG URLs were observed for card icons/covers only, outside v1 attachment scope. Downloaded JPEG/PNG/GIF/WEBP/HEIC/HEIF bytes: NOT OBSERVED. WEBP and HEIC/HEIF attachment shapes: NOT OBSERVED.

## E. Image quality

- Best directly supplied per-slot candidate: pics[].large.url, generally /mw2000/, sometimes /large/. This is the API's larger candidate, not experimentally established highest quality.
- Original-quality evidence: node-level original_pic explicitly exists and uses /large/. It is distinct in field/path from the commonly returned per-slot /mw2000/ candidate. No byte/dimension comparison proved superiority or equivalence to the uploaded original. No per-slot original field was observed.
- Any URL rewriting required: none to acquire the returned candidates. There is no evidence authorizing rewriting a pid, thumbnail, mw2000 or large URL to manufacture an original URL.
- Recommendation: choose directly returned per-slot large.url, falling back to its directly returned url with a quality label. Do not assume a node-level original_pic belongs to every slot or count it as an additional image. It could only supplement a slot after a trustworthy association is established. Describe quality as API-supplied, not lossless/original.

## F. Download behavior

| property | result |
|---|---|
| Ordinary attachment hosts | wx1.sinaimg.cn, wx2.sinaimg.cn, wx3.sinaimg.cn, wx4.sinaimg.cn |
| Actually probed hosts | wx1.sinaimg.cn, wx2.sinaimg.cn, wx4.sinaimg.cn |
| Other image-like hosts, not probed | ww1.sinaimg.cn, ww2.sinaimg.cn, h5.sinaimg.cn, p6.moimg.net, tvax2.sinaimg.cn for covers/icons/avatar |
| HTTPS | Returned per-slot url and large.url used HTTPS. Node bmiddle_pic used HTTP. No HTTP resource requested. |
| GET | Four range GETs of Live Photo stills returned HTTP 403; no successful or full GET established. |
| HEAD | One 403; cannot establish successful HEAD support or HEAD/GET equivalence. |
| Redirects | Zero redirects in all five resource probes. Redirect behavior on success remains unknown. |
| Cookie required | INCONCLUSIVE. No Cookie sent; no authenticated CDN request attempted. |
| Referer required | INCONCLUSIVE. No Referer sent; a static first-party Referer comparison was not performed. |
| Works without Weibo session | Not demonstrated for the selected sample; failures do not establish a universal requirement. |
| Content-Type / Content-Length / dimensions | Not captured for 403 error responses; no successful image response to compare. Geometry field types were inspected, not value distributions. |
| Range | bytes=0-65535 sent; 403 cannot prove honored or ignored. |
| Signed/temporary URLs | No query keys on observed pics URL/large URL or node-level image URLs. Does not establish permanence. Companion videos had Expires/KID/ssig and other keys; avatar query signing was also observed. Neither was fetched. |

The probe did not read/save error bodies, diagnose the 403 cause, or test browser/proxy/captcha workarounds. Do not generalize these four Live Photo stills to ordinary photos or GIF availability. The sample-selection limitation is material to DEFER.

## G. Completeness boundary

Cannot promise “保存全部图片” or “完整保存所有历史图片”. A defensible future promise, contingent on successful download evidence, is “保存本次接口返回且可下载的图片，并明确报告缺失与失败”.

Ten nodes had pic_num > len(pics): 3 W, 7 RT. Nine were definitely declared >9 while list lengths never exceeded 9. Mixed video lists mean not every mismatch has the same interpretation; never silently equate all differences with missing still files. Record declared count, returned slot count, unsupported slot count and an enumeration warning separately.

pic_ids is an identity list without locators; no demonstrated extra-URL field closes these gaps. Its aggregate length matched pics, but per-node matching was not checked. Node-level thumbnail/bmiddle/original fields do not enumerate missing slots. Existing long-text hydration does not supplement images. Complete enumeration would need additional endpoint evidence or another proven field; no particular new endpoint is justified by this study. No request was made merely to find more categories.

Define completion of downloads of returned assets separately from completeness of image enumeration. Missing identities, positive counts without locators, count inconsistencies, unknown slot types or malformed structure require explicit warnings and prevent an unqualified COMPLETE result. A known tombstone is an unavailable source, not a fabricated zero-image success. No historical total can be inferred from the currently accessible timeline.

## H. Proposed separate architecture

Shared components: saved login/DPAPI, verified TLS and UA policy, cancellation primitives, target validation, raw card extraction and absolute-time parsing where stable. Share policy and small primitives; do not share an authenticated HTTP instance with CDN downloads.

New image-only components: image models, parser, timeline orchestrator, streaming resource client/downloader, manifest, separate dialog/worker. Keep existing files in place.

Explicitly untouched text components: Post, Archive, MediaInfo, text parser behavior, hydration, normalized text cache, Full Markdown, WEIBO_AI_1 and Custom export semantics. No Post.image_urls, Archive.download_status, media fields in text cache or WEIBO_AI_2.

Current WeiboClient.fetch is not a reusable raw stream: traversal is coupled to Post construction, text hydration, sorting and Archive creation. A dedicated small image traversal must preserve applicable pinned-post, duplicate-ID, foreign-account, stalled-page and since-date guards. This creates maintenance cost. _timeline_page is already raw-returning, but private methods are not a stable shared API. A later explicitly approved narrow extraction could reduce duplication; do not begin with a generalized framework or moving the text package.

Range concepts can be reused without Archive semantics: Test 20 and Recent N count selected top-level posts, not images or RT nodes; Since date uses absolute provenance and two wholly old normal pages as stopping evidence; unknown/relative dates cannot prove the boundary. Full snapshot means currently returned accessible timeline until proven natural end. Preserve the existing distinction between pinned candidates and normal timeline frontier; reproduce final selection rules explicitly instead of calling fetch() for convenience.

Separate “图片备份…” dialog owns target, range, output location, progress, cancellation and summary. Use an independent generation/task state. Initially serialize text and image network tasks at application level to avoid two simultaneous crawls. No image checkbox inside text export and no shared success flag.

## I. Proposed data model

All fields here are proposals with a specific preservation need, not claims that upstream supplies them.

ImageRecord:

- source_post_id (id, or validated mid fallback); containing_post_id for the W occurrence; relation TOP_LEVEL or RETWEET_SOURCE.
- created_at plus timestamp provenance for the source node.
- declared_image_count (nullable), returned_slot_count, enumeration state/reason; assets.

ImageAsset:

- Original 1-based raw slot index, optional observed pid, subtype (ordinary/GIF/Live Photo still), selected field/quality.
- Returned locator in memory only; no guessed URL.

Manifest:

- Independent schema version, local target identity, requested range, run timestamps, traversal termination/completion and image result label.
- Record/asset identity mapping, enumeration warnings, unavailable-source count, per-asset status, safe reason/status code, local relative filename, observed Content-Type, byte size, SHA256.
- Asset result statuses saved, already_present, unavailable, failed; unfinished assets are represented with no terminal result yet, not fabricated failure/success. This is necessary to audit cancellation/crash recovery.

Use source identity plus ordinal for traceability, with relation/containing-post context where needed. pid is useful corroborating evidence for edits, not a demonstrated globally stable key. Exact URLs may change and may contain secrets; do not make them identity. Content hash verifies bytes after download; do not globally deduplicate different posts or RT occurrences. Never merge source ownership merely because names/URLs/bytes match.

already_present requires a matching manifest identity (including pid when available) and a verified existing file, preferably SHA256 and size. File existence alone is insufficient. An ordinal whose pid changed, unknown revision identity, or a conflicting file must not silently overwrite/skip. Preserve a separate revision/collision suffix and report the conflict; same pid does not prove bytes can never change. Resume without saved URLs requires re-enumerating the live range; page-number-only resume is unsafe. Previously saved files remain useful if the source later disappears.

## J. Output layout

```text
Archives/
└─ <local-account-key>_图片备份/
   ├─ manifest.json
   ├─ 2026/
   │  ├─ <W-post-id>_01.jpg
   │  └─ <W-post-id>_RT_<source-post-id>_01.gif
   ├─ 2025/
   │  └─ <post-id>_01.jpg
   └─ unknown-date/
      └─ <post-id>_01.jpg
```

Use the source node's year only for source_offset/source_wall absolute facts, without invented timezone conversion. Relative_unverified/unknown dates go to unknown-date. RT uses its source date, not W date. Later date clarification should honor a previously validated manifest path rather than accidentally duplicate a file.

Prefer validated numeric id, with validated numeric mid only when available. All 388 observed nodes had an identity; mid fallback was not separately exercised. A bid fallback requires explicit namespacing/validation and additional evidence; missing identity is a discovery error, not a made-up stable post. Do not use author/display name or post text in filenames. Use fixed safe components and final signature-derived suffix. Existing unmatched paths require non-overwriting collision allocation; preserve originals. Retain the allocated path in the manifest.

## K. Failure / cancellation semantics

- COMPLETE: selected traversal ended with valid range/natural-end evidence, all discovered in-scope assets saved or verified already_present, no unresolved enumeration/source warnings or pending entries, and manifest durably finalized. Even this label is scoped to this response/range, not all historical images.
- PARTIAL: useful files or meaningful inspected records were retained, but an unavailable source/asset, failed asset, missing enumeration evidence, pending work or interrupted traversal prevents COMPLETE. Include the stopping reason.
- CANCELLED: user requested cancellation; takes precedence over other terminal labels. Preserve counts and finalized files. Unprocessed entries remain unfinished.
- FAILED: fatal setup/discovery/storage problem prevents a trustworthy useful checkpoint, or every attempted asset fails without usable preserved output. A global stop after useful verified output yields PARTIAL with fatal-stop reason. An honestly completed empty image range can be COMPLETE with zero images if no gaps exist.

Per-asset saved means final file committed; already_present means existing file verified. unavailable requires explicit resource/source evidence such as 404/410, not every 403. A 403 is failed/access-denied of unknown cause; repeated denial, authentication/challenge, 429 or unexpected structures stop conservatively. Tombstone counts as unavailable source without inventing an image count. Summary includes posts inspected, image assets discovered, saved, already_present, unavailable, failed, unfinished and enumeration/source warnings.

Atomic file behavior: stream one asset into a unique same-directory temp, check cancellation between chunks, enforce size/time/type limits, flush/fsync, then finalize atomically with a conflict-safe destination policy. Delete the active temp on failure/cancellation; preserve completed files. Never advertise success before the final file exists. Check directory ownership/symlink/reparse issues before writing. Single writer per output root avoids concurrent collision races.

Manifest behavior: atomically rewrite after each asset and after discovery/run-state checkpoints, using a separate same-directory temp, flush/fsync and atomic replacement. This is simpler to recover than task-end-only checkpointing. It may become costly on large archives; accept that cost in v1 or reconsider based on measurement. On Windows do not claim perfect power-loss durability beyond supported filesystem semantics.

A crash can occur between image finalization and manifest replacement. An unreferenced final file is not automatically already_present; reconcile it against a fresh locator/identity and verified bytes, or preserve it as a conflict and use a new name. Manifest-write failure stops the task; do not continue producing untracked successes.

## L. Security / privacy

Threats: tokenized locators, cross-host Cookie and Referer leakage, unexpected redirects, HTML challenges masquerading as images, untrusted Content-Disposition/path names, oversized/chunked responses, disk exhaustion and manifest/file mismatch. Image decoding is unnecessary, so decoder decompression bombs are outside the downloader's work; compressed transport still needs bounds or rejection.

Required guards:

- Separate cookie-free resource transport. The existing HttpClient automatically adds saved/learned Cookie to requests and reads whole bodies; do not use that authenticated instance for CDN streaming. Current safe diagnostics retain URL paths; image paths can contain identifiers, so image logs need stronger redaction.
- HTTPS only, verified TLS, exact initial host allowlist based on wx1–wx4.sinaimg.cn for the observed attached-image scope. Reject HTTP-only candidates rather than silently changing schemes. Reject userinfo, unexpected ports/IP hosts and unsupported schemes. Unknown hosts become an explicit unsupported/access result pending review.
- Validate every redirect before following; cap at three, enforce HTTPS and exact approved image hosts. Never forward credentials. An allowlisted hostname is not by itself protection against a hostile DNS/proxy environment; retain system trust and reject non-public destinations if resolution is controlled by this transport.
- Do not send source-post URL as Referer. A fixed first-party origin Referer could be assessed in a later authorized normal-client comparison; this study did not prove it necessary. No CDN cookies without concrete evidence and a reviewed scope policy.
- Proposed conservative limits, not empirically tuned: 50 MiB per image, 15-second socket timeout, 60-second per-asset wall deadline with cancellation checks, a disk-space reserve. Check Content-Length if supplied, enforce streamed bytes regardless, require EOF and consistency when length is supplied. Reject unexpected content encoding; request identity.
- Generate filenames locally; ignore Content-Disposition. Validate all manifest paths stay within the selected root; reject traversal, absolute paths and reparse escapes. Allow only fixed extensions.
- Content-Type allowlist plus magic/header sanity: JPEG FF D8 FF; PNG full signature; GIF87a/GIF89a; WEBP RIFF plus WEBP and sane length. URL suffix is only a hint. Require MIME/signature agreement, reject HTML/SVG, unknown binary and generic octet-stream in conservative v1. Signature checks do not prove a complete valid image; check nonempty data, EOF, length and hash without claiming full decoding validation.
- HEIC/HEIF was not observed: exclude from v1; future support would require bounded ISO-BMFF ftyp-brand inspection and compatible MIME evidence, not accepting every ftyp file as image. No Pillow needed for basic signatures.
- GIF locators and Live Photo stills are in scope only when returned bytes pass the supported-type checks. Save GIF as an image file; never follow gifvideos/livephoto videoSrc. Explicit video slots and page_info covers/icons/avatar are out of scope. Unknown future subtype is a warning, not silently assumed ordinary image.
- Do not persist remote URLs, headers, tokens, cookies or ordinary post text. Reacquire locators from a fresh timeline for resume. Keep pids/post mappings in the private local manifest because traceability requires them; do not include them in public diagnostics. File bytes can themselves contain personal metadata; preserve locally without decoding/uploading.

## M. Performance / pacing

Sequential behavior: one API or image request at a time, no parallel text/image crawls. Image CDN hosts are distinct from m.weibo.cn. Use a modest cancellation-aware 0.5-second minimum start spacing initially; this is a conservative proposal, not an inferred server rate limit.

Retries: none by default in v1; report transient failure for later manual retry. Stop on systemic denial/rate limiting; never repeat 403 with escalating headers/cookies or transformations automatically. An eventual single transient retry would require an explicit bounded policy.

Timeout: proposed 15-second socket bound and 60-second total deadline, streaming chunks and cancellation checks. A blocked stdlib socket read may delay cancellation until its socket timeout; UI must describe cancellation as requested until worker exit.

No evidence warrants importing the text client's 120-second session-rest logic into image transfers. API traversal should retain its own production pacing/guards. No reason concurrency is needed for feasibility; throughput is deliberately not optimized. Extra disk and manifest writes dominate large-run cost, and images can dwarf text archive size.

## N. Dependencies / packaging

New runtime dependencies: none expected. urllib.request, ssl, threading, tempfile, os, json, hashlib, pathlib and bounded signature parsing are sufficient for the proposed downloader/manifest. Existing Tkinter can host the separate dialog. No requests, aiohttp, Pillow, ffmpeg or browser runtime.

Expected bundle impact: several small Python modules, no major bundled runtime. This is an architectural estimate, not a measured build. Frozen-build import inclusion and Windows filesystem/TLS behavior would need later smoke checks. No release was built in this spike.

## O. Implementation scope estimate

MEDIUM, conditional on resolving the download evidence gap.

Likely additions: weibo_archive/image_models.py, image_parser.py, image_client.py, image_downloader.py, image_manifest.py, image_ui.py; dedicated synthetic image tests/fixtures if implementation is approved.

Likely existing-file changes: app.py for a separate entry/dialog/task guard; tests/run_tests.py only to register future tests if needed; product documentation only after scope is approved. Potentially a narrowly reviewed shared raw-page helper in client.py/network.py, but no text-semantic changes and no package reorganization. No production changes now.

It is not SMALL because secure streaming, redirect policy, recoverable manifest/file commits, edits/collisions, enumeration warnings and independent UI cancellation need care. It need not be LARGE because no decoding, video pipeline, parallel scheduler or generalized crawling framework is needed. Deterministic future tests should cover these real failure boundaries and preserve existing text golden contracts; private live responses must not become fixtures.

## P. Reasons NOT to build it

1. No successful image GET was established; four Live Photo stills plus one HEAD all returned 403. Ordinary/GIF and normal Referer behavior remain untested.
2. Timeline enumeration is demonstrably limited in >9 declared-image cases; no verified path closes the gap.
3. Per-node pic_ids-to-pics correspondence and interpretation of mixed-media pic_num are not established.
4. large/original labels do not prove upload-original quality; no comparative bytes/dimensions were obtained.
5. GIF is a locator-shape observation, not verified GIF/animation bytes; HEIC/WEBP behavior remains unobserved.
6. URLs, pids, remote ordering and edit semantics are undocumented and may change. Ordinal filenames alone cannot detect revisions.
7. No-URL manifests require fresh enumeration to resume; deleted/inaccessible sources can make retries impossible.
8. Existing authenticated HttpClient is not a safe drop-in CDN downloader; streaming and redirect guards add code and audit cost.
9. Reusing range intent is easy; reusing the current Post-producing traversal without coupling is not. Duplication or a narrow future shared boundary creates regression/maintenance cost.
10. Images add substantial storage, partial-write, disk-full, power-loss and collision recovery obligations.
11. One login/target/session and three pages are not a platform-wide guarantee; no real success, retry, redirect, large-file or cancellation transfer path was exercised.
12. UI must clearly distinguish unavailable source, incomplete enumeration and failed transfer without contaminating text success. This is a separate product surface to maintain.

## Q. Recommendation

Do not begin Image Archiver v1 implementation on this evidence alone. Preserve the separate architecture decision and perform a separately bounded follow-up only if the owner wants to resolve the blocker.

Evidence needed to change the verdict: successful normal HTTPS GET and MIME/signature agreement for representative ordinary JPEG and returned GIF assets; a Live Photo still transfer or explicit support limitation; a controlled normal-client check with a fixed first-party Referer to distinguish that missing header from other causes, without CDN Cookie escalation, URL surgery or bypass. Successful full-file EOF/size validation is still needed after any prefix probe.

For the limited product, owner acceptance of “returned downloadable images” plus explicit enumeration gaps is sufficient in place of a new endpoint. If the requirement is all images, evidence of complete >9 enumeration through an already-known safe response/request is additionally necessary. Also establish per-node pid matching and revision/collision handling before promising reliable skip/resume. No automatic continuation or implementation is authorized by this document.

## R. Git status

- branch: main
- baseline: f45919b, Release 0.5.7, HEAD tagged v0.5.7
- staged: none
- unstaged tracked changes: none
- untracked: docs/IMAGE_ARCHAEOLOGY.md only
- No production/test edits, version bump, stage, commit, tag, push or release build.

## Follow-up — 2026-09-16: normal GET and fixed Referer

### Updated decision

**GO WITH LIMITS.** This dated follow-up supersedes the original DEFER verdict and NOT-YET implementation recommendation above; the original observations remain as historical evidence. The owner explicitly accepted the limited product promise below and defined successful HTTPS status, matching image MIME/signature and no HTML as the bounded download-feasibility criterion. Full-file transfer was deliberately not required or performed in this probe.

Representative ordinary still images from two different top-level posts, one GIF, one Live Photo still and one additional nested RT ordinary still all passed that criterion with the production fixed Referer. No image-CDN Cookie, URL transformation, video request, browser, proxy or anti-bot escalation was needed. The prior Range/HEAD failures therefore do not establish that ordinary image backup is infeasible.

### Scope and privacy

- Preflight: main; HEAD f45919b407c8c749dc407541e8d5f27be7c5394a (v0.5.7); no tracked modifications; only this existing untracked document.
- Existing saved credentials were read through the read-only protected-store method. Credential hashes matched before/after; no credential migration, deletion or write.
- Same selection policy as the initial study: most recently modified local cached target, without printing its identity.
- Two normal timeline pages plus one existing production preheat: 3 API requests. Sample selection inspected 152 top-level occurrences and their available RT nodes before all categories were found. No full archaeology rerun, long-text/detail request or new endpoint.
- Five distinct assets, all using the exact pics[].large.url supplied by the response. Two ordinary W photos were selected from different posts. GIF was directly returned in pics[]; Live Photo used its still URL, never videoSrc.
- Exactly two normal streaming GETs per asset, 10 image requests total. First used the production User-Agent; second added only the normal fixed Referer https://m.weibo.cn/. No Range or HEAD, no automatic retries or header permutations.
- Verified TLS; explicit direct connections with proxy handling disabled; separate resource opener without any Cookie header or cookie jar. Requests were sequential with 0.5-second cancellation-aware waits and 15-second socket timeout.
- Redirect following was disabled conservatively in this probe; no redirect response occurred. All observed final responses came directly from the requested HTTPS image host.
- Read cap 65,536 bytes per response. Error bodies were classified in memory without printing their content. Total resource bytes read: 328,870 (five 238-byte error responses plus five 65,536-byte image prefixes), below the 655,360-byte overall cap.
- No image or raw-response file was written. The temporary script was outside the repository and was deleted after the run. Only this untracked document was updated; production and tests remain unchanged.

### Samples

| Sample category | Observation | Selected assets |
|---|---|---|
| Ordinary still | OBSERVED | 2 JPEG-like assets from different W posts |
| GIF | OBSERVED | 1 directly supplied GIF candidate, validated GIF signature |
| Live Photo still | OBSERVED | 1 RT static component, validated JPEG signature |
| Nested RT still | OBSERVED | 1 additional ordinary RT image, validated JPEG signature |

### Request results

The labels below are synthetic sample labels, not post or asset identifiers. Content-Length is the response's advertised total size; bytes read is the actual prefix inspected.

| Sample | Form | HTTP | Final hostname | Redirects | Content-Type | Content-Length (bytes) | Signature | Bytes read | Result |
|---|---|---:|---|---:|---|---:|---|---:|---|
| Ordinary A | UA only | 403 | wx3.sinaimg.cn | 0 | text/html | 238 | HTML | 238 | Failed |
| Ordinary A | UA + fixed Referer | 200 | wx3.sinaimg.cn | 0 | image/jpeg | 1,278,288 | JPEG | 65,536 | DOWNLOADABLE |
| Ordinary B | UA only | 403 | wx4.sinaimg.cn | 0 | text/html | 238 | HTML | 238 | Failed |
| Ordinary B | UA + fixed Referer | 200 | wx4.sinaimg.cn | 0 | image/jpeg | 862,039 | JPEG | 65,536 | DOWNLOADABLE |
| GIF | UA only | 403 | wx3.sinaimg.cn | 0 | text/html | 238 | HTML | 238 | Failed |
| GIF | UA + fixed Referer | 200 | wx3.sinaimg.cn | 0 | image/gif | 2,813,812 | GIF | 65,536 | DOWNLOADABLE |
| Live Photo still | UA only | 403 | wx1.sinaimg.cn | 0 | text/html | 238 | HTML | 238 | Failed |
| Live Photo still | UA + fixed Referer | 200 | wx1.sinaimg.cn | 0 | image/jpeg | 1,049,665 | JPEG | 65,536 | DOWNLOADABLE |
| Nested RT still | UA only | 403 | wx1.sinaimg.cn | 0 | text/html | 238 | HTML | 238 | Failed |
| Nested RT still | UA + fixed Referer | 200 | wx1.sinaimg.cn | 0 | image/jpeg | 1,085,165 | JPEG | 65,536 | DOWNLOADABLE |

Referer conclusion: required for successful retrieval in this bounded paired matrix; every UA-only attempt failed and every fixed-Referer attempt succeeded. This supports using the existing normal mobile origin Referer as ordinary CDN/hotlink behavior. It does not prove a universal permanent requirement across all hosts or times. No sample succeeded without it.

Cookie conclusion: Cookie was NOT sent to the image CDN. All five successful responses were obtained without session credentials at the CDN.

### Accepted completeness boundary and remaining limits

Exact accepted product promise:

> 保存本次微博接口明确返回且可下载的图片。

The future UI/result must separately report declared image/media count, enumerated image slots and enumeration gap. pic_num > len(pics) is not a blocker for this limited v1, but cannot be hidden or presented as complete media backup. No >9-image endpoint investigation was performed.

Ordinary still, GIF and Live Photo still acquisition are feasible under this task's prefix-validation criterion. Full-file EOF/integrity, complete GIF animation structure and decode validity were not tested; future downloads must still enforce size bounds and complete-transfer checks. This small sample does not establish success for all images or hosts, permanence of URLs, or original-upload quality.

Remaining outside demonstrated support: Live Photo video and normal video (intentionally excluded); PNG/WEBP/HEIC/HEIF resource retrieval (not sampled); unavailable/deleted resources; unreturned image slots; unexpected CDN hosts and redirect chains. No higher-quality URL derivation was tested or authorized.

### Implementation recommendation, not implementation authorization

**YES**: the evidence supports proceeding to a separately approved limited Image Archiver v1 implementation. Maximum scope:

1. Independent image models/parser/download state/manifest/output; preserve Post, Archive, WEIBO_AI_1 and all text-export semantics.
2. Save only directly returned in-scope image URLs: ordinary stills, verified GIF files and Live Photo static components; exclude video, covers and avatars.
3. Use sequential verified HTTPS with production UA and fixed mobile-origin Referer, a cookie-free CDN client, approved hosts and strict redirect/size/type controls; no URL rewriting or guessing.
4. Attribute every asset to its source post and W/RT relation, preserving raw slot ordinal and optional pid for reconciliation; no cross-post global deduplication.
5. Report declared count, enumerated slots and enumeration gaps separately, using exactly the accepted limited promise.
6. Use atomic image finalization and manifest checkpoints; verify existing files before skipping; preserve completed files on cancellation/failure.
7. Provide a separate image dialog, progress/cancellation and COMPLETE/PARTIAL/CANCELLED/FAILED results independent of text success.
8. Remain stdlib-only; validate full-stream failure/size/EOF handling and synthetic recovery/security tests during implementation. No browser, Pillow, ffmpeg or concurrency.

Final follow-up Git state: main; no staged or unstaged tracked modifications; docs/IMAGE_ARCHAEOLOGY.md is the sole untracked file. No feature implementation, test modification, version bump, stage, commit, tag, push or release build.

## Implementation validation — 2026-09-16: non-UI v1 core

**CORE READY FOR UI.** Implementation was explicitly authorized after the accepted feasibility follow-up. The scope is the non-UI core only, on feature/image-archiver-v1, based on unchanged v0.5.7 / f45919b407c8c749dc407541e8d5f27be7c5394a. No existing tracked production file or test/golden was changed. No stage, commit, tag, push, version bump or release build.

### Core boundary and entry points

Six new modules provide image-only frozen models, raw parsing, independent timeline traversal, credential-free CDN streaming, schema-1 manifest storage and orchestration:

- image_models.py: independent candidate/record/result types; locator hidden from repr and present only in runtime candidates.
- image_parser.py: independent W/RT attribution, original slot order, direct large/regular selection, explicit video exclusion and controlled diagnostics for ambiguous/malformed slots.
- image_client.py: ImageTimelineClient.iter_posts; authenticated existing JSON primitive only, no WeiboClient.fetch, Post construction or long-text hydration. Trial/Recent count unique non-pinned timeline posts, with pins retained as extras. Absolute-date since evidence, two distinct wholly-old pages, fail-closed empty-response handling and three-page no-progress protection are tested. statuses_count remains informational and is not fetched solely for image discovery.
- image_downloader.py: verified HTTPS normal GET to wx<digits>.sinaimg.cn, fixed mobile-origin Referer, production UA, no CookieJar/Cookie/Authorization/proxy, no redirects/Range/HEAD/retries, 0.5-second minimum request-start spacing. Defaults: 50 MiB per file, 15-second socket timeout, approximately 60-second deadline, 64 KiB stream chunks and 64 MiB disk reserve. Disk reserve is also checked during streaming.
- image_manifest.py: URL-free explicit schema-1 projection, same-directory atomic/fsynced writes, path/reparse guards, deterministic collision suffixes and OS-backed single-writer root lock. A small .image-backup.lock file is retained; the OS releases its advisory lock on process exit/crash. The manifest has a conservative 64 MiB size ceiling and fails safely rather than writing an unreadable checkpoint.
- image_backup.py: ImageBackup.run(target_identity, fetch_range, client=client) for complete traversal orchestration; run_records(..., report=...) for already-discovered runtime records and bounded tests. A caller supplying truncated discovery must leave termination=None. Both return an independent ImageBackupResult.

No image_ui.py or GUI hook was added. app.py, Post, Archive, MediaInfo, WEIBO_AI_1, Full/Custom exports, text long-text/tombstone/cache/auth/pacing/pagination behavior remain unchanged. A future UI should run the synchronous core in its own worker and provide one shared cancellation Event to its image task; no text success flag is reused.

### Persistence and completion semantics validated

Manifest persists selected-range/run provenance, record count facts and safe asset identities/results, never runtime remote URLs, request headers, cookies, post text or author names. pic_num minus len(pics), floored at zero, is explicitly an enumeration gap, not a proven number of missing still images. Any gap or unresolved discovery warning prevents COMPLETE. Known video slots are excluded from the image candidate set and counted separately.

Each final filename uses the validated source/containing IDs and raw ordinal, with source-year or unknown-date; extensions come from verified signatures, never URLs. Existing unrelated/corrupt files are preserved with deterministic _2, _3, ... allocation. After signature-prefix identification, pending identity/path is checkpointed before the temporary image file is created. Full data is streamed and hashed, checked against supplied Content-Length, flushed and fsynced. Expected size/hash are checkpointed while still pending before the final atomic replacement. An exclusive destination reservation prevents an unrelated file created after allocation from being overwritten.

Resume reads the entire existing local file, checking signature, extension, byte size and SHA256 before already_present. Prior pending entries are recovered only when durable expected hash/size/type metadata matches a complete local scan. A bare pending filename is never adopted. A file committed before manifest failure remains useful output; the run is PARTIAL, and the preceding pending checkpoint permits later verified recovery. Successful earlier files are never deleted by cancellation or failure.

COMPLETE requires valid traversal termination, every eligible image saved/verified present, and no gap/warning/unavailable/failed/pending entry. PARTIAL requires useful saved/verified output plus an unresolved issue/global stop. CANCELLED preserves files/checkpoints and leaves unprocessed assets pending. Failure before useful output is FAILED. A valid empty image range can be COMPLETE with zero files. HTTP 404/410 are unavailable; HTTP 403 is a failed access request, not proof of deletion. Systemic access/rate/storage failures stop conservatively.

JPEG, PNG, GIF and WEBP have byte-signature checks and fixed local suffixes. MIME, when supplied, must be supported and agree with the signature. Missing MIME can be accepted only with a supported signature. HTML/SVG/unknown bodies and encoded transport are rejected. WEBP RIFF length is checked too. These are bounded binary sanity checks, not image decoding or proof of full semantic validity.

### Offline verification

- Existing tests/run_tests.py: all 70 unchanged test groups passed using the existing repository Python 3.14 build environment and a temporary isolated LOCALAPPDATA. The initially available bundled interpreter had a Tcl initialization problem; no tests or goldens were altered to work around it.
- New tests/test_image_core.py: 96 deterministic tests passed, including all requested parser/range/CDN/filesystem/manifest/result categories. It is independently runnable with python -B -m unittest discover -s tests -p test_image_core.py; existing test registration was not changed.
- New tests also cover credential-free headers, verified TLS, redirect refusal, manifest injection/path rejection, hash-required crash recovery, commit/checkpoint ordering, concurrent-root locking, changing pid, disk-space loss and cancellation-aware pacing.
- Windows did not permit creating a real symlink in the test environment; that test uses a synthetic reparse-point stat to exercise the rejection guard. This does not claim an adversarial concurrent filesystem/symlink-race audit.
- Total: 166 passing tests/groups across the unchanged text suite and new image suite. Zero golden changes. No new runtime dependency.

### Single bounded live core smoke

One live run was performed only after the offline suites passed. It used read-only existing saved credentials and the same latest-cached-target selection policy, with no printed identifiers or private URLs/text.

| Measurement | Result |
|---|---|
| Timeline pages / API requests | 1 / 1; no extra preheat, profile, long-text or detail request |
| Full image downloads attempted | 4, sequential, one normal GET each |
| Fully saved files | 4; zero failed or unavailable |
| Naturally selected sample categories | 2 ordinary W photos, 1 GIF, 1 additional ordinary RT image |
| Actual verified file types | 3 JPEG, 1 GIF |
| Total full-file bytes | 6,039,304 |
| Supplied Content-Length matched EOF byte count | 4 / 4 |
| SHA256 independently recomputed and matched manifest | 4 / 4 |
| Signature and actual saved extension matched | 4 / 4 |
| Resume already_present | 4 / 4 |
| Extra CDN requests during resume | 0 |
| Extra timeline requests during resume | 0; same runtime candidates reused |
| Cookie/Authorization sent to image CDN | No; audited before each actual request |
| Manifest remote URLs / credential/header/post-text fields | None |
| Credentials changed | No; before/after file hashes matched |
| Temporary live directory | Deleted after verification |

Live Photo was NOT OBSERVED within this single page; no second page or substitute request was made to improve coverage. Its static-candidate parsing is covered offline, and the earlier follow-up established successful JPEG-prefix retrieval, but this smoke does not claim a new full-file Live Photo test. PNG and WEBP are offline-tested only; HEIC/HEIF and video remain unsupported.

The bounded selection intentionally supplied no complete traversal termination. Accordingly both sample operations report PARTIAL / discovery_incomplete despite all selected file operations succeeding. This validates the rule against presenting a selected sample as a full range backup. It is not a resource-download failure. The live output was confined to a fresh verified temporary directory outside the repository; full files, their private local manifest and temporary script were removed after aggregate evidence was recorded.

### Remaining UI-phase work and limits

Likely integration is a new image_ui.py plus a small app.py entry/task coordination hook and focused UI tests, subject to later authorization. Decisions remain around dialog defaults, confirmation of output/target, how pinned extras and enumeration gaps are presented, progress delivery and mutual exclusion with text network tasks. No user-visible product claim or UI has been added now.

The accepted promise remains exactly “保存本次微博接口明确返回且可下载的图片。” Missing timeline slots, original-upload quality, URL permanence and remote edits under unchanged pid are not solved. Resume reacquires metadata in an ordinary new run; it is not a stable page-number checkpoint. The in-memory discovery/manifest metadata and per-asset full manifest rewrite have scaling costs. Cancellation during a blocked socket read can wait until socket timeout; the wall deadline is checked between reads. Atomic replacement and fsync do not promise filesystem-independent power-loss guarantees or protection from another malicious process running as the same OS user.

## UI integration validation — 2026-09-17

**READY FOR OWNER UI REVIEW.** This phase adds a thin Tkinter/ttk surface around the accepted core. The six image-core modules and tests/test_image_core.py were not modified. The sole modified tracked file is app.py (27 added lines); new files are image_ui.py and tests/test_image_ui.py. This research file remains untracked. No version, public documentation, text goldens or release metadata changed. Nothing was staged, committed, tagged, pushed or published.

### Product behavior and isolation

- Main action area gains one secondary “图片备份…” button. A single owned Toplevel opens; reopening raises/focuses it. There is no text-export image checkbox or second Tk root.
- Image window defaults to “测试备份 20 条”. Recent and Since validation reuse the existing text range validator; target parsing reuses its UID extraction and single-post URL guard. Valid main target prefills an independently editable image input.
- Output input selects a base folder, defaulting to the application's Archives base. A locally resolved WeiboImages_<numeric-target> subfolder is derived for stable per-account state. Existing same-target backups run without a resume prompt; target mismatch fails with a controlled message.
- Exact scope: “保存本次微博接口明确返回且可下载的图片；视频不在备份范围内。” Resume explanation: “已有图片会先校验，确认完整后不会重复下载。” Only ALL mode has the one-time-per-start disk/time confirmation.
- All UI fields resolve into a frozen ImageBackupRequest before starting the background worker. Network, local verification and checkpoint reads run off Tk. A bounded mailbox retains only the latest aggregate progress plus one terminal result, each tagged with the image task generation. Arbitrary exceptions never become UI text.
- UI-local adapters observe discovery groups and read atomic checkpoints between work units; they do not change core transport, parser, manifest schema or result semantics. No raw locator, pid, post body, Cookie or header enters progress events.
- Text login/export/recovery or a still-draining cancelled text worker blocks image Start. An active image worker disables and guards text export/login/credential clearing. Its ownership is not released until the worker actually exits. Image-window close requests cancellation first and waits for terminal cleanup; closing the main app during image work follows the same route.
- Saved-login abstraction is reused. There is no image QR flow or credential file. Expiry stops safely and instructs the owner to reauthenticate in the main window, then rerun the same backup. Existing files/checkpoints are preserved.
- Image ActivityIndicator is another instance of the unchanged published Pac-Man class. Image progress/result does not change the text status, animation, generation or completion view. Folder opening uses the existing safe local-path launcher only after the explicit image button action.

### Result wording

| State | Title | Important explanation |
|---|---|---|
| COMPLETE | 图片备份完成 | 本次接口明确返回的可下载图片已处理完成。 |
| PARTIAL | 图片备份完成，但存在未保存项目 | 已成功保存的文件会保留，可稍后再次运行同一备份继续检查。 |
| CANCELLED | 已取消图片备份 | 已经成功保存的图片和进度记录会保留。 |
| FAILED | 图片备份未完成 | Controlled Chinese failure mapping; no upstream exception text. |

The result shows inspected posts, enumerated slots, known declared count/unknown-count records, saved/already-present/unavailable/failed counts and enumeration gap. Positive gaps additionally say: “微博声明的媒体数量超过本次接口返回的图片槽位数量，因此不能确认所有历史图片均已枚举。” It never claims all historical images are backed up.

### Final offline verification

- tools/build/environment_check.py: PASS.
- Existing tests/run_tests.py: 70/70 unchanged test groups PASS.
- tests/test_image_core.py: 96/96 PASS, unchanged.
- New tests/test_image_ui.py: 41/41 PASS.
- Total: 207 tests/groups PASS. git diff --check PASS. Zero golden changes.

Tests cover single-window lifecycle, focus/prefill independence, range/default/confirmation behavior, no-login blocking, frozen requests, non-Tk workers, coalesced progress, stale generations, cancellation/close, exact single terminal outcome including malformed runner results, bidirectional task exclusion, credential-expiry/mismatch wording, no private diagnostics, explicit local folder open, ordinary resume, and a fake-network run through the actual UI worker adapters/core. AST comparisons verify the published ActivityIndicator, theme, range validator, text worker, completion dialog and text event pump unchanged; the main layout differs only by the image button. Existing single-fetch text contracts and all prior goldens continue to pass.

### Local packaged development build and visual review

Used the existing pinned build environment and unmodified tools/build/weibo_text_archiver.spec. The local onedir bundle is isolated at build/image-ui-dev/dist/WeiboTextArchiver_0.5.7_Windows/; published dist/release artifacts were not replaced. Existing package auditing, bundled-doc copying, GUI executable/icon verification passed. A rebuild briefly encountered DLL locks from the prior smoke process; after that process exited the final rebuild and audit succeeded. No release ZIP or release metadata was produced.

Final packaged startup showed “Weibo Text Archiver · 0.5.7”; the new image window opened with no missing imports. Windows screenshots were inspected: controls fit, range suffixes remain attached to their fields, original main cards/layout remain intact except the added action. Long filesystem paths use the normal horizontally scrollable entry and can be visually truncated when unfocused. No new theme/framework/style system was introduced.

Owner artifacts, ignored by Git and not committed:

- build/image-ui-dev/review/main-packaged.png — final packaged main window, blank target.
- build/image-ui-dev/review/image-idle-packaged.png — final packaged image window, blank target.
- build/image-ui-dev/review/image-partial-offline.png — synthetic PARTIAL result rendered by the same source UI, explicitly titled as an offline example; no private account data.

The PARTIAL artifact is a source/offline view, not a claim that a live or packaged run produced those synthetic counts. No production screenshot/test hook was added.

### Bounded live UI smoke

After source tests and packaged startup/layout checks passed, one Trial-20 backup ran through the actual source UI Start button, frozen request, worker/controller, image core, Tk polling and result rendering. One same-target/same-range rerun verified normal resume; no extra run sought better coverage. Test instrumentation audited outgoing image headers and observed core results without changing network/download behavior. The later final UI-only malformed-result guard was covered offline and packaged; no live rerun was needed for that exception-path change.

| Fact | Initial Trial-20 | Same-range resume |
|---|---:|---:|
| Inspected top-level posts | 22 | 22 |
| Normal target count | 20 | 20 |
| Pinned extras, retained without consuming target count | 2 | 2 |
| Enumerated / eligible image slots | 71 / 71 | 71 / 71 |
| Saved | 71 | 0 |
| Already present | 0 | 71 |
| Unavailable | 0 | 0 |
| Failed | 0 | 0 |
| Enumeration gap | 0 | 0 |
| CDN GET requests | 71 | 0 |
| Terminal state | COMPLETE | COMPLETE |

UI terminal results exactly matched the core result and persisted manifest state. Local saved files were independently checked against manifest size/SHA256. Tk remained responsive: 1,132 observed timer ticks across the two operations, maximum interval 0.122 seconds; 117 progress/count updates occurred. Cancel was available during work and text export controls were disabled. Explicit Open Backup Folder invoked the normal Windows launcher successfully.

Cookie/Authorization was never sent to the CDN; the fixed normal Referer remained present. No text fetch/export/cache-save path was invoked, and no Markdown was produced. Manifest contained no remote URLs or credential headers. Credential-file hashes matched before/after. A fresh verified temporary output root outside the repository held the live files and manifest; it was deleted after verification. Temporary harness scripts were removed. No private identifiers, URLs, post text or live screenshots are retained in this report/artifact set.

### Remaining owner/release decisions

Owner visual acceptance is pending. Product documentation, final version selection and release-finalization remain explicitly deferred. There is no observed implementation blocker in the verified workflow. The accepted image-core limitations still apply: current returned slots only, no video, no guarantee of original quality/permanent remote locators, and cancellation may wait for a blocked socket timeout. Remote authentication expiry was exercised through offline behavior/wording tests, not deliberately triggered against the live account. This phase's screenshots cover the current Windows display setup, not a full DPI/monitor compatibility matrix.

## Performance spike — 2026-09-17

**KEEP CURRENT SEQUENTIAL DESIGN.** Exactly one live sequential baseline was executed. No optimized live run was warranted by the measured gate; no concurrency code, connection pool, reduced delay, changed checkpoint policy or new retry behavior was introduced.

### Existing pacing: already latency-aware

image_downloader.py computes max(0, spacing - (clock() - last_start)) before a request, then records the new request start. The 0.5-second constant therefore means the next request starts no earlier than the previous start plus 0.5 seconds (B), not an extra 0.5 seconds after completion (A). Network, hashing and intervening checkpoints already consume that interval. The latency-aware request-start idea is already present conceptually; no equivalent rewrite was made.

### Opt-in measurement, not a product change

New tools/image_performance.py provides an opt-in ImageMeasurement for a dedicated sequential research process. It uses monotonic perf_counter measurements and scoped wrappers around existing calls; wrappers/opener references are restored on failure or completion. The production GUI does not import it. It makes no request by itself and retains no URLs, post IDs, pids, filenames, headers, body text or credential material in its metrics. Allowed CDN hostnames, status counts and Content-Length totals are aggregate facts. An assertion checks the existing credential-free GET/UA/Referer contract without injecting or changing headers.

The five exclusive elapsed-time buckets sum to measured wall time:

- network: urllib open/read calls, including connection/TLS/response/body waiting; not an isolated wire-transfer or CPU measurement;
- pacing: actual elapsed cancellation-aware wait, with requested/planned wait recorded separately;
- filesystem_hash: remaining local downloader validation/signature/hash/file/fsync work and manifest-store path/resume work outside atomic writes; includes small Python bookkeeping/cleanup;
- manifest: entire atomic manifest write, including schema/path validation, JSON serialization, temporary write, fsync and replacement;
- other: remaining orchestration and measurement overhead outside those spans.

Manifest validation time is a nested non-additive detail of the manifest bucket. Latency means request start through image finalization, excluding pre-request pacing and the following saved-state manifest update. Aggregate throughput is saved MiB divided by full measured core wall time. Acquisition, independent post-run file auditing and cleanup are outside that timed interval. No metrics appear in normal user UI, no telemetry is sent, and no timing values enter the image manifest.

### Baseline workload and execution

The existing saved login was read without modification. One normal timeline page/API request acquired candidates once. Selection followed the Trial-20 discovery path and stopped after 50 eligible assets were accumulated, at 16 inspected top-level records; no full crawl or special-category search occurred. The selected records contain 51 raw slots, but the last eligible slot beyond the explicit 50-asset sample cap was not scheduled. No upstream enumeration gap was observed in these records.

The immutable runtime candidate tuple was kept in memory until the experiment decision, without a second timeline request. The sample contained 49 JPEG files and one GIF; source relation distribution was 44 TOP_LEVEL and 6 RETWEET_SOURCE. Live Photo still was NOT OBSERVED in the selected sample. A fresh temporary output root outside the repository prevented already-present files from affecting baseline timing.

| Baseline metric | Measured result |
|---|---:|
| Eligible assets / requests / successful downloads | 50 / 50 / 50 |
| Already present | 0 |
| Full bytes saved and read | 17,250,190 |
| Aggregate Content-Length | 17,250,190 across 50 responses |
| Wall time | 25.755640 s |
| Network open/read time | 2.645498 s |
| Planned pacing wait | 9.285083 s |
| Actual pacing elapsed | 9.816956 s |
| Filesystem/hash/local validation | 0.845559 s |
| Manifest atomic-write path | 12.445236 s |
| Other | 0.002391 s |
| Aggregate throughput | 0.638736 MiB/s |
| Atomic manifest writes | 217 |
| Manifest validation, included above | 11.689526 s |
| Per-image latency mean / p50 / p95 | 0.186353 / 0.197503 / 0.284386 s |
| HTTP status summary | 200: 50 |
| 403 / 429 / other 4xx / timeout / TLS-network failures | 0 / 0 / 0 / 0 / 0 |

CDN host distribution: wx1.sinaimg.cn 8, wx2.sinaimg.cn 13, wx3.sinaimg.cn 17, wx4.sinaimg.cn 12. No full URL was recorded. Planned wait was 36.0507% of wall time; actual waiting was 38.1158%, reflecting that timed waits can return later than their requested duration.

### Where wall time went

| Exclusive bucket | Share of wall |
|---|---:|
| CDN network open/read | 10.2715% |
| Actual spacing wait | 38.1158% |
| Filesystem/hash/local work | 3.2830% |
| Atomic manifest-write path | 48.3204% |
| Other | 0.0093% |

The largest measured code path is manifest writing, but 93.9277% of that bucket is its validation step, which repeatedly checks schema and filesystem paths for stored assets. It would be inaccurate to label all 12.45 seconds as disk serialization/fsync. Fresh downloads already produce SHA256 incrementally; the baseline adds an independent full-file audit after measurement, not a second production scan to optimize away.

### Candidate decision and fixed-spacing ceiling

No candidate run was executed; before/after timings and speedup are NOT MEASURED, not zero or synthetic improvements. The baseline candidate tuple remained available in memory while making this decision, then was discarded with cleanup. There is no second workload for which URL, byte or manifest equivalence can be claimed.

Concurrency=2 failed the prerequisite gate: network was only 10.27% of wall, while pacing was material rather than small. Consequently no concurrency, per-host scheduler or concurrency-specific production tests were added.

Sequential manifest optimization was also declined for this sample. At unchanged global sequential request-start spacing, 50 requests alone require at least (50 - 1) * 0.5 = 24.5 seconds between first and last request starts. The baseline wall was 25.755640 seconds. Even the unrealistic limit of zero final-request/commit/setup cost permits at most 1.255640 seconds, or 4.8752%, further sequential wall reduction. Much of any reduced validation work would become additional pacing wait. This is a conservative theoretical upper bound for this measured workload at fixed spacing, not an observed optimization result and not a general large-archive forecast.

There is no reason to introduce checkpoint/path-validation complexity for this small ceiling, weaken checks, or simply lower the spacing constant. The optional custom connection-reuse investigation was not pursued; no pool or alternative HTTP dependency was added.

### Correctness, privacy and cleanup

All 50 saved files passed an independent complete-file signature/size/SHA256 comparison against their manifest entries after timing. Supplied Content-Length matched received sizes through the unchanged downloader. SHA256 equality between live variants is N/A because there was no variant. An offline test verifies the instrumented and uninstrumented core produce identical bytes, result and manifest semantic facts, excluding run timestamps.

The bounded sample result is intentionally PARTIAL / discovery_incomplete: the research selection supplies no complete traversal termination. It does not mean any of the 50 scheduled downloads failed. Atomic writes, fsync, full-file resume verification, cancellation, TLS/host policy, fixed Referer and no-Cookie behavior remain unchanged. Existing tests and new instrumentation cleanup/cancellation tests pass. Credential-file hashes matched before/after. Temporary full images, private manifest, retained candidate process, sanitized temporary metrics/control files and the temporary benchmark harness were cleaned up. Only aggregate evidence is retained here.

### Verification and repository state

- Existing text suite: 70/70 PASS.
- Existing image core suite: 96/96 PASS.
- Existing image UI suite: 41/41 PASS.
- New tests/test_image_performance.py: 10/10 PASS (aggregation/privacy, exclusive timing, resume count, failure summaries, patch restoration, sequential ownership, cancellation cleanup, existing start-spacing behavior, percentiles, semantic/byte equivalence).
- Total: 217 tests/groups PASS. git diff --check PASS. Existing 207 tests/goldens unchanged.
- This spike only adds tools/image_performance.py and tests/test_image_performance.py and appends this section. Existing app.py's 27-line UI integration diff predates this spike; all image/text/UI production code is unchanged during this task.
- Branch remains feature/image-archiver-v1. Nothing staged, committed, version-bumped, tagged, pushed, packaged or released in this phase.

Evidence boundary: one small, fresh-root, local-system run is not a stability study, large-manifest/resume benchmark or full network diagnosis. A larger pre-existing manifest may change the balance because validation traverses its asset entries; that case was not measured. The present evidence supports preserving sequential behavior, not claiming the manifest path is inexpensive or that every future workload has the same ceiling.

## 0.6.0 release finalization — 2026-09-17

The owner accepted Image Archiver v1 and its UI review. KEEP CURRENT SEQUENTIAL DESIGN is final. The research-only tools/image_performance.py and tests/test_image_performance.py were removed for release; the measured evidence above remains historical documentation. No benchmark, image-core instrumentation, concurrency implementation or performance promise is shipped.

Release-only changes update the single product version to 0.6.0, public feature/security/architecture notes, the stale GitHub runtime statement, and Windows LICENSE packaging. The accepted seven image runtime modules and text semantics are frozen. Text cache schema 4, image manifest schema 1 and FORMAT=WEIBO_AI_1 remain unchanged.

Authoritative offline commands remain explicit: tests/run_tests.py covers 70 text/application groups, while unittest discovery separately runs 96 image-core and 41 image-UI cases. All 207 passed, as did the environment check and git diff --check. Four existing release-version assertions were updated to 0.6.0; the UI baseline guard permits exactly those substitutions while preserving all other text-test content and every golden.

The normal BUILD_WINDOWS.bat path produced the fresh 0.6.0 onedir bundle and WeiboTextArchiver_Windows.zip. ZIP integrity, required directories/files, exact root LICENSE bytes, private/runtime-content exclusions and the frozen module inventory passed independent auditing. A synthetic ZIP missing only LICENSE was rejected by the packaging verifier. No research document, benchmark module, test, screenshot, personal archive or credential was included in the user bundle.

The packaged title was observed as “Weibo Text Archiver · 0.6.0”, and “图片备份…” opened its window normally. No backup was started during this release smoke. The accepted 71-image UI/download/resume evidence and the single 50-image performance baseline were reused without additional Weibo traffic for release ceremony. Public release notes describe the limited independent image tool without universal-completeness, original-quality, video or performance claims.
