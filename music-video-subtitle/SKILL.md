---
name: music-video-subtitle
description: 为单曲MV、官方Music Video和短篇音乐影像完成片源取得、官方字幕优先、中文逐事件翻译、Whisper口语与SOFA演唱机器对齐、候选人工复对、字幕预览、压制验收、四平台文案、真实画面封面和复制归档；处理MV字幕、官方字幕、无LRC时间轴对齐、Whisper/SOFA/Combined比较或修复时间轴时使用
---

# 单曲MV双语字幕2.8.0

## 运行时入口

业务代码随Skill发布，Python环境、模型、CUDA和ffmpeg统一放在项目外部运行时。开始任务后先读取当前项目的`AGENTS.md`和`.subtitle-runtime.json`，再按路由执行预检：

```powershell
python scripts/runtime_manager.py doctor --project-root "<mv-project-root>" --task youtube-download
python scripts/runtime_manager.py doctor --project-root "<mv-project-root>" --task official-subtitle
python scripts/runtime_manager.py doctor --project-root "<mv-project-root>" --task song
python scripts/runtime_manager.py doctor --project-root "<mv-project-root>" --task whisper
python scripts/runtime_manager.py doctor --project-root "<mv-project-root>" --task compare
```

需要从YouTube取得片源时使用`youtube-download`，它检查base、EJS、FFmpeg、ffprobe及Deno或Node。已有本地片源和官方字幕时只用`official-subtitle`，不要因此检查或安装模型。机器演唱对齐用`song`，自然口语用`whisper`，用户明确要求三路比较才用`compare`

`doctor`发现唯一可用运行时后直接复用。它不仅检查文件，还实际执行FFmpeg、检查separator的CUDA，并用正式SOFA模型建立ONNX会话，输出`READY`、`DEGRADED`或`BLOCKED`及精确组件错误。缺失或出现多个候选时，先运行`plan`并按[运行时发现与安装](references/runtime.md)搜索已有资源；仍缺少时必须把依赖、模型、预计新增空间和候选位置告诉用户，获得确认后才安装并重新运行`doctor`。不得在单支MV工作区内创建虚拟环境、模型缓存或工具副本

`download`、`alignment-prepare`和`build-candidate`会自动通过运行时管理器重启到绑定的base Profile。不得在外层命令临时补`PATH`、另找Python或手工注入FFmpeg目录

公开Skill不依赖本机`AGENTS.md`。运行时需求、公开来源和版本记录在`runtime-requirements.json`，发现与验证逻辑随Skill保存在`scripts/runtime_manager.py`

按“官方字幕优先、中文继承官方时间轴、没有官方字幕才机器对齐、分离失败禁止降级、机器候选隔离、字幕人工审定、预览先于压制、交付只复制不自动删除”执行。工作流以文件为事实，不维护状态机

## 完整任务图

```text
官方链接或已有片源
  → 检查人工制作的官方字幕
  ├─ 有 → 锁定官方正文与逐事件时间轴
  │       → 中文逐条翻译并继承相同起止时间
  │       → 官方字幕候选与人工语义复对
  └─ 无 → 日中歌词正文与角色路由
          → 无外部时间轴输入审计
          → BS-RoFormer分离人声
          → 演唱SOFA／口语Whisper／独立Combined诊断
          → 可编辑候选与人工复对
  → master.srt人工审定
  → 标准ASS与典型场景预览
  → 递增版本压制
  → 机器检查与人工抽查
  → 四平台合并文案与真实画面封面
  → 交付检查
  → 复制归档
```

目标发布平台不是前置输入。发布阶段固定在一个文件中准备微博、小红书、B站、视频号四个平台章节

## 路由边界

使用本Skill处理：

- 单首歌曲MV、官方Music Video、动画或真人单曲影像
- 从官方来源取得片源和歌词后制作日中双语字幕并完成交付
- YouTube片源、官方字幕、封面图和发布文案的一体化交付

以下任务改用`concert-subtitle`：

- 多首歌连续演出
- 多首歌、长MC、返场、歌单分段或多人讲话的Live
- 需要逐段审批、全场覆盖审计或歌曲进度图形层的演唱会

单曲MV不建立歌单、MC工作包、演唱会Manifest、全片ASR覆盖审计或歌曲进度条。短片头或片尾对白仍留在本Skill并路由到Whisper或人工处理

