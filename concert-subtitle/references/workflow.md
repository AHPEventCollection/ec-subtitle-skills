# 工作流

本页记录制作步骤和恢复规则，入口见[SKILL.md](../SKILL.md)。命令在Skill目录执行，所有单场路径均位于Skill目录外

先完成歌单查找和区间识别，脚本接收这些步骤生成的文件。用户已有歌单、区间或歌词时直接利用；缺少时由Skill继续完成，不要求用户编写JSON

## 目录

- 阶段A：开工准备
- 阶段B：区间清单
- 阶段C：工作包
- 阶段D：分段执行
- 阶段E：审查
- 阶段F：全场合并
- 阶段G：多平台文案
- 阶段H：外部归档与清理
- 状态机
- 恢复规则

## 阶段A：开工准备

先把本场资料查询和素材分析写入`input/concert-context.md`，沿用已有资料，新增事实附来源链接

|内容|准备结果|
|---|---|
|演出身份|演出名称、实际日期、场地、城市及片源版本；区分演出日、播出日和制作日|
|演出阵容|乐队成员、本场支援乐手、各自乐器／分工、嘉宾及登场环节；使用官方拼写，历史演出按当时阵容核对|
|歌单与语境|本场歌单、返场、合唱或串烧，以及MC会涉及的人名、地名、作品名；查询无结果或来源有冲突时据实记录|
|素材状况|时长、分辨率、帧率、音轨、缺段／广告／片尾、软硬字幕、原片歌名报幕及其覆盖歌曲|
|画面布局|台标／LIVE角标、黑边、报幕和已有字幕的位置，保存必要的画面时间点，确定双语字幕安全区及进度条位置、宽度、厚度|

查询优先使用演出官网、主办方／场馆公告、官方演职员表与可靠现场报道。成员名单不能直接代替本场登台阵容，未查到嘉宾不代表没有嘉宾；身份与拼写靠资料核实，登场顺序和实际说了什么仍对应现场

将画面分析结论写入区间输入文件的同级`presentation`配置，建立Manifest时保留显式设置并补齐默认值。`progress_bar`采用1920×1080坐标，默认如下

```json
{"x": 0, "y": 8, "width": 1920, "height": 18, "layout": "duration", "mc_weight": 3, "mc_max_song_ratio": 1.2, "mc_label_always": true, "unplayed_alpha": 102}
```

常态块宽按时长×权重分配，MC权重3、其他类型1；MC权重上限为歌曲时长中位数的1.2倍，无歌曲时不施加上限。权重仅影响视觉宽度，不改字幕和真实时间。当前段上下各扩8像素、高34，并在需要时扩宽容纳内嵌名称；切段后恢复常态比例，其他块在剩余宽度中按受限权重分配。细段高18，当前标签24号、两侧各8像素。所有MC块常驻“MC”不编号，非当前MC标签16号居中。歌曲仅当前段显示“1. 歌名”，其他分类保持回顾、过场或MV

未播放区域（含当前段剩余部分）白色且40%透明，`unplayed_alpha=102`（0不透明，255全透明）；已播放区域以金色填满整个块高，当前块连续推进，不另加底部细线。字体Yu Gothic，缺字库时估算宽度并复检；空间不足仅水平压缩。旧报幕字段仍可读取，不隐藏当前段歌名，也不另叠报幕

本默认自0.19.0生效，包括重新生成时没有指定`layout`的旧工程；已有交付文件不自动重写。显式配置继续优先，旧贴字模式可指定`layout: "label_fit"`；旧时长模式需保留原来的`mc_max_song_ratio: null`、`mc_label_always: false`和`unplayed_alpha: 0`时应显式写出。样式变化后重新生成图形并预览，不沿用旧图形验收结果，不改字幕批准记录

运行`concert_preflight.py`

```powershell
python scripts/concert_preflight.py --source "<source-video>" --concert-dir "<concert-dir>"
```

