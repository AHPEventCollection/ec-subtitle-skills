# 官方字幕与按需中文参考

## 官方字幕优先与歌词回退

用户指定官方中文字幕时，核实其为人工上传轨而非自动翻译，按原唱语言轨逐事件比对。中文与原文的起止时间和事件数一致时，直接填入`translation.tsv`和`chinese.tsv`并保留官方文本，不调用模型改译、不查询网易云；不一致时先解决分句映射。用`chinese-source --kind official`记录`中文歌词来源：官方YouTube中文字幕`，保留原始字幕和核对证据，再运行`official-subtitle-build`。用户明确授权确认官方中字后直接出片时，核实后执行`finish`

下载命令固定调用系统共享`ytdlp-global`，由全局入口统一解析`yt-dlp`、`yt-dlp-ejs`、JS运行时、FFmpeg、缓存、认证和客户端策略。项目只请求SDR优先、最高4320p、MKV、页面缩略图及人工字幕侧文件，不使用`--write-auto-subs`。下载结束后自动检查`source/`中的任意语言的官方VTT、SRT、ASS或SSA

YouTube客户端策略、错误分类和认证边界见[YouTube取源](youtube-acquisition.md)。这些策略只由全局入口维护，项目不得另设客户端回退。所有尝试写入`review/source-acquisition.json`，只有最终成功策略进入`source/source.md`

发现唯一官方原文字幕时，以其原文正文和事件顺序为冻结基准，以逐条起止时间为初始时间轴。已有可用官方中文时直接采用；仅在缺少可用中文、需要网易云参考时查询前5个候选，综合歌名、艺人、原文正文相似度、总条数、重复句顺序和整段相对时段匹配现成中文。网易云绝对时间不得直接覆盖现场版时间，只用于匹配重复段落和提示相对时段偏移；真正的早起或晚起必须结合实际演唱或独立音频对齐确认

```powershell
python -B $pipeline official-subtitle-prepare --mv-dir $mvDir
# 缺少可用中文、需要网易云参考时才加--reference-netease
```

命令生成冻结基准`source-cues.tsv`、可审定工作表`translation.tsv`和精简输入`chinese.tsv`，默认不发起网易云查询。显式加`--reference-netease`时才取得`lyrics/original.lrc`、`lyrics/chinese.lrc`、`lyrics/lookup.md`以及`netease-match.tsv`、`netease-match.md`、`netease-chinese.tsv`。高置信网易云中文自动预填到空白`chinese.tsv`，不会覆盖已有人工中文；`netease-chinese.tsv`始终单独保留完整匹配结果，已有人工中文时可逐行对比或显式作为`--translations`输入。已有官方工序文件时只刷新网易云参考可运行：

```powershell
python -B $pipeline official-subtitle-reference --mv-dir $mvDir
```

首先通读review/official-subtitle/review.md整首审阅表，一次检查错译、漏译、跨行语义、重复句不一致；只有真实问题才修改，不为统一文风反复重写。少量修改保存为line、japanese、chinese三列TSV，日文须与冻结基准完全相同，运行official-subtitle-review --edits <修改行.tsv> --translation-model <实际模型名>一次合并、构建和校验；未列出的中文与时间保持不变。完整输入仍可用chinese.tsv或--translations，不能和--edits同时提供

相对时间换算只使用实际完整匹配的首尾歌词起点，不把混音、录音或母带署名当歌词，不用末句结束时间拉伸全曲；不足两个不同时间锚点时显示无法提供相对提示

复核`netease-match.tsv`中的正文相似度、对应条数、相对位置和时段偏移提示。相对偏移只说明录音室版与现场版的速度、删句或段落位置不同，不得直接判定官方字幕早起或晚起。原文正文与顺序不得修改；官方起止时间是初始值，不是不可更改真值。实际演唱或独立音频对齐证明某句早起或晚起时，在`translation.tsv`修订`start_ms`或`end_ms`，保持正时长和零重叠。只编辑或补齐`chinese.tsv`的`chinese`列，再用固定命令合并：

```powershell
python -B $pipeline official-subtitle-apply --mv-dir $mvDir
# 只有存在网易云未覆盖或人工改译行时才需要
python -B $pipeline official-subtitle-apply --mv-dir $mvDir --translation-model "<本次实际模型名>"
python -B $pipeline official-subtitle-build --mv-dir $mvDir
# 用户确认候选后才执行
python -B $pipeline build-subtitle --mv-dir $mvDir --candidate "$mvDir\review\alignment\candidates\official-subtitle.srt"
```