## 建立工作区并取得片源

每支MV使用外部项目根的`workspace/mvs/<mv-id>/`，其中包含`source/`、`lyrics/`、`subtitle/`、`work/`、`review/`和`output/`。设置`MV_SUBTITLE_PROJECT_ROOT`指向该项目根，不把媒体或生成物放进Skill仓库

```powershell
$env:MV_SUBTITLE_PROJECT_ROOT = "<mv-project-root>"
$pipeline = "<installed-skill-dir>\scripts\mv_pipeline.py"
$mvDir = Join-Path $env:MV_SUBTITLE_PROJECT_ROOT "workspace\mvs\<mv-id>"

python -B $pipeline init --mv-dir $mvDir
python -B $pipeline download --mv-dir $mvDir --url "<official-url>"
```

下载页面来自艺人、厂牌、发行方或作品官方频道。命令保存片源、页面字幕、缩略图、标题、艺人和`source/source.md`，禁止播放列表和同名覆盖。优先SDR；检测到HDR时必须更换SDR片源或另行明确转色方案

用户已有可靠片源时，不重复下载：

```powershell
python -B $pipeline import-source --mv-dir $mvDir --source "<source-video>"
```

导入只复制到本支工作区，不改写用户原文件

## 官方字幕优先与歌词回退

下载命令固定使用当前共享运行时的`python -m yt_dlp`、匹配的`yt-dlp-ejs`、已登记的JS运行时及共享FFmpeg目录，不读取用户全局yt-dlp配置。它使用`--write-subs`取得平台提供的人工字幕，不使用`--write-auto-subs`。下载结束后自动检查`source/`中的官方日文VTT、SRT、ASS或SSA

YouTube客户端策略、错误分类和认证边界见[YouTube取源](references/youtube-acquisition.md)。普通公开视频不读取浏览器Cookie。默认客户端发生格式403或挑战解析失败时，脚本内部只执行一次`default,web_embedded`回退；429不继续轮询客户端。所有尝试写入`review/source-acquisition.json`，只有最终成功策略进入`source/source.md`

发现唯一官方日文字幕时，立即以其正文、事件顺序和逐条起止时间为唯一基准，跳过网易云时间轴、SOFA、Whisper和Combined主流程。不得因为官方分句与歌词网站不同而重新切句或改时间

```powershell
python -B $pipeline official-subtitle-prepare --mv-dir $mvDir
```

命令生成`review/official-subtitle/source-cues.tsv`和`translation.tsv`。只编辑`translation.tsv`的`chinese`列，逐条翻译官方日文事件，不得修改`line`、`start_ms`、`end_ms`或`japanese`

```powershell
python -B $pipeline official-subtitle-build --mv-dir $mvDir
python -B $pipeline build-subtitle --mv-dir $mvDir --candidate "$mvDir\review\alignment\candidates\official-subtitle.srt"
```

`official-subtitle-build`逐条比较冻结基准，只有中文全部填写且官方正文、顺序和起止时间完全未变时，才生成`official-subtitle.srt`。先人工复核翻译语义，再将它提升为`master.srt`

发现多份日文字幕或语言无法确认时停止自动选择，使用`official-subtitle-prepare --subtitle <source内字幕路径>`明确指定。自动生成字幕只作参考，不属于本工序的官方字幕

只有确认不存在人工制作的官方字幕时，才进入网易云歌词和机器对齐回退流程

默认按`source/title.txt`和`source/artist.txt`查询网易云，检查搜索结果前5个候选，综合歌名、艺人和中文逐句覆盖选择最可靠结果：

```powershell
python -B $pipeline lyrics --mv-dir $mvDir
```

只有100%覆盖的双语结果才自动继续，并写入`lyrics/original.lrc`、`lyrics/chinese.lrc`和`lyrics/lookup.md`。网易云没有可用结果时固定生成`lyrics/fallback-search.md`，依次使用UtaTen和TuneCore Japan，按“艺人加完整歌名→完整歌名→可辨识首句”的方式搜索并保存采用URL。查询不完整时保留候选审计，直接核对正文并补齐`chinese.lrc`