- 使用源视频只做媒体探测和独立音频提取
- 在多个时间点比较直接定位与提前8秒解码所得的连续画面哈希，验证随机访问不会因关键帧或索引异常落到错误画面
- 源片检查失败时先按轻量预览规格转码`run/runtime/source-preview.mp4`，固定每2秒关键帧并重新执行同一检查；代理仍失败或与源片时长差超过0.5秒时停止开工
- 检查通过使用源片作为`review_media`，检查失败且代理复检通过时只用代理生成审核材料；独立FLAC和终版压制始终绑定原片
- 生成单声道16kHz FLAC
- 保存源媒体基本信息、音轨信息和文件指纹
- 后续对齐、ASR和音频定位只能消费提取后的音频

## 阶段B：区间清单

先利用已有歌单、可靠外部资料和片中信息确定歌单及来源，再按[区间识别模板](../templates/section-indexing.md)识别区间，确定每首歌和MC的正式开始、结束时间

全场内容要覆盖至片尾，返场、纯伴奏、动画片段或其他讲话区间也需识别和记录，不能因为不在歌曲工作包中就遗漏。区间证据与置信度随结果保存，疑点仅在工作记录中标记，不写进字幕正文

每段额外保留15至30秒上下文，只用于判断转场、人声起点和尾音。上下文不属于该段正式字幕区间

区间识别可综合：

- 已知歌单顺序
- 人工试听
- 画面章节或提示
- 音频能量和人声边界
- ASR语义证据
- 同曲源素材音频定位

ASR只能提供区间证据，不能单独决定已知歌词正文

建立Manifest时，歌曲已提供`lyrics_path`则保持用户文件只读；缺少路径则按
`title+artist`自动执行网易双语优先获取，原文与`tlyric`分离保存。工具会检查
多个近似匹配候选；网易有原文但没有中文时保留原文并标记缺失，网易没有可用
原文时才回退LRCLIB。全部来源、状态和查询证据写入Manifest

歌词获取沿用以下顺序：

1. 网易候选按歌名、歌手匹配和热度排序，检查匹配分数接近的前5个候选，优先选择同时有原文`lrc`与中文`tlyric`的版本
2. 网易有原文而无中文时保留原文并记录`translation_status:not_found`；只有没有可用原文时才回退LRCLIB，LRCLIB只补原文
3. 自动文件集中写入`input/known-lyrics`，原文为`<歌名>.lrc|txt`，中文为`<歌名>.zh.lrc|txt`；只有原文进入正文与顺序校验
4. 用户提供的`lyrics_path`与`translation_path`保持只读，失败时记录`lyrics_status:not_found`，补齐原词后重建工作包

网易默认可无Cookie请求；需要已有登录态时使用`NETEASE_COOKIE`、`MUSIC_U`、`NETEASE_COOKIE_FILE`，或在入口传`--netease-cookie-file <path>`，Cookie文本不得写入命令行

## 阶段C：工作包

运行`index_concert.py`建立Manifest，再运行`build_work_packages.py`

阶段A、B已经完成时，复用独立音频与区间结果

```powershell
python scripts/index_concert.py --concert-dir "<concert-dir>" --source "<source-video>" --audio "<independent.flac>" --sections "<sections.json>" --setlist "<setlist.txt>"
python scripts/build_work_packages.py --concert-dir "<concert-dir>"
```

开工时已有可靠区间文件，可用现有`prepare`入口串联媒体预检、Manifest和工作包生成

```powershell
$env:CONCERT_SUBTITLE_PROJECT_ROOT = "<concert-project-root>"
python scripts/concert_pipeline.py prepare `
  --concert-dir "<concert-dir>" `
  --source "<source-video>" `
  --sections "<sections.json>" `
  --setlist "<setlist.txt>" `
  --concert-id "<concert-id>"
