---
name: music-video-subtitle
description: 为单曲MV、官方Music Video和短篇音乐影像完成片源取得、官方字幕与网易云歌词对照、中文逐事件匹配、时间轴复核、Whisper口语与SOFA演唱机器对齐、候选人工复对、字幕预览、压制验收、发布文案、现成官方封面、发布待提交检查和核验归档清理；处理MV字幕、歌词匹配、时间轴、该MV的发布准备或B站已有官方投稿的中文歌词弹幕补充时使用
---

# 单曲MV双语字幕2.19.2

## 最短入口

先读当前项目实际生效的`AGENTS.md`和`.subtitle-runtime.json`，随后只读本页和当前分支所链接的参考文件。本任务已经读过且没有变化的规则不重复读取。常规执行直接用公开CLI；只有参数不明或报错时才查对应函数、测试与运行时细节，不在开局通读脚本或预检全部模型

单首MV使用本Skill；多首歌连续演出、长MC和全场覆盖审计使用`concert-subtitle`。每支MV在外部项目根的`workspace/mvs/<mv-id>/`工作，不把媒体、环境或模型写入Skill仓库

B站已有经核实的官方投稿、用户选择不另行发布字幕视频时，进入下方[B站歌词弹幕](#b站歌词弹幕)备用流程。复用已有歌词与时间轴，只补齐必要材料，不进入常规压制、封面和重新投稿流程；其他已授权平台的工作按原范围执行

### 按任务选路由

以下均为本Skill随包提供的功能，不要求安装某台电脑上的平台Skill。已配置的平台工具或Skill可承担实际查询和发布；先核对它们当前支持的能力，使用本包规定的输入、输出和回读标准

|本次任务|本包入口|交付|
|---|---|---|
|制作MV字幕|下文官方字幕、机器对齐与人工门禁分支|字幕、视频与实际检查结果|
|B站歌词弹幕|[歌词弹幕规则](references/publish-handoff.md#b站歌词弹幕)|目标与时间表、实际发送及公开回读结果|
|微博文案|[文案格式](references/publish-copy-format.md)|标题、正文、来源与制作署名、话题、独立章节评论|
|B站文案|[文案格式](references/publish-copy-format.md)|标题、简介、来源字段、制作署名、标签、独立章节评论|
|小红书文案|[文案格式](references/publish-copy-format.md)|标题、正文、来源与制作署名、话题、独立章节评论|

完整MV任务保留现有视频号交付，用户指定平台范围时沿用其范围。只做文案或弹幕时不重跑已完成的字幕、压制和封面；发布动作沿用本次授权

```powershell
$env:MV_SUBTITLE_PROJECT_ROOT = "<mv-project-root>"
$pipeline = "<installed-skill-dir>\scripts\mv_pipeline.py"
$mvDir = Join-Path $env:MV_SUBTITLE_PROJECT_ROOT "workspace\mvs\<mv-id>"
python -B $pipeline download --mv-dir $mvDir --url "<official-url>"
# 已有可靠片源则使用import-source，不重复下载
```

当前工序自动进入绑定的最小Profile：下载只检查`youtube-download`，候选复核和媒体处理只检查`media`，没有官方字幕时的机器演唱才检查`song`，自然口语才运行Whisper。不临时补PATH或另找Python；实际缺失组件时才查[运行时发现与安装](references/runtime.md)，安装依赖与模型须按用户既有授权或确认的位置执行

下载固定使用系统共享`ytdlp-global --auth auto`，优先匿名，必要时仅使用全局入口的专用隔离登录态，不读默认浏览器Cookie、不临时切客户端。保留最高4320p范围内的原选视频和字幕；若音轨不兼容MP4直拷贝，在本次下载中通过同一入口取得同视频的官方兼容音轨并无转码封装。通过核验的片源唯一保存在`review/preview/`，不保留额外原视频副本，视频不降清晰度。具体取源合同按需见[取源范围](references/workflow-start.md)和[YouTube取源](references/youtube-acquisition.md)

## 有官方字幕（不限语言）

用户指定采用官方中文字幕时，优先使用已核实的官方中文轨；与原唱语言轨逐事件核对正文对应及时间，不以网易云或模型译文替换，公开署名为`中文歌词来源：官方YouTube中文字幕`。用户明确授权确认官方中字后直接出片时，核实对应关系后可进入出片流程

只读[官方字幕与网易云匹配](references/official-subtitles.md)。`download`查询并下载各语言的人工字幕，英文、韩文等同样可以提供原文与初始时间轴，不要求包含日文字符；唯一轨可直接进入复核，多轨先按实际演唱语言选择，不能因歌手是日本人就固定优先日语。可在source/language.txt填写已核实语言代码，或用official-subtitle-prepare --subtitle明确选轨；无法确定时列出候选，不转入缺字幕的机器对齐分支。原文正文和顺序冻结，时间允许按实际演唱审定修订；网易云提供中文与相对时段参考，不能直接替换MV绝对时间

歌词查询自动提取「歌名」及常见视频后缀、清理频道名末尾的官方频道标识，保留`source/title.txt`和`artist.txt`的原始证据。核对前5个网易云候选和`netease-match.tsv`，确认中文覆盖与语义；先整首通读review/official-subtitle/review.md，集中检查漏译、错译、跨行语义和重复句；正确中文不为换文风而重写。少量改动使用line/japanese/chinese三列TSV的--edits一次提交，也可填写`chinese.tsv`并署名本次实际模型

```powershell
python -B $pipeline official-subtitle-review --mv-dir $mvDir --translation-model "<实际模型名>"
# 中文全部为高置信网易云匹配时省略--translation-model
```

该命令合并中文回填、候选生成与校验，并同时完成独立的源画面抽样。返回可编辑的`review/alignment/candidates/official-subtitle.srt`，固定复用预览目录中的唯一片源，不为每次复核另建文件夹。完整源视频和同名SRT位于`review/preview/`，源画面总览位于`review/source-visual-audit/contact-sheet.jpg`。不修改审定`master.ass`、不生成正式ASS、不自动批准；原有单步CLI保留供定点调试

## 没有可用官方字幕

歌词查询先核对接口正文与解析结果，按[歌词返回核对](references/alignment-workflow.md#歌词返回核对)区分接口失败、空正文、纯文本和带时间歌词；不得把解析器统计的0行直接报告为平台无歌词

只读[机器对齐与候选隔离](references/alignment-workflow.md)，先取得歌词正文，再运行`build-candidate`。网易云无时间标签的纯文本也是可用正文，原样保存在`lyrics/original.lrc`，送入对齐前只去除元信息和明确的段落标题，保留重复句，不伪造时间；先确定歌曲身份再检查歌词可用性，正确歌曲缺歌词或缺中文时不得改用其他歌曲。已知歌词的演唱固定BS-RoFormer全长分离加纯SOFA时间轴；网易云时间不得输入机器对齐，分离失败不得降级到原混音。Whisper只处理自然口语，三路比较仅在明确要求时运行

机器候选生成后按[完整代表段与结构推广](references/structure-timing.md)校准完整一番或二番并推广到全曲，保留差异审计；疑似原生歌词卡须在审定前补完整逐句画面对照

## 人工门禁与出片

先交付完整视频与同名候选SRT供人工复对。仅在用户明确选定候选后直接建立`subtitle/master.ass`，不再生成master.srt，已有人工主字幕不得被候选覆盖。用户已确认候选并明确要求直接出片时，此授权覆盖正式预览、压制和机器验收，不再重复问样式确认

到本阶段只读[样式、压制与交付](references/delivery-workflow.md)，涉及样式异常才查[质量门禁](references/quality-gates.md)

```powershell
python -B $pipeline finish --mv-dir $mvDir --candidate "<已确认候选.srt>"
# 已有master.ass时省略--candidate
```

`finish`自动逐阶段打印耗时和成品规格，保存`review/final-vNN-timings.md`，沿用机器验收报告与交付状态完成回读，不另写逐曲计时或回读脚本。下载耗时按source-acquisition.json分别列出主片源和兼容音轨补取调用；说明是否含启动、解析和首播等待。复用片源的重跑须明确标为不含下载，不得称为全流程测速。默认x264 medium/CRF18不变；只有用户要求硬件编码试验时才按[压制参数](references/delivery-workflow.md)显式启用NVENC，未经体积和画质对照不切换默认

`finish`依次建立主字幕、生成标准ASS与完整外挂预览、递增版本压制、验收并落盘已就绪的文案和封面。字幕样式固定中文上方暖黄80号、原文下方白色57号、不自动折行，超宽只水平压缩且低于55%退回调整。音频必须直拷贝，视频尺寸保持片源，禁止为通过压制偷换音频转码

成品完整解码、尺寸时长和音轨内容校验后生成8张典型抽帧及`review/final-vNN-contact-sheet.jpg`。先集中查看总览和封面；文字太小或有疑点再打开对应原帧。真实播放、音画同步和拖动检查仍必须实际执行，不能用抽帧代替或自动勾选

片源只保存在`review/preview/`，下载侧文件与来源说明仍留在`source/`；候选SRT和正式ASS轮流配合同一个视频，不复制片源到其他工作目录。正式预览时将同名候选SRT改名保留，避免播放器误载；机器候选重跑不改审定ASS。旧项目的master.srt可通过style转换为ASS，旧人工文件保留，不批量覆盖或清理既有工程

## 并行准备与交付

下载、歌词匹配期间可同步准备独立的官方发行信息、文案和封面；finish在压制与验收期间自动准备YouTube封面，执行工具返回运行中会话后，利用等待编码的时间完成同一份work/publish-copy.md（完整写入临时同级文件再原子替换，避免读取半稿）。不要先空等压制再写文案；素材较晚就绪时只运行stage-delivery补齐，不重压正片；同一文件的生成与读取保持依赖顺序。尽量一批读取当前工序需要的字幕、来源说明和画面，不反复查询同一状态，不重复研究已核实材料

文案集中于`work/publish-copy.md`，默认微博、小红书、B站、视频号；用户明确的平台范围优先。按[MV文案格式](references/publish-copy-format.md)输出标题、正文、来源和话题，各平台正文分别包含真实Token用量及口径、使用模型及用途、制作耗时、`使用技能：ec-subtitle-skills`。歌词来源仍原样引用`subtitle/chinese-source.txt`；中文主体并非模型翻译时不将校对模型写成翻译来源，实际使用模型另列于制作署名。用户修改过的正式output文案优先于旧草稿

微博、B站、小红书各附独立`章节评论：`字段，至少准备`00:00 开场`和结束段实际起点，便于评论与原生章节编辑。B站简介不包含章节表。发布时默认提醒三平台可以编辑章节，按项目章节流程检查、保存并重新打开核验；文案里的章节文字不代表平台章节已保存

标题前缀、正文段落和话题筛选按当前项目AGENTS.md及其指定的本地发布约定执行。写文案和发布前读取[MV文案格式](references/publish-copy-format.md)，核实内容事实、话题与活动的实际关联及采用条件；没有项目专属约定时使用通用字段结构

YouTube来源默认采用本支官方视频的source/source.webp等缩略图，保留原素材尺寸、构图与比例转换为work/cover-source.jpg；缺失时只补取同视频高清缩略图，不搜索歌曲封面、不从MV截图。默认交付原比例封面，最短边至少720px，不放大低清图；项目明确要求其他比例或多版时按该规格准备并逐版检查，不适合裁切时报告待调整，不强裁或拉伸。具体落盘和其他来源规则见[封面来源](references/cover-sources.md)

本地交付包括同版本MP4、ASS、文案、封面和验收记录。回读`delivery-status`并如实列出缺项；人工检查全完成后运行`delivery-check`。上传或待提交仅在用户要求时读取[发布流程](references/publish-handoff.md)，最终发布按明确授权执行。工作流以文件为事实，不新增状态机、批准令牌或通用哈希清单

## B站歌词弹幕

这是B站已有官方投稿且不另发字幕视频时的备用交付方式；用户明确指定其他已有稿件补弹幕时，按其目标执行，不将普通转载称为官方投稿。先核实目标及已有弹幕，定稿整曲中文与发送时间表，再按12秒间隔发送、公开回读核验，最后准备动态转发提示。默认仅中文、底部25号暖黄色；双语须另有要求，日语使用默认白色。复用本期审定歌词和已核验时间轴，密集短句按语义合并，不直接逐行照搬SRT

发布默认直接接口，浏览器界面操纵为备用线路；实际发送、有限补发、动态文案与授权边界按[歌词弹幕规则](references/publish-handoff.md#b站歌词弹幕)执行。日常执行只使用已有且已验证的入口，缺少能力时报告具体阻塞，不现场开发脚本或适配器

## 归档与清理

归档前必须读取当前项目AGENTS.md中的本地归档约定及其指定的权威规则，核对实际内容分类、固定归档位置、文件夹命名和保留范围。用户已定义这些约定时直接沿用，不能因CLI使用路径占位符就临场选择目录，也不能按使用了MV流程将多曲Live误归类为单曲MV。个人盘符只留在本地项目规则，不写入公开Skill

保存本任务成品和必要工程资料并核验，按[归档步骤](references/delivery-workflow.md#交付检查与归档)执行。是否清理工作目录以及具体范围由项目规则或本次用户指令确定，已有明确授权直接沿用。唯一源文件、人工修改及必要材料先保存；工作区外的原始素材和其他任务保持原位。未完成的人工验收与实际发布状态如实保留，归档不补勾
