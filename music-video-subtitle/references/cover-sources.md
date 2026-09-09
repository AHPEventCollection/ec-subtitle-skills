# MV现成封面来源

## 固定优先级

### YouTube来源

默认采用本支官方视频自己的封面缩略图。优先复用download保存的source/source.webp、JPG或PNG，保持原尺寸、构图和比例转为work/cover-source.jpg，同时写入work/cover-source.md说明原图与视频ID

本地缩略图缺失或尺寸不足时，只请求同一视频的高清缩略图，单次请求超时15秒。不重新下载视频，不读浏览器Cookie，不搜索歌曲或专辑封面，不从视频抽帧。取图失败或短边不足720px时明确报告缺封面，保留已完成的正片；用户另有指定时遵循具体要求

finish与正片压制并行准备封面，stage-delivery也可单独补齐；自动选择只认可与当前YouTube视频ID一致的已准备封面，旧发行图不会被自动选中

### 其他来源

优先查找歌曲实际所属单曲或专辑的正面封面，固定顺序如下：

1. [mora](https://mora.jp/)：搜索`艺人名＋完整歌名`，进入单曲或专辑商品页核对曲目、艺人和发行名
2. [Apple Music日本区](https://music.apple.com/jp/)：搜索`艺人名＋完整歌名`，确认歌曲确实收录于对应单曲或专辑
3. [MusicBrainz](https://musicbrainz.org/)与[Cover Art Archive](https://coverartarchive.org/)：用艺人和发行名定位Release Group，只采用已确认的正面封面，优先1200px或原图

艺人、厂牌或发行方官网已有明确发行页时可直接采用，并用它解决上述站点的版本冲突

## 其他来源搜索写法

- mora：`site:mora.jp/package "完整歌名" "艺人名"`
- Apple Music日本区：`site:music.apple.com/jp "完整歌名" "艺人名"`
- MusicBrainz：先搜`releasegroup:"发行名" AND artist:"艺人名"`，再到Cover Art Archive取`front-1200`或原图

不要只搜歌名后采用第一张图片。歌名、艺人、单曲或专辑名任一不一致时继续核对，不采用用户歌单封面、歌词卡、资讯配图、社交平台转载图或搜索结果缩略图

## 落盘门槛

- 保存现成原图为`work/cover-source.<ext>`，或在`stage-delivery`中用`--cover`显式传入
- 最短边至少720px；低清图直接换来源，不放大伪装成高清
- YouTube使用本支视频缩略图；其他来源使用歌曲对应的正式单曲或专辑封面
- 找不到合格图片时明确报告缺封面，不从MV截图，不生成图片，不用无关图片补位