```

`prepare`不负责识别歌单和区间，`--sections`是脚本的输入契约，不是用户必须提供的文件

每段目录是独立、可重跑的执行边界。输入哈希变化后，旧结果必须视为`stale`

自动歌词查找失败的歌曲仍可保留Manifest诊断信息，但缺少已知原词属于硬gate，
不得让ASR正文或无来源翻译继续进入正式歌曲流程

歌曲事件时间使用相对正式区间起点的秒数。分段音频包含上下文，`input.json`同时记录音频起点和正式区间起点

## 阶段D：分段执行

GPU任务固定并发1项：

- Whisper
- Demucs或其他GPU人声处理
- GPU声学对齐

所有GPU命令通过`gpu_queue.py`执行。优先复用常驻模型进程，避免每段重复加载`large-v3`

MC初稿完成后，必须实际回听音频并运行`assess_mc_complexity.py`。
评估写入`<section-dir>/evidence/mc_complexity_assessment.json`并绑定当前
`input_hash`。分类和处理路径如下：

- `single_speaker`：单人连续讲话，可进入保守词级重排
- `managed_dialogue`：说话人和话轮可靠的双人对话，可进入保守词级重排并完整复核
- `crowded_multi_speaker`：3人以上或多人混杂，进入人工精校

即使分类名称不是`crowded_multi_speaker`，只要出现交叠、快速抢话、说话人不可靠、
画外音或人群插话，风险仍提升为`high`，自动重排策略固定为`draft_only`。
评估证据必须包含连续音画或音频回听；静态画面和ASR词时间戳只能作辅助

例如，实际回看确认多人交叠与快速抢话后记录评估；分类、人数和证据参数必须据实填写

```powershell
python scripts/assess_mc_complexity.py `
  --section-dir "<section-dir>" `
  --classification crowded_multi_speaker `
  --speaker-count-estimate 4 `
  --signal overlapping_speech `
  --signal rapid_turn_taking `
  --evidence-basis continuous_av_review `
  --evidence-basis asr_word_timing
```

歌曲歌词事件完成后，使用正式ASR转录运行`detect_embedded_speech.py`。
该步骤只生成`speech_candidates.json`，用于发现开场前、间奏和收尾的歌词外讲话。
候选必须先复核日文并补齐中文翻译，再以`role: speech`加入歌曲事件；
不得把未复核候选直接并入正式字幕

```powershell
python scripts/detect_embedded_speech.py --concert-dir "<concert-dir>" --section "<song-section-id>"
```

歌曲按完整分段直接完成对齐、翻译和歌词外讲话处理，不设置打样或结构推广前置步骤。某首歌完成后确有合适结构，再按[单曲结构复用（可选）](structure-timing.md)单独处理该曲；其他歌曲不等待。边界建议、可选逐字重复块微调和人工终版对照见[歌曲精校与误差评估](song-refinement.md)

多人歌曲接着执行[演唱归属](singing-attribution.md)，从已确认独唱参照结合实际音画判断单人、句内接唱、合唱和未知。`singing_parts`与原文逐字对应，渲染器只给已确认单人短句上色，中文和时间保持不变；既有工具的声纹分数仅作候选。没有模型或听辨能力时明确保留未知，不用结构推测或静态镜头代替确认

边界和传播先生成`applied:false`提案，正文、顺序与事件数量保持不变；节奏只用于同曲映射和漂移证据。获批传播应用后分段进入`stale`，重新执行gate和审查，旧批准不能复用

全场合并前运行`audit_concert_coverage.py`。每个歌曲候选必须标记为
`approved`、`known_lyric`、`crowd_nonlexical`或`rejected_hallucination`；
Manifest没有覆盖的时间区间必须建立裁决，标记为已批准讲话、纯音乐/人群声、
片尾或无语音。仍有未裁决项时不得视为正式成品

## 阶段E：审查

低、中风险MC可在自动gate之前运行`reflow_mc_events.py`。工具先保守合并有明确未完词尾或continuation证据的相邻短前缀，再以词级时间为边界拆开句号后的独立句、短回答、从句边界和已可靠标注的换人事件。重排只组合或拆开现有日文与中文，不重新听写或翻译正文；split和merge数量只表示结构变化，不表示观看体验改善

MC单条日文超过56显示单位或中文超过44显示单位时自动gate直接失败。生产者必须在审核包生成前结合词级时间、停顿和完整语意拆句；固定字符数切分、只改中文或把长句保留为提醒均不合格

高风险MC运行重排命令时默认返回`skipped_crowded_mc`并保持`events.json`
不变。`--dry-run`可观察草稿指标，但草稿不能进入正式批准。人工精校按以下顺序：

