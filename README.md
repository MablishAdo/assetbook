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
- **导入账单**：支持支付宝、微信导出的 CSV（自动识别中文字段名），先「预览」再确认入库，重复导入会跳过
- 导出全部流水为 CSV

**持仓**
- 基金 / 股票 / 债券 / 黄金等，录入份额、成本价、现价
- 自动算市值、盈亏额、盈亏比例
- 一键更新现价

**预算**
- 按「月份 + 分类」设预算，看已花 / 剩余；月度横向条形对比

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
| `TZ` | 时区 | `Asia/Shanghai` |

**关于 AI 分类**：不配置也能用，本地关键词规则覆盖了常见商户。
配置后，规则没命中的流水会再问一次模型，结果仍会落到你的分类表里，可随时手改。

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
│   ├── db.py              # SQLite 连接、建表、默认分类与规则、净值快照
│   ├── security.py        # 密码哈希、签名 Cookie、鉴权依赖
│   ├── models.py          # Pydantic 请求模型
│   ├── services.py        # 业务计算：余额、汇总、配置、报表、导入、分类
│   ├── routes/
│   │   ├── auth.py        # 登录 / 首次设密码 / 登出 / 状态
│   │   ├── data.py        # 账户、分类、规则、流水、导入导出
│   │   └── assets.py      # 总览、持仓、预算、净值、月度报表
│   └── static/            # 前端（原生 HTML/CSS/JS，无构建步骤）
│       ├── index.html
│       ├── app.css
│       ├── app.js
│       └── favicon.svg
├── Dockerfile
├── docker-compose.yml
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
| `POST` | `/api/transactions/import` | 导入账单 CSV（`dry_run=true` 先预览） |
| `GET` | `/api/export/transactions.csv` | 导出流水 |
| `GET` `POST` | `/api/holdings` · `/api/holdings/{id}/price` | 持仓 / 更新现价 |
| `GET` `POST` | `/api/budgets` | 预算报表 / 设置预算 |
| `GET` | `/api/reports/monthly` | 月度报表 |

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