日文歌词决定正文和顺序。网易云时间轴只作机器候选生成后的人工参考，不得进入Whisper、SOFA、Combined或内部切段。必须对照MV实际演唱检查改词、重复、语气词和间奏，ASR只提供口语或疑点窗口的音频证据，不替换歌词正文

不存在官方字幕时，`build-candidate`每20秒抽取一帧、最多12帧检查原生歌词、逐字歌词或卡拉OK歌词，本项不暂停人声分离或SOFA候选。代表帧疑似连续歌词卡时，必须在确认`master.srt`前补做完整逐句画面对照。画面歌词与实际演唱共同构成时间轴证据，网易云时间仍不得直接视为MV时间

## 机器对齐、候选隔离与人工审定

完整合同和文件布局见[alignment-workflow.md](references/alignment-workflow.md)

本节只在确认不存在人工制作的官方字幕时执行

```powershell
python -B $pipeline build-candidate --mv-dir $mvDir

# 只调试单步时使用
python -B $pipeline alignment-prepare --mv-dir $mvDir
python -B $pipeline alignment-preflight --mv-dir $mvDir
```

`alignment-prepare`只把LRC正文顺序、日中正文、角色、读音和音素写入`review/alignment/text-only.tsv`，并证明输出字段不含`start`、`end`、`timestamp`或LRC时间。正式常用词表位于`references/common-readings.tsv`；逐曲特殊读音只在`lyrics/pronunciation-overrides.tsv`写少量确认行，必须绑定行号和原文，不做全歌词覆盖。未被两者解决的英文、数字或疑似注音写入`pronunciation-review.tsv`并停止SOFA

已知歌词的演唱默认使用纯SOFA。`build-candidate`先自动进入绑定的base Profile并运行真实组件doctor，再用15秒样本验证绑定的BS-RoFormer，生成与源片等长的`work/vocals.wav`，运行SOFA，严格核对正文音素并把AP和SP仅作为静音审计，最后生成可编辑的`sofa-only.srt`候选和审计材料。相同输入已有有效文件时自动复用，不建立状态数据库，不生成预览视频，也不修改`master.srt`。MC和自然口语仍显式使用Whisper

Whisper不接收歌词提示、LRC时间、人工字幕时间或时间窗口。SOFA只接收分离人声与无时间音素序列。长间奏、重复副歌、重复短句和喊声只允许用音频事件限制搜索范围，不允许用LRC、VTT或人工字幕时间切段

用户明确要求实验或三路对比时，才分别生成`whisper-only.srt`、`sofa-only.srt`、`combined.srt`和`three-way-comparison.tsv`。三者必须保持独立身份。现有“SOFA起点＋Whisper终点”的Combined策略只作实验诊断，不进入默认流程，不得宣称优于纯SOFA，也不能把相互回填后的结果冒充单独模式

```powershell
python -B $pipeline alignment-validate --mv-dir $mvDir
python -B $pipeline build-subtitle --mv-dir $mvDir --candidate "$mvDir\review\alignment\candidates\sofa-only.srt"
```

`alignment-validate`检查候选隔离、结构、三路字段和人工`master.srt`保护基线。先把可编辑SRT、对比表和必要预览交给用户人工复对，只有用户明确选择当前候选后才运行`build-subtitle`

`build-subtitle`只接受固定候选目录中已经通过结构检查的SRT，并在`subtitle/master.srt`不存在时建立人工审定入口。`master.srt`存在时拒绝覆盖，后续修改必须直接人工审阅

## 标准样式与预览

```powershell
python -B $pipeline style --mv-dir $mvDir
python -B $pipeline preview --mv-dir $mvDir
```

`style`从当前审定字幕派生标准ASS：

- 中文在上，暖黄色，80号
- 日文在下，白色，57号
- `WrapStyle:2`，不自动折行
- 超宽行保持字号，只降低`\fscx`；所需水平缩放低于55%时失败并退回人工调整
- 分辨率与画布按片源宽高比适配

颜色、字号和处理顺序直接采用本项目规范，不读取旧成品猜测参数。完整质量门禁见[quality-gates.md](references/quality-gates.md)

