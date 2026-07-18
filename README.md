# OpenClaw SmartRouter 双模式智能路由插件

> 为大模型调用提供成本最优化与质量自适应的中间层调度。
> "内核 + 双形态外壳" 解耦设计，既可作为 OpenClaw 内嵌插件，也可作为独立 API 代理网关运行。

## 核心特性

| 特性 | 说明 |
|------|------|
| 双模式运行 | OpenClaw 插件模式（进程内）+ 独立进程模式（FastAPI 网关） |
| 毫秒级在线学习 | ONNX 静态特征 + SGD 增量学习，预测 <30ms，零阻塞主流程 |
| 智能路由模式 | `model="auto"` 自动选择最优模型，指定模型名则直连代理 |
| 预测期望模型 | 预测引擎直接返回期望调用的模型名、难度、任务类型 |
| 动态成本最优路由 | 综合难度、Token、价格、余额、可靠性，免费模型优先 |
| 模型生效时间 | 每个模型可配置生效时间段（如 09:00-18:00），时间外自动禁用，默认全天生效 |
| 多维余量适配 | 策略模式适配 DeepSeek / 智谱 / SiliconFlow / OpenAI / 阿里云 / 本地估算 |
| 自动价格同步 | 从 litellm model_prices.json 定时拉取最新模型单价，支持手动触发 |
| 训练集可视管理 | Web 面板增删改查训练样本，批量导入，一键重训，新增标记自动消失 |
| 路由日志追踪 | 记录路由来源（智能路由/直连/缓存/降级）、Prompt 预览、请求模型名 |
| 日志生命周期管理 | 日志保存天数配置、一键清除、永久保存选项 |
| JWT 认证保护 | 管理面板密码登录 + JWT Token 认证；/v1 接口可选 API Key 认证 |
| 数据闭环反馈 | 前端显式反馈 + 隐式语义分析，反向修正预测模型 |
| 容灾降级 | 冷启动降级、超时熔断、自动重试、Prompt 缓存 |

## 项目结构

```
openclaw-smart-router/
├── core/                          # 【内核】纯逻辑，不依赖网络框架
│   ├── __init__.py
│   ├── config.py                  # 配置加载（YAML + 环境变量覆盖）
│   ├── auth.py                    # JWT 认证 + API Key 验证
│   ├── database.py                # SQLite 异步写入层（训练集管理 + 路由日志）
│   ├── predictor.py               # ONNX + SGD 毫秒级在线学习（含 predict_with_model）
│   ├── router.py                  # 智能决策路由 + 任务类型检测
│   ├── pricing_manager.py         # 价格同步 + 余额策略（多厂商适配）
│   ├── exchange_rate.py           # 汇率同步与换算
│   └── feedback_analyzer.py       # 显式/隐式反馈分析
├── adapters/                      # 【适配层】双形态外壳
│   ├── __init__.py
│   ├── standalone_app.py          # FastAPI 独立网关（含认证中间件）
│   └── openclaw_plugin.py         # OpenClaw 进程内插件
├── web/dist/                      # 【交互层】Vue3 控制面板
│   └── index.html                 # 单文件应用（8 个功能 Tab）
├── scripts/
│   ├── inject_feedback.js         # 前端反馈按钮注入
│   ├── download_minilm.py         # ONNX 模型下载脚本
│   ├── build.sh                   # Docker 构建脚本
│   └── validate_dockerfile.py     # Dockerfile 验证脚本
├── models/                        # ONNX 模型目录（按需下载）
├── tests/                         # 单元测试
├── data/                          # SQLite 数据持久化
├── main.py                        # 独立模式入口
├── plugin.py                      # 插件模式入口
├── config.yaml                    # 核心配置
├── requirements.txt               # Python 依赖
├── Dockerfile                     # 多阶段容器构建
├── docker-compose.yml             # 一键编排
├── .env.example                   # 环境变量示例
├── .dockerignore
├── .gitignore
└── README.md
```

## 快速开始

### 方式一：Docker 部署（推荐）

