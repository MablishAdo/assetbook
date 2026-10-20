# AssetBook · 记账与投资

一个**自己部署、数据归自己**的记账与投资工具。后端 FastAPI + SQLite（零 ORM、零外部服务），
前端是一张**纸面账本风格**的单页应用，部署后只需要一个浏览器。

只有两件事：**记账**和**投资**。没有账户、没有银行卡余额、没有净资产估算。

> v2.0 起账户功能整体下线。你要盯的是「这个月花了多少」和「我的持仓该怎么调」，
> 资产账面数字对你没有意义，索性不记。数据存在你 Docker 挂载的那个卷里，
> 没有云、没有账号、没有订阅。

---

## 界面预览

纸面质感（暖纸底 + 墨色字 + 衬线大数字），深浅色两套主题，手机端自动收成顶部导航。

```
┌─────────┬──────────────────────────────────────────────┐
│ AssetBook│  记账                    投资                 │
│ 记账     │  ─────────────────────────────────────────── │
│ 投资     │  本月结余 ／ 收入 ／ 支出 ／ 储蓄率            │
│ 设置     │  ▸ AI 记账分析：钱花在哪、下个月怎么改          │
│         │  ▸ 记一笔：日期 类型 金额 分类 备注（无账户）  │
│         │  ▸ 流水明细：搜索 + 月份/类型筛选 + 分页        │
└─────────┴──────────────────────────────────────────────┘

┌─────────┬──────────────────────────────────────────────┐
│ 投资     │  投资市值 ／ 总成本 ／ 总盈亏 ／ 盈亏率         │
│         │  ▸ AI 持仓分析：逐只给 加仓/持有/减仓 + 具体动作  │
│         │  ▸ 持仓明细：份额 成本价 现价 市值 占比 盈亏     │
│         │  ▸ 品种分布环图 ／ 30 天·12 个月市值走势        │
└─────────┴──────────────────────────────────────────────┘
```

---

## 功能一览

