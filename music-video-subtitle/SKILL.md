---
name: music-video-subtitle
description: 为单曲MV、官方Music Video和短篇音乐影像完成片源取得、官方字幕与网易云歌词对照、中文逐事件匹配、时间轴复核、Whisper口语与SOFA演唱机器对齐、候选人工复对、字幕预览、压制验收、发布文案、现成官方封面、发布待提交检查和复制归档；处理MV字幕、歌词匹配、时间轴或该MV的发布准备时使用
---

# 单曲MV双语字幕2.18.0

## 最短入口

先读当前项目实际生效的`AGENTS.md`和`.subtitle-runtime.json`，随后只读本页和当前分支所链接的参考文件。本任务已经读过且没有变化的规则不重复读取。常规执行直接用公开CLI；只有参数不明或报错时才查对应函数、测试与运行时细节，不在开局通读脚本或预检全部模型

单首MV使用本Skill；多首歌连续演出、长MC和全场覆盖审计使用`concert-subtitle`。每支MV在外部项目根的`workspace/mvs/<mv-id>/`工作，不把媒体、环境或模型写入Skill仓库

```powershell
$env:MV_SUBTITLE_PROJECT_ROOT = "<mv-project-root>"
$pipeline = "<installed-skill-dir>\scripts\mv_pipeline.py"
$mvDir = Join-Path $env:MV_SUBTITLE_PROJECT_ROOT "workspace\mvs\<mv-id>"
python -B $pipeline download --mv-dir $mvDir --url "<official-url>"
# 已有可靠片源则使用import-source，不重复下载
```

当前工序自动进入绑定的最小Profile：下载只检查`youtube-download`，候选复核和媒体处理只检查`media`，没有官方字幕时的机器演唱才检查`song`，自然口语才运行Whisper。不临时补PATH或另找Python；实际缺失组件时才查[运行时发现与安装](references/runtime.md)，安装依赖与模型须按用户既有授权或确认的位置执行

下载固定使用系统共享`ytdlp-global --auth auto`，优先匿名，必要时仅使用全局入口的专用隔离登录态，不读默认浏览器Cookie、不临时切客户端。保留最高4320p范围内的原选视频和字幕；若音轨不兼容MP4直拷贝，在本次下载中通过同一入口取得同视频的官方兼容音轨并无转码封装。原始片源保留于`work/youtube-original/`，视频不降清晰度。具体取源合同按需见[取源范围](references/workflow-start.md)和[YouTube取源](references/youtube-acquisition.md)

## 有官方日文字幕

只读[官方字幕与网易云匹配](references/official-subtitles.md)。`download`自动准备冻结日文、初始时间与网易云参考；出现多份字幕选择提示时先明确选定。日文正文和顺序冻结，时间允许按实际演唱审定修订；网易云提供中文与相对时段参考，不能直接替换MV绝对时间

歌词查询自动提取「歌名」及常见视频后缀、清理频道名末尾的官方频道标识，保留`source/title.txt`和`artist.txt`的原始证据。核对前5个网易云候选和`netease-match.tsv`，确认中文覆盖与语义；先整首通读review/official-subtitle/review.md，集中检查漏译、错译、跨行语义和重复句；正确中文不为换文风而重写。少量改动使用line/japanese/chinese三列TSV的--edits一次提交，也可填写`chinese.tsv`并署名本次实际模型

```powershell
python -B $pipeline official-subtitle-review --mv-dir $mvDir --translation-model "<实际模型名>"
# 中文全部为高置信网易云匹配时省略--translation-model
```

该命令合并中文回填、候选生成与校验，并同时完成独立的源画面抽样。返回可编辑的`review/alignment/candidates/official-subtitle.srt`，固定复用预览目录和未变化的视频副本，不为每次复核另建文件夹。完整源视频和同名SRT位于`review/candidate-preview/`，源画面总览位于`review/source-visual-audit/contact-sheet.jpg`。不修改`master.srt`、不生成ASS、不自动批准；原有单步CLI保留供定点调试