```bash
# 1. 复制环境变量文件并填入 API Key
cp .env.example .env
# 编辑 .env，填入你的 API Key

# 2. 构建并启动
docker compose up -d --build

# 或者
docker build -t han/smart-router:2.0.0 .
docker compose up -d

# 3. 查看日志
docker compose logs -f

# 4. 健康检查
curl http://localhost:8000/health

# 5. 访问控制面板
# 浏览器打开 http://localhost:8000/admin
# 默认密码: admin（强烈建议修改）
```

如需持久化配置文件和长期保存数据，建议将容器内的 `/app` 目录映射出来：

```bash
docker run -d \
  --restart=unless-stopped \
  --name ai-smart-router \
  -e TZ=Asia/Shanghai\
  -p 8000:8000 \
  -v smart-router-data:/app/data \
  -v ./config.yaml:/app/config.yaml:ro \
  -v ./models:/app/models:ro \
  --env-file .env \
  han/smart-router:1.0.0
```


### 方式二：本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动服务
python main.py

# 3. 客户端配置
# 将任何支持 OpenAI 格式的客户端 Base URL 设置为
# http://localhost:8000/v1
```

### 方式三：OpenClaw 插件模式

```bash
# 1. 将 openclaw-smart-router 文件夹放入 OpenClaw 的 plugins/ 目录
cp -r openclaw-smart-router /path/to/openclaw/plugins/

# 2. 在 OpenClaw 主配置中启用
# plugins: ["openclaw-smart-router"]

# 3. 随 OpenClaw 启动自动加载，预测模型常驻内存

# 4. 在 OpenClaw 的"自定义前端脚本"设置中粘贴
#    scripts/inject_feedback.js 启用反馈按钮
```

## 智能路由模式

SmartRouter 支持两种 API 调用模式，通过 `model` 字段区分：

### `model="auto"` — 智能路由模式

系统自动预测 Prompt 难度和任务类型，选择最优模型路由。这是默认模式。

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### `model="具体模型名"` — 直连代理模式

直接将请求代理到指定模型，不经过智能路由决策。

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 路由来源标识

每次请求的路由来源会记录在日志中：

| route_source | 说明 |
|--------------|------|
| `auto` | 智能路由模式，系统自动选择模型 |
| `direct` | 直连模式，用户指定了具体模型名 |
| `cache` | 缓存命中，复用之前的路由结果 |
| `fallback` | 降级路由，无可用模型时使用兜底模型 |

### 准备模型接口

在发送实际请求前，可以先查询系统会推荐哪个模型：

```bash
curl -X POST http://localhost:8000/v1/prepare-model \
  -H "Content-Type: application/json" \
  -d '{"prompt": "帮我写一个Python快速排序算法"}'
```

返回示例：

```json
{
  "predicted_difficulty": 4,
  "estimated_tokens": 800,
  "task_type": "coding",
  "recommended_model": "deepseek-chat",
  "route_source": "auto",
  "model_info": {
    "name": "deepseek-chat",
    "api_type": "openai",
    "capability": 4,
    "price_input": 0.00014,
    "price_output": 0.00028,
    "price_currency": "USD",
    "task_types": ["coding", "reasoning"]
  }
}
```

## 配置说明

### config.yaml 核心字段

```yaml
# 管理面板密码（默认 admin，强烈建议修改）
admin_password: "admin"

# API Key 认证（为空则不启用，设置后 /v1 接口需携带 Authorization: Bearer <key>）
api_key: ""

# 默认模型（冷启动降级使用）
default_model: "deepseek-chat"

# 兜底模型（无法路由时使用）
fallback_model: "deepseek-chat"

# Prompt 缓存 TTL（秒）
cache_ttl_seconds: 300

# 余额缓存 TTL（秒）
balance_cache_seconds: 300

# 价格同步间隔（小时）
price_sync_interval_hours: 6

# 汇率同步间隔（小时）
exchange_rate_sync_interval_hours: 12

# 日志保存天数（0=永久保存）
log_retention_days: 0

# 训练样本新增标记持续时间（秒），超时后 NEW 标记自动消失
new_mark_ttl_seconds: 3600

# 显示货币单位（CNY/USD）
currency: "CNY"

