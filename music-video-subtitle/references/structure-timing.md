# 完整代表段与结构推广

机器初稿生成后，先校准完整的一番或二番，再把时间结构用于其他部分。官方字幕优先分支保留既有正文与初始时间；确需修订其时间轴时也可使用本方法

## 结构由主线程识别

`build-candidate`在`review/structure/start.md`交代后续工序，并建立`review/structure.tsv`表头。主线程根据已知歌词与独立音频填写，不要求用户填写，不照抄LRC时间

字段依次为`cycle、part、role、first、last、start、end、beats、complete、evidence`，使用制表符

- `cycle`是演唱轮次，同一结构的`part`在各轮使用相同名称
- `role`为`verse`、`prechorus`、`chorus`、`bridge`或`other`
- `first`、`last`是初稿事件的一基闭区间
- `start`、`end`是独立音频核实的音乐结构首尾，使用MV全片时间，可写`MM:SS.mmm`
- `beats`记录该部分的拍数假设，4拍/8拍乐句作为定位线索，实际音频决定起点与速度
- `complete=yes`只表示经过定位确认的完整部分，`evidence`具体说明使用的独立音频、起句和收尾依据

代表轮必须覆盖完整主歌到副歌，中间过渡不能漏。可选完整一番或二番，先保证完整性，再比较哪一轮更清楚。短副歌、ASR准确率或固定秒数不能替代这一判断，长结构不为满足旧时长限制而被截掉

填写大致起止后可运行音频节奏分析，结合回听核对拍数与4拍/8拍分组

```powershell
python scripts/mv_pipeline.py structure-rhythm --mv-dir "<mv-dir>" --candidate "<initial-candidate.srt>"
```

输出`rhythm-hints.md`保留半速/倍速候选，证据不足时不推定固定拍数

## 校准与推广

在Skill目录执行：

```powershell
python scripts/mv_pipeline.py structure-prepare --mv-dir "<mv-dir>" --candidate "<initial-candidate.srt>" --cycle 1
```

`--cycle`可省略，默认选择第一轮结构完整的候选。`review/structure/reference.srt`保存完整代表段，时间仍是MV全片时间。候选复对继续使用完整原视频与同名SRT，不重新编码或截短MV

查看`review/structure/review.md`所列完整时间窗口，校准整轮的句首、句尾与发声。保留日中正文、顺序和事件数；断句数量需要改变时先更新初稿与结构，在新审核目录重建参考

```powershell
python scripts/mv_pipeline.py structure-propose --mv-dir "<mv-dir>"
```

根据结构对应关系和独立音频中的4拍/8拍乐句锚点做分段线性映射，保留目标段自己的歌词与翻译。不同歌词或细微旋律变化不直接排除参考，字幕边界保留相对乐句位置，不机械吸附拍点

生成`review/structure/structure-candidate.srt`整曲候选，并刷新原视频的同名候选SRT。`usage.tsv`逐行列出参考位置、已映射部分与需要局部校正的原因，`transfer.md`用文字报告覆盖情况

目标段因变奏或断句差异产生重叠时，保留全部候选时间与参考记录，将冲突行标成局部复核并在`transfer.md`列出位置。修订后才能审定，审定入口仍拒绝重叠，不回退为整曲独立重做。代表段自身有重叠则先校正参考再传播

拍数或结构增删、不同断句数量、参考段未覆盖的部分会保留原候选并标记局部复核，附上可用参考范围。音频锚点不足时明确写明只参考结构首尾比例，不把它当作精确节拍匹配。全部已映射部分仍需检查不同歌词的具体发声

在整曲候选中校正差异，用户确认后才使用既有审定入口：

编辑`structure-candidate.srt`后，先运行`structure-preview --mv-dir "<mv-dir>"`刷新原视频旁的同名SRT。该入口保留人工时间改动，不重新生成或覆盖候选；仍有重叠时在复对说明中明确标记，不能进入审定

```powershell
python scripts/mv_pipeline.py build-subtitle --mv-dir "<mv-dir>" --candidate "<mv-dir>/review/structure/structure-candidate.srt"
```

入口重新回读初稿、结构、代表段、音频参考与逐行覆盖记录，拒绝过期结果。此后继续既有样式、预览和出片流程，不引入演唱会状态机，不自动覆盖`subtitle/master.srt`

修改过的代表SRT和整曲候选不自动覆盖。重新定位或改变模板时，由主线程将现有`review/structure`目录改名保留为审核备份，再运行`structure-prepare`建立新一轮；不删除旧目录或要求用户整理文件，也不能重跑初稿后跳过已经校准的代表段
