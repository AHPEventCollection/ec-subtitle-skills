# 单曲结构复用（可选）

默认流程切割后直接逐段完成，不打样。本页工具仅在某首歌已经完成、发现适合复用的结构后单独使用，参考时间从已完成的该曲字幕取得，不要求用户重新打一轮样，也不为其他歌曲生成结构包

以下是保留工具的可选操作，不是制作或批准前置条件。工具的`reference`文件名只表示该曲复用参考；映射只是候选，不能代替目标音频核对。无合适结构或复用没有收益时直接结束，不阻塞全场

## 先识别结构

结合已知歌词、独立音频和必要画面，在分段目录写`structure.tsv`。这是制作过程中维护的内部输入，不要求用户填写

表头为`cycle、part、role、first、last、start、end、beats、complete、evidence`，字段之间使用制表符

- `cycle`：实际演唱轮次，如`1`、`2`、`last-chorus`
- `part`：同曲中对应结构使用同一名称，如`A`、`B`、`C`，每轮内唯一
- `role`：`verse`、`prechorus`、`chorus`、`bridge`或`other`
- `first`、`last`：本段对应初稿事件的一基闭区间，按演唱顺序排列
- `start`、`end`：独立音频中核实的音乐结构起止，时间相对歌曲正式起点，覆盖该部分的全部演唱，不抄LRC时间
- `beats`：该部分的拍数假设，根据实际音频核实；4拍/8拍乐句有助于定位，不强制所有歌曲使用同一拍数
- `complete`：实际确认该部分完整后填`yes`，未确定填`no`
- `evidence`：写明定位证据和观察，例如哪份独立音频、主歌起句、过渡及副歌收尾；不能填占位说明

结构必须覆盖完整一轮主歌到副歌，可包含过渡。完整性由结构与音频证据决定，不能由ASR准确率、字幕行数、重复句完整或固定秒数决定。结构超过84秒或180秒也保留全段，不缩短主歌。间奏或长停顿不自动成为截断点

歌词外讲话、adlib及不适合套用模板的桥段保留为局部复核项。不要为了让表格通过而把它们标成普通歌词

先填写大致结构起止，可用下列入口计算音频节奏候选，再结合回听核实拍数与4拍/8拍分组

```powershell
python scripts/structure_review.py rhythm --concert-dir "<concert-dir>" --section "<song-id>"
```

`rhythm-hints.md`保留半速/倍速歧义；相关性不足时报告证据不足，不自动填拍数或标记完整

## 从已完成字幕准备复用参考

在Skill目录执行，`--cycle`可省略，默认取第一轮结构完整的候选，也可明确选完整二番

```powershell
python scripts/structure_review.py prepare --concert-dir "<concert-dir>" --section "<song-id>" --cycle 1
```

`build_review_clips.py`只生成完整歌曲与MC分段，不调用本入口。缺少适合本工具的结构时跳过可选复用，不要求补打样段

结构工具的内部工作包位于`run/review/<song-id>/structure`。交付前按[待检查文件整理](workflow.md#待检查文件整理)将本轮视频和字幕复制到统一平铺目录，并同名配对。内部工作包包括：

- `reference.mp4`与`reference.ass`：重新编码、PTS归零的完整代表段配对
- `reference.srt`：从已完成字幕提取的参考，时间相对参考视频起点；确需局部修改后重跑`prepare`刷新外挂ASS，不覆盖已编辑SRT
- `review.md`：完整窗口、轮次、结构和歌词范围
- TSV文件：初稿、结构及选段的回读依据

直接使用该曲已完成的句首、句尾和断句作为参考，不要求再校准一遍整轮。当前只改时间回灌要求保持正文、翻译和事件顺序；确需改变断句时先更新歌曲字幕与结构，并在新目录保留原稿后重建参考

## 向其他部分推广

```powershell
python scripts/structure_review.py propose --concert-dir "<concert-dir>" --section "<song-id>"
```

按`part`和角色对应结构，保留目标段自己的歌词。相同结构内用独立音频寻找4拍/8拍乐句附近的起音锚点，结合已核实的结构首尾建立分段线性映射。字幕保留与乐句锚点的相对距离，不吸附到拍点，也不再只给目标旧轴加一个代表段修正量

歌词不同不拒绝映射，旋律稍有差异时生成候选并复查实际发声。结构增删、拍数不同、断句数量不同或模板未覆盖的部分，保留原候选并明确列出参考范围和局部复核原因；不能把未使用参考的部分当作已推广

`structure-candidate.srt`包含整首歌，`usage.tsv`逐行说明`reference`、`mapped`或`local_review`，`transfer.md`直接列出各类数量。每一行都必须有去向。缺少足够音频乐句锚点时明确标为结构首尾比例候选，继续复核拍数与局部旋律

目标段局部重叠会保留在候选中并标明冲突行，不能因此丢掉整曲推广结果。应用前必须校正冲突，`apply`仍拒绝重叠；代表段自身重叠则先校正参考，禁止传播

在整曲候选中修订差异后，用户明确确认整曲及局部复核项，才执行：

```powershell
python scripts/structure_review.py apply --concert-dir "<concert-dir>" --section "<song-id>" --reviewed
python scripts/validate_section.py --section-dir "<section-dir>"
```

应用前回读当前歌曲字幕、结构、参考段、音频锚点与逐行覆盖记录，拒绝过期候选。先保存旧分段结果，再应用已审定时间并设为`stale`，之后重新检查和整曲审核。未选择复用的歌曲不要求这些记录；已经应用的复用记录仍需与当前结果一致

已编辑的代表SRT和整曲候选不自动覆盖。重新定位或改变模板时，先将现有`run/review/<song-id>/structure`目录改名保留为本歌审核备份，再运行`prepare`建立新一轮；不删除旧目录或要求用户整理文件
