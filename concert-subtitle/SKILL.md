---
name: concert-subtitle
description: 制作多曲演唱会、配信Live和现场录像的日中双语字幕，包括歌单与歌曲/MC区间识别、歌词获取、分段对齐与听写翻译、审核、全场合并和终版压制
---

# 演唱会字幕

从整场视频制作日中双语字幕，按“歌曲与MC分别处理、逐段审核、全场合并”执行

默认先按歌曲与MC切割，再逐段完成整段字幕，不设置打样、代表段校准或结构推广前置步骤。某首歌完成后，若存在适合复用的结构，再仅针对这一首歌处理，不要求其他歌曲等待或补做样段

## 接到任务后

先读取当前项目规则和已有工作记录，确认源视频、单场工作目录及已完成阶段；已有可用结果时从当前阶段续做

### 当前版本与项目记忆

演唱会字幕的现行工作流以当前版本的本入口及其引用资料为准，并结合对应脚本和测试核实实际能力。历史项目记忆、复盘、旧版本实验和未实施方案只作背景，不得覆盖当前流程；发现冲突时停止沿用旧约定，不为迁就记忆回退当前版本

项目记忆只保留单场事实、素材来源、人工修订和实际交付状态，不重复维护会随版本变化的样式参数、审查限制、格式要求或实现状态。用户明确要求更新记忆时，删除其中与当前版本冲突的约定；后续流程调整只在本入口及受影响引用中维护，保持一致。历史个案成功不代表已有通用能力，方案不代表已经实现；用户在当前任务中的明确要求优先

用户提供源视频，已有歌单、区间、歌词或人工字幕则直接利用。歌单查找、歌曲/MC区间识别和缺省歌词获取本来就是本Skill的工作，不要求用户为开工准备JSON。`sections.json`、Manifest和分段工作包在制作过程中维护

只有现有资料和音画证据仍无法确定歌曲身份、片源或关键区间时，才就具体疑点询问用户。不能因为底层脚本需要`--sections`，就把生成区间文件的工作交回用户

## 制作顺序

|阶段|处理内容与工具|产出与进入下一步的条件|
|---|---|---|
|1.开工准备|查询本场资料，分析源片报幕、字幕和画面布局；读取运行时绑定，运行`runtime_manager.py doctor`和`concert_preflight.py`|本场资料与来源、素材分析和字幕布局决定、独立FLAC|
|2.识别整场内容|查找歌单，结合歌单、独立音频和必要画面识别歌曲/MC及其他区间|歌单、带证据和置信度的区间文件；疑点明确标记|
|3.建立分段工作包|`index_concert.py`和`build_work_packages.py`；缺少歌词时自动调用`lyrics_source.py`|完整Manifest、分段音频、歌词与输入契约|
|4.制作分段字幕|歌曲做已知歌词对齐与翻译，MC做日文听写、断句与翻译；GPU任务进入单槽队列|逐段`events.json`、`report.json`和时间证据|
|5.检查与审核|歌曲扫描歌词外讲话，MC评估复杂度；运行`validate_section.py`、`build_review_clips.py`并实际审核|每首歌曲与MC的完整分段视频、配套字幕和审查决定；自动检查通过仍为`needs_review`|
|6.全场合并|全部分段批准，完成覆盖审计后运行合并与全局验收|全场双语ASS、独立歌曲进度图形层和验收报告|
|7.终版交付|预览确认后一次压制；获得归档、清理授权后调用内置归档工具|终版ASS与MP4、外部归档校验结果|

详细步骤、命令、状态失效和恢复规则见[工作流](references/workflow.md)，各项硬检查和审查点见[质量gate](references/quality-gates.md)

## 1.开工准备

在已有歌单查询中一并核对本场日期、场地与城市、乐队成员及本场支援乐手、乐器分工、嘉宾与登场环节、MC涉及的专名和地名。优先使用演出官网、主办方或场馆公告、官方演职员表及可靠现场报道；区分常设成员与本场阵容，不凭听写猜姓名，也不把未查到嘉宾写成无嘉宾