# ONNX 模型路径（缺失时自动降级为哈希特征）
onnx_model_path: "models/minilm.onnx"

# 模型名映射（别名），使客户端可以用 gpt-4 等名称连接
model_aliases:
  gpt-4: "deepseek-chat"
  gpt-3.5-turbo: "qwen2.5-7b-instruct"

# 模型列表
models:
  - name: "deepseek-chat"              # 模型名
    api_type: "openai"                  # 决定余额查询策略和默认 base_url
    base_url: "https://api.deepseek.com/v1"  # 上游 API 地址
    api_key: ""                         # 留空则用环境变量
    litellm_name: "deepseek/deepseek-chat"    # 用于 litellm 价格同步
    params_b: 67                        # 参数量(B)，自动换算能力等级
    capability: 4                       # 1-5 能力等级（不设则从 params_b 自动计算）
    task_types: ["coding", "reasoning"] # 适合的任务类型
    price_input: 0.00014                # USD / 1M tokens
    price_output: 0.00028
    price_currency: "USD"
    active_hours: "09:00-23:00"         # 生效时间段（格式: HH:MM-HH:MM），支持列表和跨天，留空或不设则全天生效

  - name: "glm-4-flash"
    api_type: "zhipu"
    base_url: "https://open.bigmodel.cn/api/paas/v4"
    litellm_name: "zhipu/glm-4-flash"
    params_b: 9
    capability: 4
    price_input: 0.0001
    price_output: 0.0001

  - name: "qwen2.5-7b-instruct"        # 免费模型，路由优先级最高
    api_type: "openai"
    base_url: "https://api.siliconflow.cn/v1"
    litellm_name: "siliconflow/qwen2.5-7b-instruct"
    params_b: 7
    capability: 3
    price_input: 0.0
    price_output: 0.0
```

### 支持的 api_type 及余额查询

| api_type | 余额查询 | 默认 base_url |
|----------|----------|---------------|
| `openai` | OpenAI billing API | `https://api.openai.com/v1` |
| `deepseek` | DeepSeek 余额接口 | `https://api.deepseek.com/v1` |
| `zhipu` | 智谱余额接口 | `https://open.bigmodel.cn/api/paas/v4` |
| `siliconflow` | SiliconFlow 余额接口 | `https://api.siliconflow.cn/v1` |
| `aliyun` | 阿里云 DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `local` | 本地扣账估算 | 无（需手动配置 base_url） |

> 所有 api_type 均使用 OpenAI 兼容接口格式代理请求。可自定义注册余额检查器。

### 模型生效时间（active_hours）

每个模型可配置生效时间段，在生效时间之外该模型会被自动禁用（不参与智能路由），默认全天生效。支持多个时间段和跨天时间段。

**配置格式：** `HH:MM-HH:MM`，支持字符串或列表

```yaml
models:
  - name: "deepseek-chat"
    active_hours: "09:00-18:00"    # 仅在 9:00-18:00 生效
    # ...

  - name: "glm-4-flash"
    active_hours: "21:00-09:00"    # 跨天：晚21点到次日上午9点
    # ...

  - name: "qwen2.5-7b-instruct"
    active_hours:                   # 多个时间段，任一匹配即生效
      - "09:00-12:00"              # 上午 9:00-12:00
      - "14:00-18:00"              # 下午 14:00-18:00
    # ...

  - name: "claude-3-haiku"
    # 不设 active_hours 或设为空，表示全天生效
    # ...
```

**行为说明：**

| 场景 | 行为 |
|------|------|
| `active_hours` 未设置或为空 | 全天生效（默认） |
| 单个字符串 `"09:00-18:00"` | 仅在该时间段内生效 |
| 列表 `["09:00-12:00", "14:00-18:00"]` | 多个时间段，任一匹配即生效 |
| 跨天时间段 `"21:00-09:00"` | 晚21点到次日上午9点生效 |
| 当前时间在任一生效时间段内 | 模型正常参与路由 |
| 当前时间不在任何生效时间段内 | 模型被排除，不参与智能路由选择 |
| 格式解析失败 | 降级为全天生效 |

