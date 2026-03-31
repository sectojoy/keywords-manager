# keywords-manager

面向 SEO 和 GEO 工作流的本地 SQLite 关键词库管理工具。

[English README](README.md)

`keywords-manager` 用来把关键词库存从零散的表格、CSV 导出文件和临时脚本里抽出来，统一保存在本机 SQLite 数据库中。它适合个人站长、内容团队中的单机自动化流程，以及需要长期跟踪关键词状态的本地工具链。

## 目录

- [这个工具的价值](#这个工具的价值)
- [功能概览](#功能概览)
- [适用场景](#适用场景)
- [环境要求](#环境要求)
- [安装方式](#安装方式)
- [数据库位置](#数据库位置)
- [数据模型](#数据模型)
- [典型工作流](#典型工作流)
- [使用示例](#使用示例)
- [端到端示例](#端到端示例)
- [快速演示脚本](#快速演示脚本)
- [批量更新 CSV 模板](#批量更新-csv-模板)
- [CSV 使用说明](#csv-使用说明)
- [输出格式](#输出格式)
- [验证](#验证)
- [仓库文件](#仓库文件)
- [License](#license)

## 这个工具的价值

关键词管理最常见的问题不是“没有关键词”，而是“关键词资产无法持续管理”：

- 同一个 CSV 被重复导入，导致计划重复。
- 不同工具各自维护一份关键词清单，状态很快失真。
- 不知道下一个该写什么，仍然靠手动翻表格。
- 文章发出去了，但关键词清单里没同步 `published_url` 和使用状态。
- 工具升级或仓库迁移后，本地数据跟着丢失。

`keywords-manager` 的核心价值，就是把关键词库存变成一个可查询、可去重、可更新、可复用的本地数据库，并且默认存放在用户目录，而不是仓库目录里。

## 功能概览

- 从本地 CSV 导入关键词
- 从公开 CSV URL 或公开 Google Sheets 导入关键词
- 自动规范化关键词并按 `site + language + keyword` 去重
- 通过 `category` 管理不同关键词集合
- 跟踪关键词状态：`unused`、`used`、`archived`
- 按 `priority` 和 `kd` 选出下一个最适合处理的关键词
- 保存 `published_url`、`priority`、`kd` 和 JSON 格式的扩展元数据
- 导出筛选后的 CSV
- 通过批量 CSV 更新多个关键词字段
- 在需要时安全重建数据库

## 适用场景

- 做内容营销，需要长期维护博客选题池
- 管理多站点、多语言关键词 backlog
- 需要在 AI 写作、发布脚本和关键词规划工具之间共享同一份库存
- 希望把“是否已写过、发到哪里了、优先级是多少”统一落库

## 环境要求

- Python `3.10+`
- 可用的 `python3`

当前实现只依赖 Python 标准库，不需要额外安装第三方包。

## 安装方式

### 方式一：在仓库内直接使用

```bash
git clone <your-repo-url>
cd keywords-manager
chmod +x bin/keywords-manager
./bin/keywords-manager --help
```

### 方式二：加入命令行 PATH

```bash
git clone <your-repo-url>
cd keywords-manager
chmod +x bin/keywords-manager
ln -sf "$PWD/bin/keywords-manager" /usr/local/bin/keywords-manager
keywords-manager --help
```

如果不想创建软链接，直接运行：

```bash
./bin/keywords-manager <command>
```

## 数据库位置

默认数据库路径：

```text
~/.data/keywords-manager/keywords.db
```

这是一个很关键的设计点：运行时数据默认放在用户目录，避免和仓库代码耦合。这样即使技能升级、重新安装或更换工作目录，关键词库存也不会丢。

你也可以手动覆盖数据库位置：

```bash
keywords-manager --db-path /custom/path/keywords.db list
KEYWORDS_MANAGER_DB=/custom/path/keywords.db keywords-manager list
KEYWORDS_MANAGER_DATA_DIR=/custom/path keywords-manager list
```

## 数据模型

每条关键词记录可以包含这些字段：

- `category`
- `site`
- `language`
- `keyword_raw`
- `keyword`
- `status`
- `priority`
- `kd`
- `used_at`
- `published_url`
- `extra`

状态值支持：

- `unused`
- `used`
- `archived`

唯一性规则：

```text
site + language + keyword
```

也就是说：

- 同一站点、同一语言下，规范化后的关键词不能重复
- 不同站点之间可以复用同一个关键词
- 不同语言之间也可以复用同一个关键词

## 典型工作流

1. 初始化数据库
2. 从 CSV 或 URL 导入关键词到某个分类
3. 取出当前最适合处理的未使用关键词
4. 完成写作与发布
5. 标记关键词为 `used`，并写入 `published_url`
6. 持续循环，不再依赖手工维护表格状态

## 使用示例

### 初始化数据库

```bash
./bin/keywords-manager init-db
```

### 从本地 CSV 导入

```bash
./bin/keywords-manager import-csv \
  --file examples/keywords.csv \
  --category blog \
  --site-column site \
  --language-column language \
  --priority-column priority \
  --kd-column kd
```

### 从公开 CSV URL 导入

```bash
./bin/keywords-manager import-url \
  --url https://example.com/keywords.csv \
  --category seo \
  --column keyword \
  --site blog.example.com \
  --language en
```

### 从公开 Google Sheets 导入

```bash
./bin/keywords-manager import-url \
  --url "https://docs.google.com/spreadsheets/d/<sheet-id>/edit#gid=0" \
  --category geo \
  --column keyword \
  --site blog.example.com \
  --language-column language
```

### 按 CSV 表头映射站点、语言、优先级和 KD

```bash
./bin/keywords-manager import-csv \
  --file examples/keywords.csv \
  --category backlog \
  --column keyword \
  --site-column site \
  --language-column language \
  --priority-column priority \
  --kd-column kd
```

### 查询关键词

```bash
./bin/keywords-manager list
./bin/keywords-manager list --category blog --status unused
./bin/keywords-manager list --site imagelean.com --language en --limit 20
```

### 取下一个待处理关键词

```bash
./bin/keywords-manager get-next --category blog --site imagelean.com --language en
```

筛选顺序是：

1. `priority` 越高越靠前
2. `kd` 越低越靠前
3. 创建时间越早越靠前

### 标记关键词已使用

按 id：

```bash
./bin/keywords-manager mark-used --id 12
```

按作用域选择器：

```bash
./bin/keywords-manager mark-used \
  --site imagelean.com \
  --language en \
  --keyword "how to compress image to 100kb"
```

### 恢复为未使用或归档

```bash
./bin/keywords-manager mark-unused --id 12
./bin/keywords-manager archive --id 12
```

### 写入发布地址

```bash
./bin/keywords-manager set-url --id 12 --url https://example.com/post
```

清空发布地址：

```bash
./bin/keywords-manager set-url --id 12 --clear
```

### 写入 JSON 元数据

```bash
./bin/keywords-manager set-extra --id 12 --json '{"source":"kwfinder","cluster":"compression"}'
```

### 调整优先级和关键词难度

```bash
./bin/keywords-manager set-priority --id 12 --priority 10
./bin/keywords-manager set-kd --id 12 --kd 8
./bin/keywords-manager set-kd --id 12 --clear
```

### 管理分类

```bash
./bin/keywords-manager categories list
./bin/keywords-manager categories create --category blog
./bin/keywords-manager categories rename --category blog --to geo
./bin/keywords-manager categories delete --category geo --yes
```

### 导出 CSV

```bash
./bin/keywords-manager export-csv \
  --file exports/blog-unused.csv \
  --category blog \
  --site imagelean.com \
  --language en \
  --status unused
```

### 批量更新

```bash
./bin/keywords-manager bulk-update --file examples/updates.csv
```

适合在外部表格中批量修改状态、发布地址、优先级、KD 或元数据后，再统一写回数据库。

### 重建数据库

```bash
./bin/keywords-manager rebuild-db --yes
```

这个操作是破坏性的，会删除当前数据库文件后再重建。

## 端到端示例

下面这个示例覆盖了从导入、选题、发布到回写状态的完整过程。

先准备源 CSV：

```csv
keyword,priority,kd
how to compress image without losing quality,5,12
png vs jpg vs webp,4,18
what is exif data,3,9
```

仓库里也已经附带了一个可直接使用的示例文件：[examples/keywords.csv](examples/keywords.csv)。

导入关键词：

```bash
./bin/keywords-manager import-csv \
  --file examples/keywords.csv \
  --category blog \
  --site-column site \
  --language-column language \
  --priority-column priority \
  --kd-column kd
```

获取当前最优先的待处理关键词：

```bash
./bin/keywords-manager get-next --category blog --site imagelean.com --language en
```

发布文章后，回写状态和发布地址：

```bash
./bin/keywords-manager mark-used --id 1
./bin/keywords-manager set-url --id 1 --url https://blog.example.com/compress-image-guide
./bin/keywords-manager set-extra --id 1 --json '{"writer":"ai","source":"kwfinder"}'
```

再查看剩余未处理队列：

```bash
./bin/keywords-manager list --category blog --site imagelean.com --language en --status unused
```

这也是这个工具最推荐的工作方式：关键词只导入一次，后续所有状态变化都写回同一个库。

## 快速演示脚本

如果你想不准备任何额外 CSV，就把整套流程先跑一遍，可以直接执行仓库自带脚本：

```bash
chmod +x examples/run-demo.sh
./examples/run-demo.sh
```

这个脚本会：

- 自动创建一个临时 SQLite 数据库，除非你显式传入自定义路径
- 导入 [examples/keywords.csv](examples/keywords.csv)
- 应用 [examples/updates.csv](examples/updates.csv)
- 输出更新后的关键词列表
- 导出剩余 `unused` 关键词到 CSV 文件

如果你想指定数据库和导出文件路径：

```bash
./examples/run-demo.sh /tmp/keywords-demo.db /tmp/keywords-demo-unused.csv
```

## 批量更新 CSV 模板

`bulk-update` 读取的是带表头的 CSV。每一行必须通过以下任一方式定位目标关键词：

- `id`
- `category` + `keyword`

可选的作用域字段：

- `site`
- `language`

可更新字段：

- `status`
- `priority`
- `kd`
- `published_url`
- `extra`

最小模板：

```csv
id,status,priority,kd,published_url,extra
12,used,10,8,https://example.com/post,"{""source"":""kwfinder""}"
13,archived,1,__CLEAR__,__CLEAR__,__CLEAR__
```

不使用 `id`、改用作用域定位的模板：

```csv
category,site,language,keyword,status,priority,kd,published_url,extra
blog,imagelean.com,en,how to compress image without losing quality,used,10,6,https://example.com/a,"{""score"":10}"
blog,imagelean.com,en,png vs jpg vs webp,archived,1,__CLEAR__,__CLEAR__,__CLEAR__
```

执行方式：

```bash
./bin/keywords-manager bulk-update --file examples/updates.csv
```

说明：

- `__CLEAR__` 用于清空 `kd`、`published_url` 或 `extra`
- `status` 合法值只有 `unused`、`used`、`archived`
- 空单元格表示“不修改这个字段”
- 单行出错不会中断整批更新，结果会在汇总中统计 `invalid` 或 `missing`

## CSV 使用说明

- 默认关键词列名是 `keyword`
- 无表头文件可用 `--column-index`
- `--column-index` 不能和 `--site-column`、`--language-column`、`--priority-column`、`--kd-column` 混用
- `site` 会被规范化为小写域名
- `language` 会被规范化为小写连字符形式，例如 `en`、`zh-cn`
- `keyword` 在入库前会进行去首尾空白、合并多空格、转小写处理

## 输出格式

命令输出为 JSON，方便接到 shell 脚本、Agent 或其他本地自动化工具中。

示例：

```json
{"status":"ok","item":{"id":12,"keyword":"example keyword","status":"used"}}
```

## 验证

运行测试：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 仓库文件

- [scripts/keywords_manager.py](scripts/keywords_manager.py)：主实现
- [bin/keywords-manager](bin/keywords-manager)：CLI 包装脚本
- [docs/requirements.md](docs/requirements.md)：需求和行为说明
- [examples/keywords.csv](examples/keywords.csv)：导入示例文件
- [examples/updates.csv](examples/updates.csv)：批量更新示例文件
- [examples/run-demo.sh](examples/run-demo.sh)：端到端演示脚本

## License

[MIT](LICENSE)