## 没有官方日文字幕

只读[机器对齐与候选隔离](references/alignment-workflow.md)，先取得歌词正文，再运行`build-candidate`。已知歌词的演唱固定BS-RoFormer全长分离加纯SOFA时间轴；网易云时间不得输入机器对齐，分离失败不得降级到原混音。Whisper只处理自然口语，三路比较仅在明确要求时运行

机器候选生成后按[完整代表段与结构推广](references/structure-timing.md)校准完整一番或二番并推广到全曲，保留差异审计；疑似原生歌词卡须在审定前补完整逐句画面对照

## 人工门禁与出片

先交付完整视频与同名候选SRT供人工复对。仅在用户明确选定候选后建立`master.srt`，已有人工主字幕不得被候选覆盖。用户已确认候选并明确要求直接出片时，此授权覆盖正式预览、压制和机器验收，不再重复问样式确认

到本阶段只读[样式、压制与交付](references/delivery-workflow.md)，涉及样式异常才查[质量门禁](references/quality-gates.md)

```powershell
python -B $pipeline finish --mv-dir $mvDir --candidate "<已确认候选.srt>"
# 已有master.srt时省略--candidate
```

`finish`自动逐阶段打印耗时和成品规格，保存`review/final-vNN-timings.md`，沿用机器验收报告与交付状态完成回读，不另写逐曲计时或回读脚本。下载耗时按source-acquisition.json分别列出主片源和兼容音轨补取调用；说明是否含启动、解析和首播等待。复用片源的重跑须明确标为不含下载，不得称为全流程测速。默认x264 medium/CRF18不变；只有用户要求硬件编码试验时才按[压制参数](references/delivery-workflow.md)显式启用NVENC，未经体积和画质对照不切换默认

`finish`依次建立主字幕、生成标准ASS与完整外挂预览、递增版本压制、验收并落盘已就绪的文案和封面。字幕样式固定中文上方暖黄80号、日文下方白色57号、不自动折行，超宽只水平压缩且低于55%退回调整。音频必须直拷贝，视频尺寸保持片源，禁止为通过压制偷换音频转码

成品完整解码、尺寸时长和音轨内容校验后生成8张典型抽帧及`review/final-vNN-contact-sheet.jpg`。先集中查看总览和封面；文字太小或有疑点再打开对应原帧。真实播放、音画同步和拖动检查仍必须实际执行，不能用抽帧代替或自动勾选

## 并行准备与交付

下载、歌词匹配期间可同步准备独立的官方发行信息、文案和封面；finish在压制与验收期间自动准备YouTube封面，执行工具返回运行中会话后，利用等待编码的时间完成同一份work/publish-copy.md（完整写入临时同级文件再原子替换，避免读取半稿）。不要先空等压制再写文案；素材较晚就绪时只运行stage-delivery补齐，不重压正片；同一文件的生成与读取保持依赖顺序。尽量一批读取当前工序需要的字幕、来源说明和画面，不反复查询同一状态，不重复研究已核实材料

文案集中于`work/publish-copy.md`，默认微博、小红书、B站、视频号；用户明确的平台范围优先。每个平台各自包含`subtitle/chinese-source.txt`的实际来源署名。用户修改过的正式output文案优先于旧草稿

YouTube来源默认直接采用本支官方视频的source/source.webp等缩略图，保持尺寸、构图与比例转换为work/cover-source.jpg；缺失时只补取同视频高清缩略图，不搜索歌曲封面、不从MV截图。最短边至少720px；其他来源按[封面来源](references/cover-sources.md)

本地交付包括同版本MP4、ASS、文案、封面和验收记录。回读`delivery-status`并如实列出缺项；人工检查全完成后运行`delivery-check`。上传或待提交仅在用户要求时读取[发布流程](references/publish-handoff.md)，最终发布和归档分别按明确授权执行。工作流以文件为事实，不新增状态机、批准令牌或通用哈希清单
