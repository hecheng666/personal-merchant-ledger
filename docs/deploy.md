# 小程序部署方案（云托管 + MySQL）

> 当前状态：后端接口已部署到云托管，健康检查通过；小程序前端在 `miniprogram/`。
> 数据层已支持 MySQL：云端配 `DB_HOST` 自动切换，本地 CLI 和测试继续用 SQLite。

---

## 1. 架构

```
小程序（原生 WXML）
   │  读本地 CSV → wx.cloud.callContainer 把文本传给后端
   ▼
微信云托管（Docker：Python 3.12 + FastAPI + uvicorn）
   │  复用 app/wechat/parser.py 解析 → 写 MySQL（环境内网 10.x）
   ▼
云托管 MySQL（内网地址，无需公网）
```

为什么不用 SQLite 落盘：云托管容器是无状态的，缩容或重新部署后容器内文件会丢，
账单数据不能存在容器里。MySQL 是控制台自带的内网数据库，正好解决持久化。

本地命令行工具仍然用 SQLite，不需要装 MySQL，测试也全部跑 SQLite。

## 2. 数据隔离

云托管会给每个请求注入 `X-WX-OPENID`。所有表都带 `owner` 字段，
交易、现金、对手方记忆按 openid 隔离，不同用户互不可见。

本地调试设置 `ALLOW_LOCAL_AUTH=1`，再用 `X-OPENID: local` 代替；
**生产环境不要开这个开关**，否则任何人拿到公网域名都能伪造身份。

## 3. 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| POST | `/api/import` | `{filename, content, dry_run}`，解析并入库 |
| GET | `/api/months` | 有数据的月份 |
| GET | `/api/pending?month=` | 按对手方聚合的待确认清单 |
| POST | `/api/confirm` | `{peer, category}`，写入记忆库 |
| GET | `/api/report?month=` | 月报 JSON |
| POST | `/api/cash` | `{day, amount, direction, note}` |
| GET | `/api/cash?month=` | 现金明细与合计 |
| DELETE | `/api/data` | 清空当前用户的全部数据 |

导入上限 8MB；没有身份头一律 401。

## 4. 启用 MySQL（控制台操作）

1. 打开 https://cloud.weixin.qq.com/cloudrun ，扫码登录
2. 左侧「MySQL」→「账号管理」→「新建账号」，记下账号和密码
3. 在「数据库管理」里建一个库，比如 `ledger`，给账号授权
4. 回到「服务管理 → ledger-api → 服务设置 → 环境变量」，加：

| 变量名 | 值 |
|---|---|
| `DB_HOST` | `10.12.107.105`（MySQL 页的「内网地址」，去掉端口） |
| `DB_PORT` | `3306` |
| `DB_USER` | 刚建的账号 |
| `DB_PASSWORD` | 刚设的密码 |
| `DB_NAME` | `ledger` |

5. 保存后重新发布一个版本，让环境变量生效
6. 部署完成后打开 `/api/health`，再进小程序导入一次，数据就会写进 MySQL

> 配了 `DB_HOST` 后，`LEDGER_DB` / `MEMORY_DB` 会被忽略，可以留着也可以删掉。

## 5. 部署步骤（改代码后）

代码包：`out/ledger-api.zip`（Dockerfile 在压缩包根目录，路径使用正斜杠）。

1. 服务 →「部署发布」→「新建版本」→ 上传 zip
2. 等构建完成，状态显示「运行中」
3. 第一次访问会冷启动几秒

模板服务 `springboot-q2wa` 测试完可以删掉，避免继续计费。

## 6. 小程序端调用

前端代码在 `miniprogram/`，用微信开发者工具直接导入。
环境 ID 和服务名集中写在 `miniprogram/config.js`。

```js
wx.cloud.callContainer({
  config: { env: 'prod-d1gvf269x5cc14c25' },
  path: '/api/import',
  method: 'POST',
  header: {
    'X-WX-SERVICE': 'ledger-api',
    'content-type': 'application/json'
  },
  data: { filename: file.name, content: text }
})
```

注意：

- `config.env` 是云托管环境 ID，`X-WX-SERVICE` 是服务名；
- 冷启动第一次请求会慢几秒，导入页要有 loading；
- 微信导出的账单是加密 zip，手机端解压麻烦，导入页要提示「电脑解压后发到文件传输助手」。

## 7. 备案与提审

| 项目 | 状态 |
|---|---|
| 主体 | 个人，已注册 |
| ICP 备案 | 已提交，等管局审核（7–15 个工作日） |
| 类目 | 工具 → 记账 |
| 文案禁区 | 不出现「发票」「报税」「税务」「财务代理」「商户服务」 |

提审前还要做：

- [ ] 小程序后台配置《用户隐私保护指引》，声明读取用户选择的文件
- [ ] 首次导入前弹授权说明
- [ ] 提供「清空我的数据」入口（对应 `DELETE /api/data`）
- [ ] README 与「关于」页写清数据流向

## 8. 成本

- 容器缩容到 0，无请求时不计实例费，冷启动 3–5 秒
- 云托管 MySQL 按规格/存储计费，控制台有价格说明，个人低频使用成本很低
- 对象存储、流量按量计费

## 9. 本地开发

```bash
pip install -r requirements.txt
pytest -q                      # 92 项，全部跑 SQLite

# 不配 DB_HOST，CLI 自动用 SQLite
python -m app.cli import --file samples/微信支付账单_2026-10.csv
```

想本地连 MySQL 调后端时，设置 `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME` 后再起 uvicorn。