1. 连续播放完整音画，标记说话人、话轮、交叠和画外音
2. 结合停顿、呼吸和完整语意重建日文事件，必要时重新听写
3. 在最终日文断句确定后重做中文翻译
4. 结合发声和画面上下文微调起止时间
5. 重新运行自动gate并记录完整人工审查点

高风险MC批准记录必须同时包含`full_section`、`continuous_av_playback`、
`speaker_turns`、`utterance_boundaries`、`source_transcription_rechecked`和
`translation_after_segmentation`

首版自动检查后，`build_review_clips.py`直接在`待检查`为每首歌曲和每段MC生成完整分段的同名ASS/MP4配对，时间范围直接取Manifest分段起止。不再先写`run/review`再复制视频，来源和哈希记录写入`run/evidence`。无需填写`structure.tsv`或生成代表样片；旧工作包的`structure_review_required`字段也不再触发打样或推广门禁

所有逐段MP4都必须解码后重新编码为H.264视频和AAC音频，并分别用`setpts=PTS-STARTPTS`和`asetpts=PTS-STARTPTS`把片段时间轴归零。禁止stream copy或其他直接按关键帧裁剪方式

歌曲的异常斜率、大于2秒的修正、外推、低置信区间、重复副歌和返场异常应加入受影响窗口复核，但不因此默认生成整首歌曲审核片；MC始终连续播放完整分段

审查结论写入分段`review_decision.json`，不能只在聊天中说明

技术gate验证时间、重叠、正文完整性、显示长度和证据契约。事件数量增加、长句减少、
零重叠或零短闪不能单独证明MC断句自然，也不能作为“精修完成”的结论

人工终版可用`evaluate_subtitle_timing.py`对照机器轴。评估器允许一对多和多对一
文本对齐，并输出首尾时间误差、覆盖率和误差分桶。该报告是后续参数优化证据，
不参与当前分段批准，也不得反向覆盖人工终版

### 待检查文件整理

分段和整场预览统一使用最高540p、H.264目标550kbps／峰值700kbps、AAC双声道80kbps、每2秒关键帧；保持原帧率和时间轴，不放大低分辨率素材。视频保持无新增字幕，配同名外挂ASS，优先保证字幕可读和跳转方便。按目标总码率估算约每小时284MB，2小时40分约760MB，实际大小随编码波动；默认不为预览提高画质

需要整场轻量视频时运行`python scripts/build_review_clips.py --concert-dir "<concert-dir>" --mode preview`，生成`待检查/整场预览/整场预览.mp4`与同名ASS。视频来源、时长、编码规格和文件哈希相符时复用视频，仅刷新未被人工修改的生成字幕。原片字幕与报幕判断、图形配置沿用阶段A的素材分析

面向用户的唯一检查入口是`<concert-dir>/待检查`，交付时直接给出该目录；`events.json`只是内部底稿，不能作为可播放、可校对的交付物，不另建`待校对`

1. 分段模式直接生成`song_02.mp4`与`song_02.ass`等同名配对，默认只生成ASS，不附加SRT。视频重新编码并归零PTS，生成后不复制第二份、不添加另一个full文件名
2. 用户指定位置时传`--output-dir`。源视频文件指纹、分段范围及已生成视频SHA256一致时复用视频，只刷新未被人工修改的生成ASS。人工修改或来源未知的字幕不覆盖，先回灌修订；确需保留两轮时指定`待检查/第2轮`等清晰位置。视频不匹配时也保留旧文件，不自动覆盖
3. 需要挂回整场视频时运行`python scripts/build_review_clips.py --concert-dir "<concert-dir>" --mode source`，无需等待分段批准或视频切片。仅导出已存在事件并按Manifest起点换算绝对时间，不修改事件、批准状态或正式output
4. 整场候选放在`待检查/整场候选`，与分段材料分开；打开方式说明指向Manifest绑定的已验证源片或代理，手动加载候选ASS，不复制整场视频。说明明确缺少字幕的分段，候选不代表完整或通过审核，也不替代逐段审查和正式合并
5. 来源映射及哈希记录写入`run/evidence`，运行中间文件写入`run/runtime`。旧工程已有`run/review`及人工文件不自动搬迁或删除；历史重复材料清理需核对归属与唯一内容后按用户授权执行

### 审核命令

