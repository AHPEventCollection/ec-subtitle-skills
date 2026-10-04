# 字幕算法内核

`scripts/subtitle_core/`承载歌词匹配、边界建议、节奏证据和误差评估等纯算法。模块只接收内存对象并返回建议或指标，不读取Manifest、不修改审批状态、不决定ASS样式

## 模块边界

- `transcript.py`：统一读取`subsup.transcript.v1/v2`及兼容的`segments`、`utterances`结构，保留词级时间和probability
- `text_match.py`：NFKC规范化、ASS标签清理、日文汉字与假名读音折叠
- `boundary.py`：按时间距离、文字长度和相似度寻找词级单调匹配，再用连续音节锚点分别判断句首与句尾
- `evaluation.py`：对齐机器字幕和人工参考，支持一对多、多对一并计算误差指标

## 适配规则

演唱会适配器继续负责：

- 只处理`role: lyric`的歌词边界建议
- 中文在上、日文在下，因此双语ASS/SRT评估默认取末行日文
- Manifest、输入哈希、工作包、状态机、审批和正式合并
- 歌曲已知歌词正文、歌词外讲话裁决和全场覆盖审计

## 安全契约

`suggest_lyric_boundaries.py`只生成`applied: false`的建议报告。未匹配事件保持原时间；单个边界默认调整超过0.75秒时记录候选但保留原值，只有不少于4个连续音节且平均词置信度不低于0.75的强边缘允许越过阈值。前句缺失句尾证据时，后一可靠句首可建立0.02秒交接；其他相邻碰撞只回退碰撞边界，并标记`neighbor_conflict_keep_original`或`suggested_partial`。公共内核不提供自动覆盖正式字幕的入口

这里的“音节”是日文读音折叠后的文字位置插值，仍依赖ASR词块起止时间，不是
从声学信号得到的真实音素或音节强制对齐。该证据不得用于推断MC说话人和自然断句

`evaluate_subtitle_timing.py`只生成JSON或Markdown报告。人工终版始终是参考真相源，评估结果仅用于观察和调参