MV预览不切片。把完整源视频原样复制到`review/preview/`，在旁边放同名外挂ASS，并把`preview.md`和`preview-windows.tsv`放在同一目录；不重新编码视频，不把字幕封装进MKV。时间窗口覆盖字幕开头、中段、结尾、最长行、亮场、暗场和末句消失位置。画面含原生歌词时，预览必须同时检查双语字幕与画面歌词是否逐句对应；只确认样式清晰不能代替时间轴核对

确认时间轴或样式有问题时修改`master.srt`，再运行`style`和`preview`

生成预览后必须停下，向用户展示本支MV的预览结论并等待明确确认。用户确认当前预览没有问题前，不得运行`render`。对完整流程的泛化授权不能代替本支预览确认

脚本只用文件修改时间检查直接依赖：`master.srt`更新后旧ASS不能预览，ASS或片源更新后旧预览不能压制。这不是持久化状态，也不生成哈希清单

## 递增版本压制与验收

```powershell
python -B $pipeline render --mv-dir $mvDir
python -B $pipeline validate --mv-dir $mvDir
```

用户明确确认本支预览后，才进入压制轮次并运行`render`。`render`要求已有预览，生成`<mv-id>.hardsub.vNN.mp4`和`<mv-id>.subtitle.vNN.ass`。视频重编码，音频必须直拷贝；源音频不兼容MP4直拷贝时失败，不偷换转码

`validate`完成全片音视频解码、尺寸和时长核对、源与成品音轨内容校验，并从当前成品抽取典型画面。它生成机器检查和未勾选的人工检查表。机器通过不等于人工通过，必须播放关键段并查看抽帧后逐项勾选

通用文件哈希、批准令牌和工作流状态文件都不产生。这里只保留音轨内容校验，因为它直接证明音频直拷贝没有改变

## 发布文案

成品验收通过后，打开官方页面核对歌曲名、艺人名、发行信息和本支MV最值得写的具体看点，再使用`human-writing`完成四个平台章节。不要把同一段机械复制四遍：

- 微博：一句有具体锚点的开场，简洁正文，来源链接和话题
- 小红书：标题、简介正文、话题，突出可感知的画面或情绪细节
- B站：视频标题、简介正文、歌曲与字幕制作信息、来源链接
- 视频号：短标题、紧凑正文、来源和话题

四个平台章节保存到一个与视频同版本的文件：

- `output/<mv-id>.publish-copy.vNN.md`

文件内固定使用`## 微博`、`## 小红书`、`## B站`、`## 视频号`四个二级标题。每个章节独立适配对应平台

未核实的信息不写，文件不得为空或保留占位词。发布动作仍需用户明确授权

## 封面图

封面只使用官方封面、官方缩略图或MV源视频中的真实画面。禁止生成画面、生成艺人形象、补绘人物和AI换脸

```powershell
python -B $pipeline cover-candidates --mv-dir $mvDir
```

命令从无硬字幕片源的12%、30%、50%、70%、88%位置抽取5张真实帧，并生成接触表。查看原图后选择主帧，再做裁切、亮度与色彩微调、必要的背景压暗和标题排版。文字内容采用已确认的歌名、艺人名或发布标题，不改动人物面部与画面事实

禁止调用图像生成制作封面。成品保存到`output/<mv-id>.cover.vNN.png`，版本号与硬字幕成品一致且至少720×720。交付前同时查看原尺寸和缩略图，确认人物、文字、边缘、对比度和平台比例正常

## 交付检查与归档

```powershell
python -B $pipeline delivery-check --mv-dir $mvDir
python -B $pipeline archive --mv-dir $mvDir --destination "<archive-directory>"
```

`delivery-check`确认同版本MP4、ASS、四平台合并文案、唯一封面、机器检查和人工检查齐全，并生成`output/delivery-check.vNN.md`

`archive`仅在交付检查存在时复制整个`output/`到一个尚不存在的目标目录。目标已存在时拒绝覆盖；复制失败时不留下目标半成品。它不删除工作区、源视频、字幕或其他文件

## 交付内容

- 递增版本硬字幕MP4
- 与成品版本一致的ASS
- 包含微博、小红书、B站、视频号四个章节的合并文案
- 官方素材或MV真实帧封面
- 机器检查、人工检查与交付检查
- 本地保留的来源、歌词、审定字幕和预览材料

涉及中文发布文案时使用`human-writing`调整语感；事实材料仍须查证并保留来源
