# 测试用户｜AI 分析版
FORMAT=WEIBO_AI_1
导出：测试导出20条｜终止=达到指定条数｜快照（导出机器本地）=2026-08-13 02:00

简介：V7 模型→渲染器回归样本
资料：UID=1234567890｜粉丝=321｜关注=45｜微博=3｜所在地=北京
摘要：共3条｜原创2｜转发1｜范围=2026-08-11 06:24~2026-08-13 00:10｜含图1条/3图｜唯一转发原文1｜重复原文省略0次
格式：W=顶层记录；RT*=转发原文编号；S*=发布来源；P=发布位置；I=图片数 V=视频媒体数 A=头条文章；R=转发 C=评论 L=点赞。
STRUCTURE: W is a top-level rendered record; ORIGINAL/REPOST means absence/presence of a structural nested RT, not character-level authorship.
ATTRIBUTION: RT is a nested repost-source record attributed to its recorded author metadata; SELF requires exact non-empty UID equality. W may contain preserved quoted text.
TEXT_CHAIN: //@ text is preserved but unparsed and cannot verify identity or attribution.
P: Weibo-displayed publication-location metadata for its containing record; P alone does not prove event location, residence, a specific visit, or timezone.
TIME: known source offset is preserved; no offset means timezone provenance is unknown; TIME is not necessarily the target account's local civil time.
ENGAGEMENT: R/C/L belong to their containing W or RT; UNKNOWN is not zero.
MEDIA: I/V/A belong to their containing record; referenced media is not included, so text may not be complete context.
CONTENT: PREVIEW_ONLY is INCOMPLETE and not full text; TEXT=EMPTY is a verified complete empty body, not unavailable content.
SOURCE_IDS=file-local: S* and RT* identifiers apply only within this file.
REFERENCE: RT* emits one repost source; =RT* is an explicit lossless file-local reference to that already emitted source.
AGGREGATES: total/original/repost/range/media describe top-level W records; unique/duplicate RT counts describe nested RT identities.
来源字典：S1=iPhone客户端；S2=微博网页版

[W｜2026-08-13 00:10｜S1｜R=1 C=1 L=5]
这是转发时写的评论。
>[RT1｜@原作者｜I1｜R=12 C=8 L=99]
> 这是被转发的原文。

[W｜2026-08-12 18:30｜S1｜P=上海｜I3｜R=3 C=0 L=21]
带图片的微博。

[W｜2026-08-11 06:24｜S2｜P=北京｜R=UNKNOWN C=2 L=10]
第一条原创微博。