**Web 面板操作：** 模型管理页面中，生效时间列支持动态添加/删除多个时间段，显示绿色圆点（生效中/全天）或灰色圆点（未生效），移动端卡片视图显示"全天"/"生效中"/"未生效"文字状态。

### 环境变量

```bash
# 管理面板密码（覆盖 config.yaml 中的 admin_password）
SMARTROUTER_ADMIN_PASSWORD="your-password"

# JWT 签名密钥（留空则自动从 admin 密码派生）
SMARTROUTER_JWT_SECRET="your-jwt-secret"

# /v1 接口 API Key 认证（留空不启用）
SMARTROUTER_API_KEY="sk-your-router-key"

# 模型 API Key 覆盖（命名规则：SMARTROUTER_API_KEY_<模型名大写，- 替换为 _>）
SMARTROUTER_API_KEY_DEEPSEEK_CHAT="sk-xxx"
SMARTROUTER_API_KEY_GLM_4_FLASH="xxx"
SMARTROUTER_API_KEY_GPT_4O_MINI="sk-xxx"
SMARTROUTER_API_KEY_QWEN2_5_7B_INSTRUCT="sk-xxx"
```

## API 接口

### 智能路由代理（OpenAI 兼容）

```bash
# 智能路由模式（model="auto" 或 model=""）
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'

# 直连代理模式（指定具体模型名）
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'

# 启用 API Key 认证时
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-router-key" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 模型列表

返回所有可用模型，首位为 `auto` 智能路由模式标识：

```bash
curl http://localhost:8000/v1/models
```

返回示例：

```json
{
  "object": "list",
  "data": [
    {"id": "auto", "object": "model", "owned_by": "smart-router"},
    {"id": "deepseek-chat", "object": "model", "owned_by": "openai"},
    {"id": "glm-4-flash", "object": "model", "owned_by": "zhipu"},
    {"id": "qwen2.5-7b-instruct", "object": "model", "owned_by": "openai"}
  ]
}
```

### 准备模型

预测 Prompt 的难度、任务类型，并返回推荐模型：

```bash
curl -X POST http://localhost:8000/v1/prepare-model \
  -H "Content-Type: application/json" \
  -d '{"prompt": "帮我写一个Python快速排序算法"}'
```

### 反馈上报

```bash
curl -X POST http://localhost:8000/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "req_xxx",
    "sentiment": "positive",
    "context_snapshot": "用户原始问题"
  }'
