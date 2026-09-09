# MV机器对齐固定流程

## 官方字幕优先分支

1. 取得片源后先检查平台人工制作的官方日文字幕
2. 有官方字幕时运行`official-subtitle-prepare`，冻结官方日文正文和顺序，以逐事件时间作为可复核的初始轴
3. 同一命令仍查询网易云前5个候选，按日文相似度、条数、重复顺序和整段相对时段生成`netease-match.tsv`，高置信中文只预填空白行
4. 网易云绝对时间不得覆盖现场版时间；相对时段偏移只用于发现现场速度、删句和重复段落差异，不等于实际早起或晚起，后者必须由实际演唱或独立音频对齐确认，再在`translation.tsv`修订起止毫秒
5. 运行`official-subtitle-apply`合并中文，再运行`official-subtitle-build`校验日文和顺序未变、时间为正且零重叠，最后人工复核并提升为`master.srt`
6. 有官方字幕时不运行SOFA、Whisper或Combined主流程，网易云仅用于中文和相对时段参考
7. 只有确认没有人工官方字幕时，才执行下面的机器对齐流程

自动生成字幕不属于官方字幕，只能作为参考

## 模式路由

- `whisper`只用于MC、对白、自然口语或演唱识别对照
- `sofa`是已知歌词演唱的默认模式，输入必须是完整BS-RoFormer分离人声；省略`--mode`时固定选择纯SOFA
- `combined`只在用户明确要求实验或对比时生成独立诊断候选；现有“SOFA起点＋Whisper终点”策略不进入默认流程，也不能直接成为人工主字幕
- `compare`同时保留Whisper、SOFA、Combined三份独立候选和逐行对比表
- 单曲MV的短片头或片尾对白仍留在本Skill，长MC、多首歌和返场改用`concert-subtitle`

## 固定顺序

1. 建立单支`workspace/mvs/<mv-id>/`工作区并取得片源
2. 确认不存在人工制作的官方字幕
3. 取得日中歌词并核对正文顺序、重复、改词和语气词
4. 常用词由`references/common-readings.tsv`处理，逐曲例外只写稀疏`lyrics/pronunciation-overrides.tsv`
5. 运行`build-candidate`，自动完成真实doctor、无时间输入、最多12帧非阻塞画面抽样、15秒分离样本、完整人声、SOFA、AP/SP转换和结构验证
6. `pronunciation-review.tsv`仍有真实疑点、人声分离失败或SOFA正文音素不一致时停止，修正后原命令续跑
7. 候选固定写入`review/alignment/candidates/`，不生成硬字幕，不修改人工`master.srt`
8. Whisper不带歌词提示、LRC时间、人工字幕时间或时间窗口运行，只用于MC、自然口语或明确实验
9. `song.lab`已是无时间的最终日语音素序列，SOFA必须使用`NoneG2P`原样读取，不得再用词典G2P重新解析
10. 首次ASR/SOFA对齐只允许使用音频事件限制搜索范围，不允许使用LRC、VTT或人工字幕时间替代本片定位；之后人工校准的代表段用于结构推广，不能丢弃后再独立处理
11. 主线程按[完整代表段与结构推广](structure-timing.md)填写结构，先核实完整一番或二番，再校准并按4拍/8拍音频线索生成整曲`structure-candidate.srt`；原始机器候选继续保留，目标段使用自己的歌词与翻译
12. 复对始终使用`review/candidate-preview/`中的完整原视频与同名SRT；逐行检查映射和局部差异，用户明确确认整曲候选后，才用`build-subtitle --candidate`建立`subtitle/master.srt`
13. 用户只确认候选时，建立并审定`master.srt`后进入正式样式预览并停下检查
14. 用户确认候选并同时明确要求直接出片时，运行`finish`连续完成`build-subtitle`、`style`、`preview`、`render`和`validate`，不再增加第二次确认；真实异常仍立即停止

## 固定文件

```text
review/alignment/
├── text-only.tsv
├── song.lab
├── input-audit.md
├── pronunciation-review.tsv
├── pronunciation-applied.tsv
├── master-guard.sha256
├── candidate-build.md
├── preflight-<mode>.md
├── candidate-check-<mode>.md
└── candidates/
    ├── whisper-only.srt
    ├── sofa-only.srt
    ├── combined.srt
    └── three-way-comparison.tsv
review/candidate-preview/
├── source.<源视频扩展名>
├── source.srt
└── candidate-preview.md
review/structure.tsv
review/structure/
├── reference.srt
├── structure-candidate.srt
├── usage.tsv
├── review.md
└── transfer.md
```

`review/candidate-preview/`只提供同目录候选复对配对，视频保持原样且字幕仍是机器候选，不等于`master.srt`或正式ASS预览

`master-guard.sha256`只保护人工`master.srt`不被机器候选阶段改写，不是通用状态机或全项目哈希清单

## 必须停止

- 人声分离失败、时长不一致或输出不是`work/vocals.wav`
- 歌词正文顺序不确定
- 日语读音或音素存在未确认错误
- SOFA音素数量或顺序不一致
- 一句歌词被拉成十几秒或几十秒
- 候选存在重叠、倒序、负时长或空事件
- 抽查已经达到明显不可用程度