评估允许自动重排时才运行MC重排，随后重新执行技术检查。高风险MC的正式精校仍走前述人工重建流程

```powershell
python scripts/reflow_mc_events.py --concert-dir "<concert-dir>" --section "<section-id>"
python scripts/validate_section.py --section-dir "<section-dir>"
python scripts/build_review_clips.py --concert-dir "<concert-dir>"
```

实际完成对应审查后记录决定，禁止为了推进状态虚填审查点

```powershell
python scripts/review_section.py --section-dir "<song-section-dir>" --decision approve --point full_section
python scripts/review_section.py --section-dir "<mc-section-dir>" --decision approve --point full_section --point continuous_av_playback
```

有`singing_parts`的歌曲在同一次完整审查中核对归属，批准时增加`--point singing_attribution`。技术gate只验证字段与正文对应关系，不证明身份识别准确

高风险MC还必须记录以下审查点

```powershell
python scripts/review_section.py `
  --section-dir "<mc-section-dir>" `
  --decision approve `
  --point full_section `
  --point continuous_av_playback `
  --point speaker_turns `
  --point utterance_boundaries `
  --point source_transcription_rechecked `
  --point translation_after_segmentation
```

歌词边界建议、重复传播、人工回灌和误差评估的完整命令见[歌曲精校与误差评估](song-refinement.md)，它们不替代分段gate与审查

## 阶段F：全场合并

只有全部分段为`approved`时才运行合并器

合并器：

- 将相对时间转换为全场绝对时间
- 保持Manifest顺序
- 检查跨段重叠和越界
- 只把最终压制ASS写入`output`
- 把合并事件JSON和合并报告写入`run/evidence`
- 按整场时间顺序生成独立`song-progress.ass`，覆盖歌曲、MC、回顾、过场和片尾MV；默认顶部全宽1920，细段y=8、高18、间隔4（超过30段间隔2）
- 默认按阶段A的时长比例与MC限宽绘制，所有MC块常驻“MC”；歌曲仅当前段显示“1. 歌名”，其他用回顾、过场或MV与片名。当前块高34、字号24，必要时扩宽，切段恢复；白色未播放区域40%透明，金色按全高度连续填充。不显示百分比或剩余时间
- `presentation.timeline_extras`登记非字幕工作包的回顾、过场或MV区间（type=recap/transition/mv，start/end为全场秒），禁止与sections重叠；其余空隙自动显示过场，不自动推断新歌曲。展示区间不豁免字幕制作、覆盖审计或批准门禁
- 已有工作包按`presentation.section_kinds`以稳定ID覆盖展示分类，例如`{"intro_00":"recap","bonus_17":"mv"}`；不改变section.type或处理/批准路由，也不占用歌曲或MC计数
- 只展示节目部分时配置`presentation.timeline_range`为`{"start":55,"end":6432}`，保留全片绝对时间；范围外区间不显示、不计数，范围内空隙仍显示过场。范围边界不能截断已有工作包，不配置则使用全片范围
- 不单独添加歌名和歌手报幕；当前文字块上下各比细段扩8像素，调整布局须保留画面内边界
- 中文在上并使用暖黄色80号，日文在下并使用默认白色57号，已确认单人短句按本场配色；残余超宽行保持字号并按1700像素安全宽度水平压缩

之后运行`validate_concert.py`做独立全局验收。验收通过后，先用`song_progress.py review`生成合并字幕与图形层的单一外挂ASS和图形检查窗口表，使用Manifest绑定的已验证源片或预览代理检查歌曲开头、中间和收尾的显示。这些窗口只检查合并效果，不替代此前歌曲与MC的完整分段审核，也不触发打样。预览确认后再用`song_progress.py render`读取原片并把字幕ASS与图形ASS在一次ffmpeg处理中压入终版视频。预览和终版压制都重新核对当前批准结果、正式ASS和Manifest

### 终版编码与输出目录

`render`默认`--encoder auto`，依次实测`h264_nvenc`、`h264_qsv`、`h264_amf`，选首个成功者；全部不可用时明确提示并回退`libx264`。指定某个硬件编码器失败则停止；`--encoder cpu`显式使用CPU。硬件试编码及正式压制均经过共享GPU单槽队列，取得队列后再次核对输入；字幕滤镜仍由CPU处理，不宣称整条链路都在显卡上