```

### 管理面板 API

所有 `/admin/api/*` 接口需要 JWT Token 认证（登录接口除外）。

| 路径 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/admin/api/login` | POST | 管理面板登录，返回 JWT Token | 无 |
| `/admin/api/change-password` | POST | 修改管理密码 | JWT |
| `/admin/api/dashboard` | GET | 仪表盘统计 | JWT |
| `/admin/api/models` | GET | 模型列表（含余额、生效状态） | JWT |
| `/admin/api/models` | POST | 保存模型配置 | JWT |
| `/admin/api/models/{name}/clone` | POST | 克隆模型 | JWT |
| `/admin/api/models/{name}/config` | GET | 获取单个模型原始配置 | JWT |
| `/admin/api/models/{name}/config` | PUT | 更新单个模型配置 | JWT |
| `/admin/api/models/{name}/test` | POST | 测试模型（刷新余额） | JWT |
| `/admin/api/metrics` | GET | 模型聚合指标 | JWT |
| `/admin/api/feedback/negative` | GET | 负向反馈列表 | JWT |
| `/admin/api/predictor/status` | GET | 预测器状态 | JWT |
| `/admin/api/task-type/stats` | GET | 任务类型统计 | JWT |
| `/admin/api/config` | GET | 获取全局配置 | JWT |
| `/admin/api/config` | POST | 更新全局配置 | JWT |
| `/admin/api/sync-prices` | POST | 手动触发价格同步 | JWT |
| `/admin/api/exchange-rate/status` | GET | 汇率状态 | JWT |
| `/admin/api/exchange-rate/sync` | POST | 手动同步汇率 | JWT |
| `/admin/api/reload-config` | POST | 重新加载配置文件 | JWT |
| `/admin/api/training-samples` | GET | 获取训练样本列表 | JWT |
| `/admin/api/training-samples` | POST | 添加训练样本 | JWT |
| `/admin/api/training-samples/{id}` | PUT | 更新训练样本 | JWT |
| `/admin/api/training-samples/{id}` | DELETE | 删除训练样本 | JWT |
| `/admin/api/training-samples/batch` | POST | 批量导入训练样本 | JWT |
| `/admin/api/training-samples/retrain` | POST | 从训练集重新训练模型 | JWT |
| `/admin/api/route-logs` | GET | 获取路由日志列表 | JWT |
| `/admin/api/route-logs` | DELETE | 清除路由日志 | JWT |
| `/admin/api/route-logs/stats` | GET | 路由日志统计 | JWT |
| `/health` | GET | 健康检查 | 无 |

#### 登录示例

```bash
# 登录获取 Token
TOKEN=$(curl -s -X POST http://localhost:8000/admin/api/login \
  -H "Content-Type: application/json" \
  -d '{"password": "admin"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# 使用 Token 访问管理 API
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/admin/api/dashboard
```

## Web 控制面板

浏览器访问 `http://localhost:8000/admin`，默认密码 `admin`。

### 功能模块

| Tab | 功能 |
|-----|------|
| **仪表盘** | 今日转发次数、累计花费、平均延迟、反馈统计；模型调用占比饼图、每日趋势折线图、任务类型分布 |
| **预测引擎** | 引擎状态/特征提取方式/已训练样本/缓存统计；任务类型检测器权重可视化；**训练集管理**（增删改查、批量导入、一键重训、来源过滤、分页、NEW 标记） |
| **模型管理** | 模型列表 CRUD（名称/API类型/Base URL/参数量/能力/适合任务/价格/生效时间/API Key/余额）；同步价格；移动端卡片式布局 |
| **调用统计** | 调用统计详情图表；模型聚合指标表（总调用/成功率/满意度/反馈/余额） |
| **反馈中心** | 不认可对话详情（人工复盘） |
| **路由日志** | 路由日志列表（时间/路由来源/请求模型/路由模型/Prompt预览/难度/类型/花费/延迟/状态）；按模型/来源过滤；统计卡片；清除日志 |
| **路由测试** | 输入 Prompt 测试路由，显示预测难度/Token/任务类型/推荐模型详情 |
| **系统配置** | 系统配置、日志管理（保存天数/新增标记时长）、货币汇率、模型别名、API Key 认证、修改密码 |

### 训练集管理

- **添加样本**：手动输入 Prompt、难度(1-5)、预估 Token、任务类型
- **批量导入**：每行格式 `prompt|difficulty|est_tokens|task_type`
- **编辑/删除**：直接在列表中操作
- **重新训练**：一键从训练集重新训练预测模型
- **NEW 标记**：新增样本显示绿色 NEW 标记，超时后自动消失（时长可在系统配置中调整）

### 移动端适配

Web 控制面板全面支持响应式布局和移动端访问：

| 设备 | 适配策略 |
|------|----------|
| 桌面端（≥1024px） | 表格视图，多列网格布局 |
| 平板端（768-1023px） | 表格视图，自适应列宽 |
| 手机端（<1024px） | 模型管理使用卡片式布局替代表格；统计卡片2列排列；按钮组自动换行 |

- 仪表盘统计卡片：手机端2列、平板3列、桌面6列
- 图表区域：移动端单列堆叠，桌面端3列并排
- 系统配置表单：移动端单列，桌面端双列
- 导航栏：支持横向滚动，小屏幕缩小字体

## 测试

```bash
cd openclaw-smart-router
pip install pytest
python -m pytest tests/ -v
```

## 架构设计

### 三层架构

```
┌─────────────────────────────────────────────────────┐
│           Data & UI Layer 交互层                     │
│  SQLite · Vue3 面板 · JWT 认证 · inject_feedback.js │
└─────────────────────────────────────────────────────┘
                          ▲
┌─────────────────────────────────────────────────────┐
│           Adapter Layer 适配层                       │
│  ┌─────────────────┐  ┌──────────────────────────┐  │
│  │ Plugin Adapter  │  │ Standalone Adapter       │  │
│  │ OpenClaw 钩子   │  │ FastAPI HTTP 网关        │  │
│  └─────────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────┘
                          ▲
┌─────────────────────────────────────────────────────┐
│           SmartRouter Core 内核                      │
│  预测引擎 · 决策器 · 价格余额管理 · 认证 · 在线学习器│
└─────────────────────────────────────────────────────┘
```

### 路由决策公式

```
Score = Cost / (Reliability x Satisfaction + 0.01)
```

- **Cost** = (est_in_tokens x price_input + est_out_tokens x price_output) / 1000
- **Reliability** = 历史成功率（默认 0.9）
- **Satisfaction** = 用户正向反馈率（默认 0.9）
- 免费模型 Cost=0，优先级最高
- 选 Score 最小者

### 性能保障

| 策略 | 实现 |
|------|------|
| 零阻塞 | 预测同步 <30ms；DB 写入、余额查询、SGD 训练异步 |
| 冷启动降级 | 模型未初始化时路由到 `default_model` |
| 超时熔断 | 预测超时降级走默认路由 |
| 自动重试 | 目标模型报错重试 1 次，再失败标记可靠性降低 |
| Prompt 缓存 | 相同 MD5 5 分钟内复用，0ms 拦截 |
| 价格自动同步 | 每 6 小时从 litellm 拉取最新单价，支持手动触发 |

## 数据库表结构

### request_logs 调用记录表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| timestamp | REAL | 时间戳 |
| prompt_hash | TEXT | Prompt MD5 |
| predicted_difficulty | INTEGER | 预测难度 1-5 |
| actual_difficulty | INTEGER | 真实难度 1-5 |
| routed_model | TEXT | 路由到的模型 |
| cost | REAL | 实际花费 |
| cost_currency | TEXT | 货币单位（CNY/USD） |
| latency_ms | INTEGER | 延迟毫秒 |
| success | INTEGER | 0/1 |
| task_type | TEXT | 任务类型 |
| route_source | TEXT | 路由来源（auto/direct/cache/fallback） |
| prompt_preview | TEXT | Prompt 前 200 字符预览 |
| requested_model | TEXT | 请求中指定的模型名 |

### training_samples 训练样本表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| prompt | TEXT | 训练 Prompt |
| difficulty | INTEGER | 难度 1-5 |
| est_tokens | INTEGER | 预估 Token 数 |
| task_type | TEXT | 任务类型 |
| source | TEXT | 来源（auto/manual/batch_import） |
| is_new | INTEGER | 新增标记（1=新增，0=已归档） |
| new_mark_ttl | REAL | 新增标记持续时间（秒） |
| created_at | REAL | 创建时间 |
| updated_at | REAL | 更新时间 |

### model_metrics 模型聚合指标表

| 字段 | 类型 | 说明 |
|------|------|------|
| model_name | TEXT PK | 模型名 |
| success_rate | REAL | 成功率 |
| satisfaction_rate | REAL | 满意度 |
| total_calls | INTEGER | 总调用数 |
| success_calls | INTEGER | 成功调用数 |
| positive_feedback | INTEGER | 正向反馈数 |
| negative_feedback | INTEGER | 负向反馈数 |
| last_balance | REAL | 最近余额 |
| last_sync_time | REAL | 最近同步时间 |

### feedback_records 反馈表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| request_id | TEXT | 关联请求 ID |
| feedback_type | TEXT | explicit / implicit |
| sentiment | TEXT | positive / negative |
| context_snapshot | TEXT | 上下文快照 |
| timestamp | REAL | 时间戳 |

## ONNX 模型生成

项目内置降级机制，ONNX 缺失时使用哈希特征。如需完整 ONNX 特征：

```bash
pip install transformers torch onnx onnxscript
python scripts/download_minilm.py
```

生成 `models/minilm.onnx`（约 22MB），系统自动加载。

## License

MIT License