停止时只保留诊断候选和证据，不能称为字幕成品，也不能继续压最终硬字幕

## 经验基线

- 同一首MV从原始混音换成BS-RoFormer分离人声后，SOFA入点MAE曾从约4.57秒降到约0.67秒
- 爱美混合片段的演唱部分使用分离人声时，SOFA边界MAE约0.493秒
- 无提示Whisper会漏唱、误识别片头制作文字或产生片尾幻觉，覆盖率不能代替歌词完整性
- 以某支含40条上传方官方日文字幕的MV为基准，纯SOFA综合边界MAE为0.254秒，优于Combined的0.471秒和Whisper的0.748秒
- 同一基准中，双边误差不超过0.5秒的事件数为SOFA 35/40、Combined 13/40、Whisper 2/40
- Whisper终点平均提前0.899秒，现有Combined因继承Whisper终点平均提前0.726秒；纯SOFA起点和终点分别平均提前0.092秒与0.222秒
- 该基准正文原样一致39/40条，仅有引号样式差异；规范化后40/40条一致，因此差距来自时间边界而不是歌词正文
- 结论固定为上传方官方日文正文和顺序优先，时间轴必须对照实际演唱复核；网易云中文仍需匹配，缺少官方字幕且已有歌词时纯SOFA优先，Whisper用于MC和自然口语，Combined只保留为实验诊断


## 机器对齐、候选隔离与人工审定

本节只在确认不存在人工制作的官方字幕时执行

```powershell
python -B $pipeline build-candidate --mv-dir $mvDir

# 只调试单步时使用
python -B $pipeline alignment-prepare --mv-dir $mvDir
python -B $pipeline alignment-preflight --mv-dir $mvDir
```

`alignment-prepare`只把LRC正文顺序、日中正文、角色、读音和音素写入`review/alignment/text-only.tsv`，并证明输出字段不含`start`、`end`、`timestamp`或LRC时间。正式常用词表位于`references/common-readings.tsv`；逐曲特殊读音只在`lyrics/pronunciation-overrides.tsv`写少量确认行，必须绑定行号和原文，不做全歌词覆盖。未被两者解决的英文、数字或疑似注音写入`pronunciation-review.tsv`并停止SOFA

已知歌词的演唱默认使用纯SOFA。`build-candidate`先自动进入绑定的base Profile并运行真实组件doctor，再用15秒样本验证绑定的BS-RoFormer，生成与源片等长的`work/vocals.wav`，运行SOFA，严格核对正文音素并把AP和SP仅作为静音审计，最后生成可编辑的`sofa-only.srt`候选和审计材料。候选通过结构检查后，把完整源视频原样复制到`review/candidate-preview/`，并在同一目录放置与视频同名的候选SRT，播放器打开视频即可自动加载；`candidate-preview.md`同时列出开头、中段、结尾、最长事件和最短事件检查点。本阶段不重新编码视频、不生成ASS、不修改`master.srt`。相同输入已有有效文件时自动复用。MC和自然口语仍显式使用Whisper

Whisper不接收歌词提示、LRC时间、人工字幕时间或时间窗口。SOFA只接收分离人声与无时间音素序列。长间奏、重复副歌、重复短句和喊声只允许用音频事件限制搜索范围，不允许用LRC、VTT或人工字幕时间切段

用户明确要求实验或三路对比时，才分别生成`whisper-only.srt`、`sofa-only.srt`、`combined.srt`和`three-way-comparison.tsv`。三者必须保持独立身份。现有“SOFA起点＋Whisper终点”的Combined策略只作实验诊断，不进入默认流程，不得宣称优于纯SOFA，也不能把相互回填后的结果冒充单独模式

```powershell
python -B $pipeline alignment-validate --mv-dir $mvDir
# 主线程先按references/structure-timing.md填写结构并校准完整代表段
python -B $pipeline structure-prepare --mv-dir $mvDir --candidate "$mvDir\review\alignment\candidates\sofa-only.srt"
python -B $pipeline structure-propose --mv-dir $mvDir
# 用户明确确认整曲结构候选后才执行
python -B $pipeline build-subtitle --mv-dir $mvDir --candidate "$mvDir\review\structure\structure-candidate.srt"
```

`alignment-validate`检查候选隔离、结构、三路字段和人工`master.srt`保护基线。官方字幕候选由`official-subtitle-build`检查日文正文和顺序不变、修订时间合法及中文齐全。候选阶段的必要预览固定为`review/candidate-preview/`中的完整源视频与同名候选SRT，不要求用户跨目录寻找或手工指定字幕。先把该配对、可编辑SRT和必要对比表交给用户人工复对，只有用户明确选择当前候选后才运行`build-subtitle`

`build-subtitle`接受固定初稿候选目录和`review/structure/structure-candidate.srt`。机器歌曲已进入结构校准流程时，只允许采用当前有效的整曲结构候选，并回读参考段、结构及逐行覆盖记录；`subtitle/master.srt`不存在时才建立人工审定入口，存在时拒绝覆盖，后续修改必须直接人工审阅