同时分析当前片源的版本、完整性、音轨、已有字幕、歌名报幕、台标／LIVE角标及黑边，确定哪些原片信息无需重复叠加、字幕安全区域和进度条的位置与尺寸。开工记录集中写入`input/concert-context.md`，保留来源链接和必要的画面时间点；具体记录内容和图形配置见[工作流阶段A](references/workflow.md#阶段a开工准备)

### 运行时与媒体预检

业务代码随Skill发布，Python环境、模型、CUDA和FFmpeg保存在项目外部运行时。先读取当前项目`AGENTS.md`和`.subtitle-runtime.json`，以下命令在Skill目录执行

```powershell
python scripts/runtime_manager.py doctor --project-root "<concert-project-root>" --task prepare
```

歌曲或MC进入本地识别前，分别检查`--task song`或`--task mc`。当前两个任务使用Whisper Profile，`prepare`只检查FFmpeg与ffprobe；需要Profile的命令通过`runtime_manager.py run`启动

已有唯一可用运行时就复用；缺失或候选不唯一时按[运行时发现与安装](references/runtime.md)处理。仍缺依赖时报告具体内容、预计空间和候选位置，获得确认后才安装。不得在单场工作区创建虚拟环境、模型缓存或工具副本

源片先检查随机访问，失败时生成并复检整场预览代理。通过后提取独立单声道16kHz FLAC，后续ASR、对齐和音频定位只读独立音频

```powershell
python scripts/concert_preflight.py --source "<source-video>" --concert-dir "<concert-dir>"
```

Manifest中的`review_media`绑定已验证源片或预览代理；独立FLAC与终版压制始终绑定原片

## 2.识别歌单与区间

整理歌单及来源，再按[区间识别模板](templates/section-indexing.md)识别区间。结合歌单顺序、独立音频、章节或转场画面判断边界，标记低置信、相似副歌、返场和纯伴奏，不把候选当作已经确认的区间

全场内容必须覆盖至片尾，不能只处理歌单上的歌曲。识别结果和节目说明写入单场工作目录；字段与时间基准见[分段契约](references/section-contracts.md)

完成预检和区间识别后建立Manifest与工作包

```powershell
python scripts/index_concert.py --concert-dir "<concert-dir>" --source "<source-video>" --audio "<independent.flac>" --sections "<sections.json>" --setlist "<setlist.txt>"
python scripts/build_work_packages.py --concert-dir "<concert-dir>"
```

如果开工时已持有可靠区间，可用`concert_pipeline.py prepare`串联预检、Manifest和工作包生成，命令见[工作流](references/workflow.md)。`prepare`消费识别结果；歌单查找与区间识别在前面的识别步骤完成

缺少`lyrics_path`时入口按歌名和歌手自动查找，网易双语优先、无原文时回退LRCLIB。原文与中文分开保存，用户提供文件保持只读；查找失败的歌曲保留诊断并补齐歌词后继续，不能以ASR正文替代已知歌词。候选选择、文件位置和登录态使用规则见[工作流](references/workflow.md)

## 3.分别制作歌曲与MC

每个分段的输入、字幕和证据保存在对应的`run/sections/<section-id>`目录，字段与文件边界见[分段契约](references/section-contracts.md)

- 歌曲：读取[歌曲模板](templates/song-subtitles.md)，已知歌词决定正文与顺序，ASR、日文读音匹配和人声边界提供时间证据；完成日中双语事件后扫描开场前、间奏和收尾的歌词外讲话
- MC：读取[MC模板](templates/mc-subtitles.md)，先确定日文听写和自然语句边界，再翻译中文；初稿后回听音频并记录复杂度，高风险段连续回看完整音画并人工重建话轮

本地GPU任务全部通过`gpu_queue.py`且并发固定为1。`run_section.py`负责将实际处理命令接入队列和分段状态管理，用法见[分段契约](references/section-contracts.md)

事件时间相对正式分段起点，不相对带上下文音频起点。已知歌词、源媒体和人工修订字幕保持只读，生成物写入当前单场目录

现场删唱、改词或重复唱分别使用显式歌词省略、`canonical_source_text`或`role: lyric_adlib`记录，绑定连续音画人工证据并重新全段复核。不得直接改写原歌词文件

每首歌先完成全曲歌词对齐、翻译和歌词外讲话处理，再交付完整分段检查。默认不生成`structure.tsv`或代表样片，不调用结构推广工具，也不以缺少推广记录阻塞批准

某首歌完成后发现合适的对应结构时，可按[单曲结构复用（可选）](references/structure-timing.md)仅处理该曲，复用已完成的时间轴，不再安排统一打样。歌词或旋律不同不强制复用，候选必须核对目标音频并保留人工原稿；实际应用后重新执行该曲gate与审查

歌词边界建议、可选的逐字重复块微调和人工终版误差评估见[歌曲精校与误差评估](references/song-refinement.md)

## 4.逐段检查与审核

歌曲使用`detect_embedded_speech.py`生成讲话候选，复核日文并补齐中文后才以`role: speech`加入事件。MC先运行`assess_mc_complexity.py`，评估至少包含音频回听证据；具体命令见[工作流](references/workflow.md)

3人以上、交叠、快速抢话、说话人不可靠、画外音或人群插话任一情况均按高风险MC处理。高风险自动重排只作草稿，正式事件必须连续音画复核、重建日文话轮与语句后重做翻译

```powershell
python scripts/validate_section.py --section-dir "<section-dir>"
python scripts/build_review_clips.py --concert-dir "<concert-dir>"
```

歌曲与MC均生成并审核完整分段，不用一个代表样片代替整段。逐段视频为重新编码且PTS归零的无字幕MP4，禁止用`-c copy`裁剪。分段和整场预览默认最高540p、H.264视频目标550kbps／峰值700kbps、AAC双声道80kbps，保持原帧率与时间轴，配同名外挂ASS；以能看清字幕和核对内容为准，不默认提高画质或烘焙字幕

字幕正文不得包含制作备注或占位提示，包括“待确认”“听不清”“前半句待确认”“後半要確認”等；这些信息只进入工作记录。ASS和SRT导出会拒绝常见核对占位标记，不能用删除标记代替内容核实

检查材料直接生成到唯一用户入口`<concert-dir>/待检查`，用户指定位置使用`--output-dir`。分段MP4与ASS同名配对，不再写`run/review`后复制，不默认附加SRT。`events.json`是内部底稿，不作为审核交付，不另建`待校对`。来源、范围及视频哈希一致时复用视频，只更新未被人工修改的生成ASS；人工修订不覆盖，需保留两轮时使用明确目录

需要挂回整场视频时，使用`build_review_clips.py --concert-dir "<concert-dir>" --mode source`，在`待检查/整场候选`导出未批准的绝对时间ASS和打开方式说明，复用已验证源片或代理，不复制视频。缺失分段明确列出，不改变正式合并门禁。详见[待检查文件整理](references/workflow.md#待检查文件整理)，内部记录留在`run/evidence`或`run/runtime`

用户要整场预览视频时使用`--mode preview`，在`待检查/整场预览`生成轻量MP4及同名ASS，并按素材配置叠加歌曲图形。只改字幕时复用视频；人工修改过的ASS保持只读，不直接覆盖

实际完成审查后，使用`review_section.py`记录决定，审查点和命令见[质量gate](references/quality-gates.md)与[工作流](references/workflow.md)。自动gate通过不代表人工已审核；不能只在聊天中宣布批准，也不能把事件数、零重叠或静态帧当作MC观看质量或连续音画确认的证据

## 5.全场合并与交付

先处理歌曲讲话候选和Manifest外时间区间的裁决，再运行全场覆盖审计。新场使用`full_timeline_asr`策略，覆盖结果必须与当前Manifest哈希一致

```powershell
python scripts/concert_pipeline.py status --concert-dir "<concert-dir>"
python scripts/audit_concert_coverage.py --concert-dir "<concert-dir>"
python scripts/merge_concert_subtitles.py --concert-dir "<concert-dir>"
python scripts/validate_concert.py --concert-dir "<concert-dir>"
python scripts/song_progress.py review --concert-dir "<concert-dir>"
```

全部分段为`approved`才允许正式合并。合并生成双语字幕ASS与独立歌曲进度图形层，预览使用1份合并外挂ASS和时间窗口表。预览、压制前重新核对批准记录、事件、正式ASS及Manifest；任一过期则返回受影响阶段

统一交付样式：中文在上、暖黄色80号；日文在下、白色57号；超宽行保持字号并仅水平压缩。歌词保留原有标点，MC不使用逗号或句号。歌曲细进度条按歌单等宽分段、已完成段常亮、当前段逐渐填充；位置与尺寸按开工素材分析配置，避开原片报幕和角标。原片已有歌名报幕的歌曲不再叠加歌名和歌手，其他歌曲仅开头短显；MC不显示图形，不显示数字序号、百分比或剩余时间

预览确认后，将字幕和图形层一次压制进终版视频

```powershell
python scripts/song_progress.py render --concert-dir "<concert-dir>"
```

交付ASS与`<concert-id>.hardsub.mp4`集中在`output`，内部事件、报告和图形层在`run/evidence`，运行中间文件在`run/runtime`。交付时直接说明完成到哪一步、实际文件位置和仍待处理的问题

归档与清理使用`archive_concert.py`调用Skill内置`verified_archive.py`，命令与前提见[工作流](references/workflow.md)。复制后核对完整清单与SHA256；删除单场工作区前再次验证外部归档，分别遵守用户对归档与清理的授权

## 续做与故障处理

输入或结果变化使旧批准失效：段内变化重做该段，区间边界变化同时复核相邻段，源媒体或音轨变化影响全部分段。已批准且输入未变化的段不重复执行

状态机及失效范围见[工作流](references/workflow.md)，依赖、错段、字幕提前、预览与MC问题见[故障处理](references/troubleshooting.md)，词级时间与读音匹配内核见[字幕内核](references/subtitle-core.md)
