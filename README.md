# AssetBook · 资产管理与记账系统

一个**自己部署、数据归自己**的资产管理与记账系统。后端 FastAPI + SQLite（零 ORM、零外部服务），
前端是一张**纸面账本风格**的单页应用，部署后只需要一个浏览器。

> 不是记账玩具：账户余额、投资持仓、预算、月度报表、净值曲线、账单导入、自动分类都在里面，
> 数据存在你 Docker 里挂载的那个卷里，没有云、没有账号、没有订阅。

---

## 界面预览

纸面质感（暖纸底 + 墨色字 + 衬线大数字），深浅色两套主题，手机端自动收成顶部导航。

```
┌─────────┬──────────────────────────────────────────────┐
│ AssetBook│  净资产 ¥162,573.50        ▲ 本月结余 …      │
│ 总览     │  ─────────────────────────────────────────── │
│ 记账     │  12 个月净值走势 ／ 资产配置环形图 ／ 账户   │
│ 持仓     │  分类支出 Top ／ 最近流水                     │
│ 预算     │                                              │
│ 设置     │  ▸ 记一笔：日期 类型 金额 账户 分类 备注      │
└─────────┴──────────────────────────────────────────────┘
```

---

## 功能一览

**总览**
- 净资产 / 总资产 / 总负债 / 投资市值 四个数一览
- 12 个月净值走势折线图（悬停显示每月净值）
- 资产配置环形图（账户按类型聚合 + 持仓按品种聚合）
- 本月分类支出排行、最近流水

**记账**
- 三态流水：收入 / 支出 / **转账**（转账自动同时改动两个账户，不重复计收支）
- 记一笔时按备注**自动分类**：先匹配本地关键词规则（内置 24 条，如 `麦当劳→餐饮`），规则不中再走可选 AI
- 流水搜索 + 月份 / 类型 / 账户筛选 + 分页
- **导入账单**：支持支付宝 / 微信支付 / 银行流水 / 通用 CSV（自动识别来源与编码，微信加密 zip 直接传），
  先预览再确认入库，重复单号自动跳过；详见下面的[账单自动导入](#账单自动导入支付宝--微信--银行)
- 导出全部流水为 CSV

**持仓**
- 基金 / 股票 / 债券 / 黄金等，录入份额、成本价即可
- **自动更新现价**：填上代码就自动从公开行情接口拉最新价，也支持手改
  - 沪深股票、场内基金 ETF/LOF、港股 → 腾讯行情（批量、无 Key）
  - 场外基金（申购赎回那种）→ 东方财富历史净值
  - 加密货币 → Binance 现货价
  - 备源：东方财富
- 自动算市值、盈亏额、盈亏比例，按品种画分布环图

**自动同步（后台定时任务，默认开启）**
- 每 30 分钟刷新一次持仓现价、每 12 小时拉一次汇率
- 每天固定时间把当天的资产/负债/净值落进 `networth` 表，攒出长期曲线
- 定时扫收件目录 / 收邮箱，把账单自动记进来（见下节）
- 全部可以在「设置 → 自动同步」里改间隔、看运行状态和日志
- 数据源都是公开接口，**不用注册、不用填 API Key**

**汇率**
- 账户可以填不同币种，汇率来自 open.er-api.com，定时刷新后统一折算成人民币口径

**预算**
- 按「月份 + 分类」设预算，看已花 / 剩余；月度横向条形对比
- 自带的「月花 1000」一键配置：部署完跑 `python3 setup-1000.py 你的密码`，
  会自动建好「邮政储蓄」生活费账户 + 7 项分类预算（合计 1000），可重复跑

**设置**
- 账户管理（现金 / 银行卡 / 支付账户 / 券商 / 信用卡 / 贷款）+ 余额校准
- 分类管理、关键词规则管理
- 改登录密码、导出流水、查看数据库路径

**安全**
- 首次访问在界面上设置密码；也可用环境变量预设
- 密码用 PBKDF2-HMAC-SHA256（12 万次迭代）哈希存储
- 会话是 HMAC 签名的 HttpOnly Cookie，改密码即全体登出

---

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
git clone https://github.com/<你的用户名>/assetbook.git
cd assetbook
cp .env.example .env        # 改掉里面的 ASSETBOOK_PASSWORD
docker compose up -d --build
```

打开 <http://localhost:8000>，用 `.env` 里设的密码登录。

数据存在具名卷 `assetbook-data` 里，`docker compose down` 不会丢，`docker compose down -v` 才会清空。

常用命令：

```bash
docker compose logs -f        # 看日志
docker compose down           # 停掉
docker compose up -d --build  # 改完代码重新构建
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

### 去重与对账户

- 每条账单行都算出一个幂等键存进 `external_id`（支付宝/微信用原始单号，银行没有单号就用内容哈希），
  同一份账单反复导、或者两份时间范围重叠的账单，都**不会把账目翻倍**
- 账单里的「收/付款方式」（如 `招商银行(1234)`、`余额宝`、`零钱`）可以用**映射规则**自动落到对应账户：
  预览时选一下账户点「记住」即可，下次同类的自动归位；没匹配到的落到导入时选的默认账户
- 每次导入记一个批次号，后悔了在导入弹窗底部**一键撤销整批**

---

## 环境变量

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `ASSETBOOK_PASSWORD` | 首次启动时自动设置登录密码；之后改密码请在「设置」里改 | 空（首次访问时在界面设置） |
| `ASSETBOOK_SECRET` | 会话签名密钥。留空则随机生成并存进数据库 | 自动生成 |
| `DATA_DIR` | SQLite 数据文件所在目录 | `backend/../data`，Docker 里是 `/data` |
| `ASSETBOOK_DB` | 直接指定数据库文件完整路径（优先级高于 `DATA_DIR`） | `$DATA_DIR/assetbook.db` |
| `ASSETBOOK_AI_BASE` | OpenAI 兼容接口的 base URL，用于 AI 智能分类 | 空（关闭） |
| `ASSETBOOK_AI_KEY` | 上面对应的 API Key | 空 |
| `ASSETBOOK_AI_MODEL` | 模型名，如 `deepseek-chat`、`gpt-4o-mini` | 空 |
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

**关于 AI 分类**：不配置也能用，本地关键词规则覆盖了常见商户。
配置后，规则没命中的流水会再问一次模型，结果仍会落到你的分类表里，可随时手改。

**关于行情数据源**：腾讯行情（`qt.gtimg.cn`）、东方财富（`push2.eastmoney.com`、`api.fund.eastmoney.com`）、
Binance、open.er-api.com 都是公开接口。如果部署在境内服务器，这几个域名都要能出网；
如果想加美股，`quotes.py` 里把 `105.` / `106.` 前缀的 secid 接回来即可。

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
│   ├── parsers.py         # 账单解析：支付宝 / 微信 / 银行 / 通用 CSV + zip 解密
│   ├── importer.py        # 导入引擎：去重、支付方式映射账户、批次撤销
│   ├── intake.py          # 自动收取：收件目录扫描 + IMAP 邮箱附件
│   ├── scheduler.py       # 后台自动同步：定时刷现价、拉汇率、记净值快照、收账单
│   ├── services.py        # 业务计算：余额、汇总、配置、报表、导入、分类、行情写回
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
