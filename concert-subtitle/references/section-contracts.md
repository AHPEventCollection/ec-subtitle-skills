# 分段契约

## 通用限制

每次分段处理限定在对应的`run/sections/<section-id>`目录

允许读取：

- `input.json`
- `audio.flac`
- 当前分段歌词文件
- 当前分段`evidence`
- 仅单曲完成后选择复用时读取该曲`run/review/<section-id>/repetition`或`structure.tsv`及`run/review/<section-id>/structure`，默认分段制作不要求这些文件

允许写入：

- `events.json`
- `report.json`
- `section.ass`
- `section.srt`
- 当前分段`evidence`

当前分段`evidence`可包含`lyric_boundary_proposals.json`，但建议文件不得替代
`events.json`，也不得声明已应用或自行批准

重复段处理可写版本化`repetition_groups.json`、`rhythm_map.json`、回灌结果和
`applied:false`提案。提案生成阶段不得执行`decide`或`apply`，不得修改正式事件、
gate、批准记录或状态；代表样片与全部离群样片确认后才能记录最终裁决

默认直接制作和审核完整歌曲分段，不先打样，不要求结构表或推广记录。该曲完成后确有适用结构，再按[单曲结构复用（可选）](structure-timing.md)使用已经完成的时间轴处理该曲，不能要求其他歌曲配合打样

可选复用只生成待审候选，保留原稿并核对目标音频；实际复核前不得调用`structure_review.py apply`或把候选标记为已人工复核。应用后重新检查该曲并使旧批准失效

MC分段`evidence`必须包含审核阶段记录的`mc_complexity_assessment.json`。
可报告疑似多人、交叠或说话人不确定，但不得自行把静态帧或ASR结果
升级成音画确认

禁止：

- 修改`concert_manifest.json`
- 修改其他分段
- 安装依赖或新建虚拟环境
- 直接以源视频执行对齐或ASR
- 绕过GPU队列
- 将未经审核的结果标记为已批准

## 区间识别输出

读取歌单、预检后的`concert.flac`和必要的画面证据，输出`sections.json`

每段必须包含：

- `id`
- `type`
- `title`
- `start`
- `end`
- `boundary_source`
- `boundary_confidence`

允许使用源视频判断画面章节和转场，但ASR、声学匹配和音频定位只能消费独立音频

区间识别阶段不生成正式字幕，也不得把低置信区间伪装成已确认区间

这是交给`index_concert.py --sections`的内部文件，用户不需要编写。示例结构如下，实际值必须来自当前素材的识别结果

```json
{
  "sections": [
    {
      "id": "song_01",
      "type": "song",
      "title": "示例歌曲",
      "artist": "示例歌手",
      "start": 84.2,
      "end": 323.8,
      "boundary_source": "audio_review",
      "boundary_confidence": 0.9
    },
    {
      "id": "mc_01",
      "type": "mc",
      "title": "MC1",
      "start": 323.8,
      "end": 418.5,
      "boundary_source": "audio_review",
      "boundary_confidence": 0.9
    }
  ]
}
```

## 分段执行命令

`run_section.py`包装已选定的实际处理命令，负责GPU排队、输入校验与状态管理；具体歌曲对齐、MC听写和翻译按对应制作模板执行

```powershell
python scripts/run_section.py `
  --section-dir "<section-dir>" `
  --gpu `
  --runtime-dir "<concert-dir>/run/runtime" `
  -- <actual-section-processing-command>
```

参数支持`{section_dir}`、`{audio}`、`{input}`、`{events}`和`{report}`占位符。必须选用已经完成运行时预检的处理命令，不能把示例占位符当作可运行工具，也不能把调度器当作完成整场字幕的命令

## 歌曲字幕输出

`events.json`：

```json
{
  "schema_version": "0.1.0",
  "section_id": "song_01",
  "events": [
    {
      "id": "song_01_0001",
      "start": 1.25,
      "end": 4.8,
      "source_text": "日文歌词",
      "translation": "中文翻译",
      "evidence": ["asr_word", "vocal_onset"]
    }
  ]
}
```

时间必须相对正式分段起点，不是相对带上下文的`audio.flac`起点

歌曲事件也必须提供中文`translation`，不允许以日文单语结果通过双语字幕gate

歌曲分段允许包含经复核的歌词外讲话事件。歌词事件的`role`为`lyric`
或省略，讲话事件必须标记`role: speech`，并包含：

- 正式ASR或词级时间证据
- 经人工复核的日文`source_text`
- 中文`translation`

歌词正文、数量和顺序gate只统计`lyric`事件。讲话事件不得与歌词事件重叠，
且会强制触发完整分段复核

可识别同曲重复候选并输出节奏证据，但只能读取独立FLAC。互相关和起音
能量只用于同曲映射、漂移和置信度，不能改写歌词、用于MC、机械吸附拍点或自行
应用传播提案

云音乐或其他已知歌词把一个完整语义句拆成相邻多行时，允许把相邻歌词行
合并为一个`lyric`事件。此时必须写入一基、闭区间的
`lyric_line_span: [start_line, end_line]`，正文规范化后必须等于该区间内
已知歌词依次拼接的结果，不得跳行、倒序或改写

`report.json`至少提供：

```json
{
  "first_vocal_start": 1.2,
  "alignment_slope": 1.0,
  "max_correction_seconds": 0.3,
  "max_extrapolation_seconds": 0.0,
  "first_reliable_anchor_ratio": 0.05,
  "unsafe_reference_extrapolation": false,
  "instrumental_gaps": []
}
```

## MC字幕输出

MC事件必须同时包含：

- `source_text`：日文听写
- `translation`：中文翻译

MC字幕交付前必须确保每条日文不超过56显示单位、中文不超过44显示单位。超限事件要结合词级时间、停顿和完整语意重建为多条事件；不得按固定字符数机械切分，也不得把超限项只写成审核提醒

专有名词或低置信听写写入`report.json`的`review_flags`

还必须在`review_flags`中报告：

- 疑似3人以上
- 交叠或快速抢话
- 说话人身份不可靠
- 画外音或人群插话

字幕初稿不能以事件数、长句数量或零重叠声明断句质量已经改善。复杂度分级在
回听音频后记录。高风险MC不执行正式自动重排，初稿只作为听写草稿

## MC复杂度评估

运行`assess_mc_complexity.py`写入评估文件。评估必须绑定当前
`input_hash`，并至少包含：

- `classification`
- `speaker_count_estimate`
- `signals`
- `evidence_basis`
- `risk_level`
- `manual_conform_required`
- `automatic_reflow_policy`

评估证据必须包含`audio_review`或`continuous_av_review`。静态画面与ASR词时间
只能作为辅助，不能单独完成分级

## 审核记录

写入`review_decision.json`并更新`status.json`

只有完成实际审核后才能将状态设置为`approved`

只有确认代表样片和全部离群样片后才能批准重复传播提案。应用工具必须绑定
该批准与当前提案文件SHA256；应用成功后状态只能为`stale`，不得直接恢复为
`needs_review`或`approved`

批准记录同时绑定`input_hash`和`result_hash`。自动gate后任何事件或报告变化都会使原批准失效

MC批准记录还绑定`mc_complexity_hash`。高风险MC必须完整记录连续音画回看、
话轮、语句边界、日文复听和重断句后翻译，缺一项均不得批准