`--crf`沿用质量值参数名，默认18：CPU映射CRF，NVENC映射CQ，QSV映射global_quality，AMF映射QP；不同编码器的相同数值不代表相同画质。`--preset`仅控制CPU预设，硬件使用各自固定质量预设

在项目根目录或单场目录创建`.subtitle-render.json`，将`output_root`设为用户确认的绝对目录。默认输出为`<output_root>/<concert-id>/<concert-id>.hardsub.mp4`，逐次核对源片与目标的物理磁盘集合没有交集；不同盘符但属于同一硬盘仍拒绝。配置缺失、路径物理盘无法确认或同盘时停止。需单次指定时使用`--output "<absolute-file.mp4>"`，该显式选择不受默认异盘检查限制，仍禁止覆盖源片或已有文件

压制完成后检查H.264视频流、正时长并执行短段解码，再记录`run/evidence/render-receipt.json`的成片路径、编码器、输入绑定与输出哈希；失败不覆盖旧成片，不在中途静默重编码。压制输出目录与正式归档目录分开管理

## 阶段G：多平台文案

字幕交付后进入[多平台文案](publicity-workflow.md)，读取本地编辑记忆和栏目规范，按目标平台分别保存完整标题、正文及话题TXT。默认位置为`output/发布文案`，用户指定位置优先；未核实话题、引用与来源缺口如实保留。明确仅要字幕时不扩展任务，不因准备文案自动上传或发布

## 阶段H：外部归档与清理

压制默认策略见上一阶段，归档入口会同时纳入其外置成片

先读取当前项目AGENTS.md中的本地归档约定及其指定的权威规则，核对实际内容分类、指定归档根目录、文件夹命名和保留范围，再构造`--destination`。不能因下面使用路径占位符就自行选择归档位置，也不能按使用的工具代替内容分类；未完成实验不得标为正式归档

归档保存成品和必要工程资料，唯一源文件、人工字幕及必要材料先保存，工作区外原始素材保持原位。长期保留范围及交付物布局按本地约定执行，完整工作区复制用于防止遗漏，不代表冗余媒体必须长期保留；整理前核对唯一内容和已验证副本

终版硬字幕完成并获得用户归档授权后运行`archive_concert.py archive`。入口先重新执行全局验收并确认
`run/evidence/render-receipt.json`记录的成片已完成且Manifest和成片哈希一致；无回执的旧项目仍检查`output/<concert-id>.hardsub.mp4`。随后调用Skill内置`scripts/verified_archive.py`
复制整个单场工作区，并将工作区外成片纳入归档的`output`。通用层统一处理目标路径类型检查、失败即停、源稳定性检查、
复制后完整清单与SHA256复核、锁和临时残留清理

按当前项目的收尾授权且外部归档确认可用后运行`archive_concert.py cleanup`；本地已明确“归档”“收工”包括清理时，不再重复索要授权。通用层再次核对外部目标和本地
归档源及外置成片未变化后删除当前单场工作区，工作区外成片保持原位。演唱会工作流不维护PowerShell目录创建、复制或
删除分支，也不直接处理`New-Item`参数兼容问题

```powershell
python scripts/archive_concert.py archive --concert-dir "<concert-dir>" --destination "<external-archive-dir>"
python scripts/archive_concert.py cleanup --concert-dir "<concert-dir>" --report "<archive-verification.json>"
```

## 状态机

```text
planned
  ↓
prepared
  ↓
queued
  ↓
running
  ↓
needs_review
  ├─→ approved
  └─→ failed
```

输入变化可使任何已有结果进入`stale`

重复传播应用后也必须进入`stale`，不能沿用旧`gate_report.json`或
`review_decision.json`

## 恢复规则

- 单段失败只重跑该段
- 修改段内参数只使该段变为`stale`
- 修改区间边界同时使直接相邻段变为`stale`
- 修改源媒体或音轨使全部分段变为`stale`
- 已批准且输入哈希未变化的段不得重复执行
- 失败记录保留在`status.json`历史中
