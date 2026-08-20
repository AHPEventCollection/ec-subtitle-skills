# MV机器对齐固定流程

## 官方字幕优先分支

1. 取得片源后先检查平台人工制作的官方日文字幕
2. 有官方字幕时运行`official-subtitle-prepare`，冻结官方正文、顺序和逐事件时间轴
3. 只填写`translation.tsv`的`chinese`列，使中文翻译逐条继承官方字幕的起止时间
4. 运行`official-subtitle-build`校验正文和时间未变，再人工复核翻译并提升为`master.srt`
5. 有官方字幕时不运行SOFA、Whisper、Combined或网易云时间轴主流程
6. 只有确认没有人工官方字幕时，才执行下面的机器对齐流程

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
9. 重复副歌、重复短句、长间奏和喊声只允许使用音频事件限制搜索范围，不允许使用LRC、VTT或人工字幕时间切段
10. 交付可编辑SRT候选和审计材料供人工复对
11. 用户明确选择并确认候选后，才用`build-subtitle --candidate`建立`subtitle/master.srt`
12. 修改并审定`master.srt`后再进入样式预览，预览确认后才压制

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
```

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
- 结论固定为上传方官方字幕优先，缺少官方字幕且已有歌词时纯SOFA优先，Whisper用于MC和自然口语，Combined只保留为实验诊断