官方一句对应网易云连续2至3句时，完整日文归一化一致才合并各句中文；缺少任一句译文则保持待补，不接受半句。仅空格、换行或标点整理仍署名网易云，不计为模型翻译。`official-subtitle-build`在源视频存在时自动生成完整源视频与同名SRT的候选预览配对，无需另行调用预览辅助函数

`official-subtitle-apply`完整输入接受`line`和`chinese`两列，按行号回填并保留已审定时间。中文全部来自高置信网易云匹配时署名网易云；存在网易云未覆盖或改译行时必须填写实际模型名并署名混合来源或翻译模型。模型名必须来自本次实际运行环境，不得照抄示例或猜测。`official-subtitle-build`逐条验证冻结的行号、原文正文和顺序，同时允许有审计的时间修订；中文必须齐全，时间必须为正且零重叠。先人工复核语义、重复段落和早晚入点，再将候选提升为`master.ass`

发现多份官方字幕或语言无法确认时停止自动选择，使用`official-subtitle-prepare --subtitle <source内字幕路径>`明确指定。自动生成字幕只作参考，不属于本工序的官方字幕

官方字幕存在时，网易云只提供中文正文和相对时段参考；只有确认不存在人工制作的官方字幕时，才把网易云原文正文送入下面的机器对齐流程

默认按`source/title.txt`和`source/artist.txt`查询网易云，检查搜索结果前5个候选，综合歌名、艺人和中文逐句覆盖选择最可靠结果：

```powershell
python -B $pipeline lyrics --mv-dir $mvDir
```

只有100%覆盖的双语结果才自动继续，并写入`lyrics/original.lrc`、`lyrics/chinese.lrc`和`lyrics/lookup.md`。网易云没有可用结果时固定生成`lyrics/fallback-search.md`，依次使用UtaTen和TuneCore Japan，按“艺人加完整歌名→完整歌名→可辨识首句”的方式搜索并保存采用URL。查询不完整时保留候选审计，直接核对正文并补齐`chinese.lrc`

网易云完整双语歌词被采用时，`lyrics`或官方字幕匹配工序写入`subtitle/chinese-source.txt`，公开署名固定为`中文歌词来源：网易云音乐`。部分采用网易云中文、部分由模型补译时署名`中文歌词来源：网易云音乐、翻译模型（<实际模型名>）`。网易云只提供日文、中文由模型补译，或从UtaTen、TuneCore Japan取得日文后由模型翻译时，运行：

```powershell
python -B $pipeline chinese-source --mv-dir $mvDir --kind translation-model --model "<本次实际模型名>"
```

此时公开署名固定为`中文歌词来源：翻译模型（<实际模型名>）`

上面的混合来源是候选工序的自动记录；发布前按[MV文案格式](publish-copy-format.md)核定中文主体。主体来自网易云、模型只做少量补译或改译时，保留翻译修改记录，用`chinese-source --mv-dir <mv-dir> --kind netease`将公开来源改为仅网易云，再生成文案；不因模型参与校对就公开署名翻译模型

没有官方字幕时日文歌词决定正文和顺序。网易云时间轴只作机器候选生成后的人工参考，不得进入Whisper、SOFA、Combined或内部切段。有官方字幕时允许把网易云时间换算为整段相对位置，用于重复句匹配和早晚入点提示，但仍不得直接替换现场版绝对时间。必须对照MV实际演唱检查改词、重复、语气词和间奏，ASR只提供口语或疑点窗口的音频证据，不替换歌词正文

不存在官方字幕时，`build-candidate`每20秒抽取一帧、最多12帧检查原生歌词、逐字歌词或卡拉OK歌词，本项不暂停人声分离或SOFA候选。代表帧疑似连续歌词卡时，必须在确认`master.ass`前补做完整逐句画面对照。画面歌词与实际演唱共同构成时间轴证据，网易云时间仍不得直接视为MV时间

## 多语言兼容

官方人工字幕不限定语言，英文、韩文等字幕可保留原文、顺序和时间轴进入同一复核流程。多个语言或区域轨无法唯一匹配时列出文件并明确选择，不默认优先日语，也不启动SOFA冒充缺字幕。按实际演唱核实所选轨是原文还是翻译；仅有翻译轨时可参考时间，但不能把翻译冒充原唱歌词

为兼容已有TSV、候选和脚本，`japanese`、`netease_japanese`等字段名暂时保留，其含义为原文歌词，不限制文字语言。英文短句仍要求中文翻译。模型仅处理缺失译文，已核实的官方时间轴继续复用