**记账**
- 流水两态：收入 / 支出。**没有转账**（它依赖两个账户，账户没了就没意义）
- 记一笔时按备注**自动分类**，三级链路：本地关键词规则（内置 24 条，如 `麦当劳→餐饮`）→
  **Jev 快通道**（配了 Key 就自动生效，决策模型，一次约 0.00003 美元）→ 对话模型兜底（要显式开启），
  详见下面的 [Jev 快通道](#jev-快通道记一笔时自动分类)
- 流水搜索 + 月份 / 类型筛选 + 分页
- **导入账单**：支持支付宝 / 微信支付 / 银行流水 / 通用 CSV（自动识别来源与编码，微信加密 zip 直接传），
  先预览再确认入库，重复单号自动跳过；详见下面的[账单自动导入](#账单自动导入支付宝--微信--银行)
- 导出全部流水为 CSV
- **AI 记账分析**：把账本汇总数字交给模型，换回一段说人话的点评

**投资**
- 基金 / 股票 / 债券 / 黄金等，录入份额、成本价即可
- **自动更新现价**：填上代码就自动从公开行情接口拉最新价，也支持手改
  - 沪深股票、场内基金 ETF/LOF、港股 → 腾讯行情（批量、无 Key）
  - 场外基金（申购赎回那种）→ 东方财富历史净值，并自动带出中文名与**申购费率**
  - 场外基金可以**按买入金额录入**：只填「买了 1000 块」，按净值与费率自动反推份额与含费成本价
  - 加密货币 → 币安现货价（451 时回退 CoinGecko）
  - 备源：东方财富
- 自动算市值、成本、盈亏、**占比**，按品种画分布环图，画 30 天 / 12 个月市值走势
- **AI 持仓分析**（本版本重点）：先抓每只标的的区间涨跌幅，再逐只给
  **加仓 / 持有 / 减仓** 的结论与可执行动作

**AI 持仓分析（v2.0 新增）**

喂给模型的不只是「现在赚没赚」，还有每只标的的**区间涨跌幅**：

| 标的类型 | 涨跌数据来源 | 提供的区间 |
|---|---|---|
| 场外基金 | 东方财富 `FundMNBasicInformation` 的 `SYL_*` 字段 | 近1周 / 1月 / 3月 / 6月 / 1年 / 今年以来 |
| 场内基金、股票、港股 | 腾讯 K 线，按收盘价自算 | 近1月 / 3月 / 6月 / 1年 |
| 加密货币 | 币安 24h，不可用时 CoinGecko | 近1日 / 1周 / 1月 |

数据包里还有：每只标的的占比、盈亏率、成本额，以及**品种集中度**（判断是不是全压在一个方向上）。
输出强制三档结论，每只都要给理由和具体动作——不允许写「建议关注」这种没法执行的话。

> 一个容易踩的坑：六位数字代码里场内与场外是同一片号段。`market=auto` 时不能直接拼
> `sz`/`sh` 去查 K 线，腾讯会返回一只不相干标的的行情（实测 110022 会被算成近1月
> -14.34%，而它真实的基金净值涨跌是 +0.36%）。错的数字比没有数字更糟，所以现在
> auto 会先问一遍行情接口确认到底是场内还是场外再选源。

**自动同步（后台定时任务，默认开启）**
- 每 30 分钟刷新一次持仓现价
- 每天固定时间把当天的投资市值落进 `portfolio_history` 表，攒出长期曲线
- 定时扫收件目录 / 收邮箱，把账单自动记进来（见下节）
- 全部可以在「设置 → 自动同步」里改间隔、看运行状态和日志
- 数据源都是公开接口，**不用注册、不用填 API Key**

**预算**
- 按「月份 + 分类」设预算，看已花 / 剩余
- 自带的「月花 1000」一键配置：建好 7 项分类预算（合计 1000）、一个不占预算的「房贷」分类、24 条常用分类规则
- 月度横向条形对比，看最近 12 个月的收支

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
git clone https://github.com/MablishAdo/assetbook.git
cd assetbook
cp .env.example .env        # 改掉 ASSETBOOK_PASSWORD；想换端口改 ASSETBOOK_PORT
docker compose up -d --build
```

打开 <http://localhost:8000>（服务器上就是 `http://服务器IP:8000`），用 `.env` 里设的密码登录。
装在自己电脑上、想换端口，改 `.env` 里的 `ASSETBOOK_PORT` 即可；装在服务器上记得**安全组 / 防火墙放行这个端口**，
长期挂公网建议前面套 Nginx 并开 HTTPS（见下面的[反向代理](#反向代理想挂公网--域名)）。

数据存在具名卷 `assetbook-data` 里，`docker compose down` 不会丢，`docker compose down -v` 才会清空。

想一键配好账户和「月花 1000」的预算（可选，见[预算](#预算)一节）：

```bash
docker compose exec assetbook python /app/setup-1000.py            # 密码自动取容器里的 ASSETBOOK_PASSWORD
docker compose exec assetbook python /app/setup-1000.py 你的密码   # 已经在界面改过密码就显式传
```

这个脚本是通过服务自己的 HTTP 接口写数据的，所以**容器要先起来**；可以重复跑，不会重复建。

常用命令：

```bash
docker compose logs -f        # 看日志
docker compose down           # 停掉
docker compose up -d --build  # 改完代码 / git pull 之后重新构建
```

### 方式二：Docker 单条命令

```bash
docker build -t assetbook:latest .
docker run -d --name assetbook -p 8000:8000 \
  -e ASSETBOOK_PASSWORD='你的密码' \
  -v assetbook-data:/data \
  --restart unless-stopped \
  assetbook:latest
```

### 方式三：本地直接跑（开发）

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
cd backend && ../.venv/bin/python -m uvicorn main:app --reload --port 8000
```

或者 `make install && make dev`。

---

## 账单自动导入（支付宝 / 微信 / 银行）

先把丑话说在前面：**支付宝、微信、银行都没有面向个人的交易 API**（它们的开放平台只对商家开放），
所以不存在「授权一下就自动同步」这种玩法。可行的路只有把账单文件弄进来，AssetBook 把这一步做到了最省心：

| 渠道 | 怎么用 | 适合谁 |
| --- | --- | --- |
| ① 手动上传 | 「记账 → 导入账单」选文件，预览确认 | 偶尔导一次 |
| ② 收件目录 | 把导出的文件丢进收件目录，后台定时扫描自动入库 | 最推荐，平时只需「导出 → 丢进去」 |
| ③ 邮箱 IMAP | 配好 IMAP 后自动收账单邮件里的附件 | 想全自动 |

**三条管道走的是同一套解析 + 去重逻辑**，所以怎么进来都不会重复记账。

### ① 收件目录

```bash
# 本地跑：项目目录下
data/inbox/

# Docker：容器内 /data/inbox（已挂卷），从宿主机丢文件进去：
docker cp ~/Downloads/微信支付账单.csv assetbook:/data/inbox/
```

后台默认每 10 分钟扫一次，能认出来的自动入库并把文件挪到 `inbox/imported/`（旁边留一个 `.log.txt` 说明），
认不出的挪到 `inbox/failed/` 并写明原因。也可以在「导入账单」弹窗或「设置 → 自动同步」里点「立刻扫描」。

### ② 邮箱（IMAP）

支付宝和微信的账单都能发到邮箱。在 `.env` 里配好 `ASSETBOOK_IMAP_HOST / USER / PASS`（见环境变量表），
后台每小时自动收一次**未读**邮件里的 CSV / ZIP 附件，处理完标记已读。
微信导出的 zip 是加密的，把微信通知里的密码填到 `ASSETBOOK_WECHAT_ZIP_PASSWORD` 即可自动解开。

### 认哪些格式

| 来源 | 识别方式 | 说明 |
| --- | --- | --- |
| 支付宝 | 表头 `交易时间,交易分类,…` | GBK 编码、跳过头部说明行；「交易关闭」自动剔除，「不计收支」标为跳过 |
| 微信支付 | 表头 `交易时间,交易类型,…交易单号` | UTF-8 BOM、自动去 `¥` 和 `/`；全额退款的会提示留意方向 |
| 银行流水 | 表头含 `日期 + 金额` | 兼容「借贷分两列」「正负号」「借贷标志」几种常见格式 |
| 通用 CSV | 兜底 | 含金额列即可，日期 / 备注尽力识别 |

### 去重与撤销

- 每条账单行都算出一个幂等键存进 `external_id`（支付宝/微信用原始单号，银行没有单号就用内容哈希），
  同一份账单反复导、或者两份时间范围重叠的账单，都**不会把账目翻倍**
- 导入时自动分类：备注先过本地关键词规则，没命中再问 Jev / 对话模型
- 每次导入记一个批次号，后悔了在导入弹窗底部**一键撤销整批**

---

## 环境变量

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `ASSETBOOK_PORT` | 只给 docker compose 用：服务映射到宿主机的端口 | `8000` |
| `ASSETBOOK_PASSWORD` | 首次启动时自动设置登录密码；之后改密码请在「设置」里改 | 空（首次访问时在界面设置） |
| `ASSETBOOK_SECRET` | 会话签名密钥。留空则随机生成并存进数据库 | 自动生成 |
| `DATA_DIR` | SQLite 数据文件所在目录 | `backend/../data`，Docker 里是 `/data` |
| `ASSETBOOK_DB` | 直接指定数据库文件完整路径（优先级高于 `DATA_DIR`） | `$DATA_DIR/assetbook.db` |
| `ASSETBOOK_AI_BASE` | OpenAI 兼容接口 base URL（AI 兜底分类 + AI 记账分析 + AI 持仓分析）。**建议直接在「设置 → AI 分析」里配，不用环境变量** | 空（关闭） |
| `ASSETBOOK_AI_KEY` | 上面对应的 API Key | 空 |
| `ASSETBOOK_AI_MODEL` | 模型名，如 `deepseek-chat`、`gpt-4o-mini` | `gpt-4o-mini` |
| `JEV_API_KEY` | TypeSafe Jev 的 Key（快通道分类）。**建议直接在「设置 → AI 分析 → Jev 快通道」里配** | 空（关闭） |
| `TYPESAFE_API_KEY` | 同上，官方文档里的名字，两个都认；`JEV_API_KEY` 优先 | 空 |
| `JEV_MODEL` | Jev 模型名 | `jev-latest` |
| `JEV_BASE` | Jev 接口地址（一般不用改，自建代理时才用） | `https://api.typesafe.ai` |
| `ASSETBOOK_CORS` | 允许跨域来源，逗号分隔 | `http://localhost:5173` |
| `ASSETBOOK_AUTO_SYNC` | 自动同步总开关，`1` 开 `0` 关 | `1` |
| `ASSETBOOK_QUOTE_INTERVAL_MIN` | 行情刷新间隔（分钟） | `30` |
| `ASSETBOOK_SNAPSHOT_HOUR` | 每天几点记净值快照 | `23` |
| `ASSETBOOK_RATES_INTERVAL_HOUR` | 汇率刷新间隔（小时） | `12` |
| `ASSETBOOK_INBOX` | 账单收件目录的绝对路径 | 本地 `data/inbox`，Docker `/data/inbox` |
| `ASSETBOOK_INBOX_ACCOUNT` | 收件箱导入时没匹配到规则的行落到哪个账户（账户名或 id） | 第一个非负债账户 |
| `ASSETBOOK_INBOX_INTERVAL_MIN` | 扫收件目录间隔（分钟） | `10` |
| `ASSETBOOK_IMAP_HOST` / `PORT` / `USER` / `PASS` | IMAP 邮箱收账单，三项都填了才启用 | 空（关闭） |
| `ASSETBOOK_IMAP_FOLDER` / `SSL` | 收件文件夹 / 是否用 SSL | `INBOX` / `1` |
| `ASSETBOOK_IMAP_FROM` | 只收某个发件人的邮件（可选） | 空 |
| `ASSETBOOK_WECHAT_ZIP_PASSWORD` | 微信加密账单 zip 的密码（在微信通知里） | 空 |
| `ASSETBOOK_MAIL_INTERVAL_MIN` | 收邮件间隔（分钟） | `60` |
| `TZ` | 时区 | `Asia/Shanghai` |

> 上面这些自动同步参数在**数据库里有设置时以数据库为准**，
> 也就是直接在「设置 → 自动同步」里改就行，不用动环境变量。
> AI 的三个变量同理：**「设置 → AI 分析」里填的优先**，环境变量只是兜底。

**关于 AI（分类与分析）**：不配置也能用，本地关键词规则覆盖了常见商户。
配置后有两个能力：① 规则没命中的流水会再问一次模型，结果仍落到你的分类表里，可随时手改；
② 概览页多出一块「AI 财务分析」。见下一节。

**关于 Jev（快通道分类）**：另一条独立的路，配不配都不影响上面两个能力。见 [Jev 快通道](#jev-快通道记一笔时自动分类)。

**关于行情数据源**：腾讯行情（`qt.gtimg.cn`）、东方财富（`push2.eastmoney.com`、`api.fund.eastmoney.com`）、
Binance、open.er-api.com 都是公开接口。如果部署在境内服务器，这几个域名都要能出网；
如果想加美股，`quotes.py` 里把 `105.` / `106.` 前缀的 secid 接回来即可。

---

## AI 分析

有两条独立的线，各自有缓存：

| | 记账分析 | 持仓分析 |
|---|---|---|
| 位置 | 记账页顶部 | 投资页顶部 |
| 看什么 | 这个月钱花在哪、风险在哪 | 每只标的该加仓还是减仓 |
| 输出 | 一句话总结 / 钱花在哪了 / 风险提醒 / 接下来做什么 | 组合体检 / 逐只建议 / 集中度与节奏 / 前提提醒 |
| 缓存粒度 | 按月份 | 按天 |

**发出去的是什么**：只有汇总数字 —— 本月收支、分类占比、预算执行、近几个月趋势、
投资市值与盈亏、每只标的的占比与**区间涨跌幅**、品种集中度，外加分类名 / 持仓名。
**不发**流水备注原文，也不发单笔流水明细。想亲眼确认：

- `GET /api/ai/snapshot` —— 给模型看的记账数据包
- `GET /api/ai/portfolio-snapshot` —— 给模型看的持仓数据包（含每只标的的涨跌幅）

**怎么省钱**：

- 结果缓存在数据库里，反复点不会反复烧 token
- 账本有新流水 / 持仓价格变了，旧分析会被标成「数字有点旧，建议重新生成」，但**不会**自动重跑 ——
  花钱的事你来点头
- 数据包控制在 2K 字符以内，一次分析通常几千 token

**配置**：`设置 → AI 分析`，填 OpenAI 兼容的接口地址（到 `/v1` 这一层）、模型名、Key。
常见的搭配：

| 服务 | 接口地址 | 模型 |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5`（Key 随便填） |

没配时记一笔照常工作（只走本地规则），只是没有 AI 兜底和分析面板。

---

## Jev 快通道（记一笔时自动分类）

[Jev](https://typesafe.ai) 是 TypeSafe 的 **System One 决策模型**，跟「聊天模型」是两回事：
它不生成文字，只按你给的选项/评分标准回**结构化字段**，代码拿到就能直接分支。
官网 <https://typesafe.ai>，文档 <https://docs.typesafe.ai>，控制台 <https://console.typesafe.ai>。

**为什么给记账用它**：自动分类本质就是「从这 15 个分类里挑一个」——正是 Jev 的强项。
比起让对话模型生成一句话再拿字符串去匹配，走 Jev 是**直接拿到选中的分类名**，不用解析自然语言，
而且更快、更稳定。计费上输入 $0.042/M、**输出免费**，一次分类大约 $0.00003。

**怎么配**：`设置 → AI 分析 → Jev 快通道`，填 Key（形如 `apikey_...`），点「测试连接」验证。
模型默认 `jev-latest`，一般不用改。Key 在 <https://console.typesafe.ai> 里生成。

**配好之后会发生什么**：

| 顺序 | 谁来判断 | 什么时候生效 |
| --- | --- | --- |
| 1 | 本地关键词规则 | 永远，命中最快 |
| 2 | **Jev** | 配了 Key 就自动生效 |
| 3 | 对话模型 | 只在显式要求时才用（避免每次记账都等一次慢请求） |

同时有两条保险，保证它不会拖累记账：

- **结果缓存**：同一段备注 + 同一种收支方向，只问一次
- **失败熔断**：连不上或 Key 失效时，静默退回「未分类」（不会报错、不会阻断记账），
  并停用 120 秒；**你在设置里重新保存一次配置就会立刻解冻**，不用重启服务

不想用了就点「清除 Key」，链路立刻退回本地规则，账本数据不受影响。

---

## 反向代理（想挂公网 / 域名）

放到 NAS 或服务器上，建议前面套一层 Nginx 并开 HTTPS：

```nginx
server {
    listen 443 ssl;
    server_name book.example.com;

    ssl_certificate     /etc/letsencrypt/live/book.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/book.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

因为会话 Cookie 是 `SameSite=Lax` + `HttpOnly`，务必**同源**代理，别把前端单独挂到别的域名上。

---

## 数据与备份

- 数据库是**单个 SQLite 文件**：`assetbook.db`（WAL 模式，同目录还有 `-wal` / `-shm`）
- 备份最简单的方式：停掉服务，把整个 `data/` 目录拷走；`make backup` 会把 db 拷到 `backups/`
- Docker 里：`docker compose cp assetbook:/data/assetbook.db ./backup.db`
- 恢复：把 `assetbook.db` 放回数据目录再启动即可

**设计要点：账户余额不落库。** 账户表只存一个基准余额 + 基准日期，之后的余额全部由流水实时聚合出来。
所以不存在「余额和流水对不上」的经典问题；记错了直接改流水，余额自动重算。
如果现实余额和账面有偏差（比如忘了记几笔），在「设置 → 账户」里用**余额校准**把基准对齐即可。

---

## 目录结构

```
assetbook/
├── backend/
│   ├── main.py            # 应用入口：路由挂载、lifespan、静态托管
│   ├── db.py              # SQLite 连接、建表、字段迁移、默认分类与规则、净值快照
│   ├── security.py        # 密码哈希、签名 Cookie、鉴权依赖
│   ├── models.py          # Pydantic 请求模型
│   ├── quotes.py          # 行情与汇率抓取（腾讯/东财/币安/er-api，零第三方依赖）
│   ├── ai.py              # AI 接入：配置读写（界面优先）、OpenAI 兼容对话调用
│   ├── jev.py             # Jev（TypeSafe System One）决策模型：noul / choice / score
│   ├── insights.py        # AI 财务分析：账本快照、提示词、生成与按月缓存
│   ├── parsers.py         # 账单解析：支付宝 / 微信 / 银行 / 通用 CSV + zip 解密
│   ├── importer.py        # 导入引擎：去重、支付方式映射账户、批次撤销
│   ├── intake.py          # 自动收取：收件目录扫描 + IMAP 邮箱附件
│   ├── scheduler.py       # 后台自动同步：定时刷现价、拉汇率、记净值快照、收账单
│   ├── services.py        # 业务计算：余额、汇总、配置、报表、导入、分类（规则→Jev→AI）、行情写回
│   ├── routes/
│   │   ├── auth.py        # 登录 / 首次设密码 / 登出 / 状态
│   │   ├── data.py        # 账户、分类、规则、流水、导入导出
│   │   └── assets.py      # 总览、持仓、预算、净值、行情与自动同步
│   └── static/            # 前端（原生 HTML/CSS/JS，无构建步骤）
│       ├── index.html
│       ├── app.css
│       ├── app.js
│       └── favicon.svg
├── Dockerfile
├── docker-compose.yml
├── setup-1000.py          # 一键配置「月花 1000」的账户与分类预算
├── Makefile
├── requirements.txt
└── .env.example
```

前端是**原生三件套**，没有 npm、没有打包：改完 `app.css` / `app.js` 刷新浏览器就生效。

---

## 接口文档

启动后访问 `/api/docs`（Swagger UI）或 `/api/openapi.json`。
几个主要接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/auth/state` | 是否已设密码、当前是否已登录 |
| `POST` | `/api/auth/setup` `login` `logout` | 首次设密码 / 登录 / 登出 |
| `GET` | `/api/overview` | 总览：汇总 + 账户 + 配置 + 净值序列 + 持仓 |
| `GET` | `/api/accounts` · `POST` `/api/accounts` | 账户列表 / 新建 |
| `POST` | `/api/accounts/{id}/snapshot` | 余额校准 |
| `GET` `POST` | `/api/transactions` | 流水查询（支持 `q/type/account_id/category_id/month`）/ 新增 |
| `POST` | `/api/transactions/import` | 老接口：一步导入账单 CSV（`dry_run=true` 先预览） |
| `POST` | `/api/import/preview` · `/api/import/commit` | 新版导入：解析预演（自动识别来源/去重/对账户）→ 确认入库 |
| `GET` | `/api/import/sources` | 支持的来源、映射规则、最近批次、收件目录状态 |
| `DELETE` | `/api/import/batch/{batch}` | 按批次撤销一整次导入 |
| `POST` | `/api/import/inbox/scan` · `/api/import/mail/fetch` | 立刻扫收件目录 / 立刻收一次邮件 |
| `GET` `POST` `DELETE` | `/api/payee-rules` | 支付方式 → 账户 映射规则 |
| `GET` | `/api/export/transactions.csv` | 导出流水 |
| `GET` `POST` | `/api/holdings` · `/api/holdings/{id}/price` | 持仓 / 更新现价（`price` 传 0 会自动去行情接口拉） |
| `GET` `POST` | `/api/quotes/preview` · `/api/quotes/refresh` | 探一个代码的行情 / 手动刷全部持仓 |
| `GET` `POST` | `/api/rates` · `/api/rates/refresh` | 当前汇率 / 手动刷新汇率 |
| `GET` `PUT` | `/api/auto/status` · `/api/auto/settings` | 自动同步状态 / 修改设置 |
| `POST` | `/api/auto/run/{quotes\|rates\|networth\|inbox\|mail}` | 手动触发某项任务 |
| `GET` `POST` | `/api/budgets` | 预算报表 / 设置预算 |
| `GET` | `/api/reports/monthly` | 月度报表 |
| `GET` `PUT` | `/api/ai/settings` | AI 配置读取（key 只回末 4 位）/ 保存；响应里带 `jev` 块 |
| `POST` | `/api/ai/analyze` | 生成 AI 财务分析（`force=true` 强制重跑） |
| `GET` | `/api/ai/snapshot` | 给模型看的那份数据包，用来核对分析基于的数字 |
| `GET` `PUT` | `/api/ai/jev` | Jev 快通道配置读取 / 保存（`clear_key=true` 清 Key） |
| `POST` | `/api/ai/jev/test` | 拿一句示例备注真跑一次，验证 Key 与链路 |
| `POST` | `/api/classify` | 试一下某段备注会归到哪个分类，`by` 告诉你是谁定的（`rule`/`jev`/`ai`/`none`） |

**行情怎么自己更新？** 启动时后台起一个 asyncio 任务，每 30 分钟（可改）调一次行情接口把
所有填了代码的持仓现价刷掉，同时把当天净值落进 `networth` 表。API 文档里有
`POST /api/quotes/refresh` 可以手动触发，比如想用系统 cron 自己控制节奏：

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" https://你的域名/api/quotes/refresh
```

---

## 参考过的开源项目

做这个东西之前认真看过一圈同类开源项目，取长补短，也把链接放这里方便你对比挑选：

- [actualbudget/actual](https://github.com/actualbudget/actual) —— 预算驱动、本地优先，思路最接近，但预算模型偏 YNAB 式，学习成本高
- [firefly-iii/firefly-iii](https://github.com/firefly-iii/firefly-iii) —— 功能最全的 PHP 记账，重、要 MySQL，改起来门槛高
- [maybe-finance/maybe](https://github.com/maybe-finance/maybe) —— 设计和净值追踪很漂亮（已停止维护）
- [ghostfolio/ghostfolio](https://github.com/ghostfolio/ghostfolio) —— 投资组合视角，偏证券
- [ezbookkeeping/ezBookkeeping](https://github.com/mayswind/ezbookkeeping) —— Go 写的个人记账，界面成熟，中文友好
- [JellyBook/ExpenseOwl](https://github.com/tanvircr7/ExpenseOwl) —— 极简月度视图
- [beancount/beancount](https://github.com/beancount/beancount) · [ledger/ledger](https://github.com/ledger/ledger) —— 复式记账文本流派的代表，严谨但需要手写账本
- [wallos/Wallos](https://github.com/ellite/Wallos) —— 订阅管理，本身不是记账

AssetBook 的取舍：**要资产视图（账户 + 持仓 + 净值曲线），也要轻量记账（三条流水 + 自动分类），
但不要数据库服务、不要构建工具链、不要云账号。** 所以最后是一个 SQLite 文件 + 一个静态前端。

---

## License

MIT。随便改、随便用，数据始终是你自己的。
