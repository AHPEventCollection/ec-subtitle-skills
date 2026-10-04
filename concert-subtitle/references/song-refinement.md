# 歌曲精校与误差评估

默认切割后逐段完成整段字幕，不打样。本页提供边界建议、可选逐字重复块微调及人工终版评估；某首歌完成后有合适结构，再单独使用[单曲结构复用](structure-timing.md)。分段批准要求见[质量gate](quality-gates.md)

## 歌词边界建议

第一版歌词轴完成后，可用正式词级ASR生成保守边界建议：

```powershell
python scripts/suggest_lyric_boundaries.py `
  --section-dir "<section-dir>"
```

结果写入`<section-dir>/evidence/lyric_boundary_proposals.json`。该工具折叠日文汉字与假名读音、按时间距离和文字长度排序候选，并强制保持词序单调。句首和句尾分别要求连续音节锚点，词块内部按假名音节位置插值；单个边界默认调整超过0.75秒时保留原值，只有连续音节不少于4且词置信度不低于0.75的强边缘证据允许越过该阈值。相邻句发生重叠时，若前句缺少句尾证据但后句有可靠句首，则以前句结束于后句句首前0.02秒完成交接；其他碰撞只回退发生碰撞的句首或句尾，不整体平移整句。它只生成`applied: false`的复核建议，不修改歌词正文、事件数量、`events.json`或批准状态

## 旧的逐字重复块微调（可选）

仅在需要诊断相同歌词重复块时执行旧入口：

```powershell
python scripts/repetition_review.py prepare `
  --concert-dir "<concert-dir>"
```

仅在某首歌已经完成且存在适用重复结构时选用本工具。它按规范化歌词与连续事件寻找重复块，以间隙扩展诊断窗口并限制180秒；`complete_anchor`只表示重复歌词块完整。不同歌词但相同曲式可单独评估`structure_review.py`，不强制复用，无合适结构则结束该曲制作

节奏证据从分段独立FLAC解码PCM，计算起音能量包络、节奏自相关签名、持续时间漂移和同曲互相关，同时保留原始包络相关与lag。节奏只用于映射、漂移检测和置信度，不决定歌词正文，不执行通用曲式识别或拍点吸附

选定单曲复用后，从已完成时间轴提取对应段作为参考；需要局部修订时在单一可见轴的ASS或SRT中只修改时间，再执行以下命令，不重新安排全场打样：

```powershell
python scripts/repetition_review.py import `
  --concert-dir "<concert-dir>" `
  --section "<song-section-id>" `
  --reference "<reviewed.ass>"

python scripts/repetition_review.py propose `
  --concert-dir "<concert-dir>" `
  --section "<song-section-id>"

python scripts/repetition_review.py decide `
  --concert-dir "<concert-dir>" `
  --section "<song-section-id>" `
  --decision approve `
  --proposal-sha256 "<sha256>" `
  --representative-reviewed `
  --all-outliers-reviewed

python scripts/repetition_review.py apply `
  --concert-dir "<concert-dir>" `
  --section "<song-section-id>"
```

`import`也支持`--full-concert-reference`读取整场人工ASS/SRT。正文、顺序或事件数变化会拒绝回灌并列入离群项。高置信传播要求音频相关度不低于0.75、段落时长漂移不超过3%、单个边界调整不超过0.75秒；现场改词、漏唱、观众接唱、讲话、adlib、低相关、重叠或非正时长均降为离群项

`propose`只生成`applied:false`提案和离群审查片段，不写正式事件。`decide approve`同时绑定提案文件SHA256并确认代表样片与全部离群样片已看。`apply`只落安全且获批的边界，先归档旧正式结果，再写新事件并把分段设为`stale`，强制重跑gate和批准

Silent Siren17首回归使用：

```powershell
python scripts/repetition_review.py benchmark `
  --concert-dir "<silent-siren-concert-dir>" `
  --reference "<manual-final.srt>"
```

报告包含起止误差中位数与P90、0.25/0.5/0.75秒覆盖率、代表事件数、离群数、预计减少深度审查事件数和错误传播率，并记录正式文件回归前后哈希。深度审查基线为旧`full_section`涉及的歌曲事件，提案口径为代表样片事件加离群事件；普通固定审查点不计入该指标

## 人工终版误差评估

人工终版完成后，可把机器轴与人工ASS、SRT或事件JSON对照，生成可积累的误差基线：

```powershell
python scripts/evaluate_subtitle_timing.py `
  --hypothesis "<machine-events.json>" `
  --reference "<reviewed.ass>" `
  --report-json "<evaluation.json>" `
  --report-md "<evaluation.md>"
```

演唱会双语字幕默认读取末行日文，支持一条机器事件对应多条人工事件及反向合并，报告首尾误差的中位数、p90、最大值、覆盖率和误差桶。评估报告只用于调参，不修改字幕和审批
