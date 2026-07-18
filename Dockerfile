# =====================================================================
# SmartRouter v2.0 Dockerfile
# =====================================================================
# 多阶段构建：frontend + backend builder + runtime
# 支持：FastAPI/Litestar, SQLite/PostgreSQL/MySQL, ONNX+sklearn
#
# 构建标签：
#   --build-arg DOWNLOAD_ONNX=true   构建时下载ONNX模型(默认false)
#   --build-arg ONNX_MODEL_PATH=./models/model.onnx  预下载模型路径
#
# ONNX模型策略：
#   1. 优先从 ONNX_MODEL_PATH 复制预下载的模型文件
#   2. 若无预下载文件且 DOWNLOAD_ONNX=true，构建时下载
#   3. 若都没有，运行时自动检测并下载(降级到hash embedding)
# =====================================================================

# ---------- 阶段 1：前端构建 ----------
FROM node:20-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --registry=https://registry.npmmirror.com 2>/dev/null || npm install
COPY frontend/ ./
RUN npm run build
# 构建产物在 /frontend/dist/

# ---------- 阶段 2：后端依赖构建 ----------
FROM python:3.11-slim AS backend-builder

ARG DOWNLOAD_ONNX=false
ARG ONNX_MODEL_PATH=""

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 先复制依赖文件，利用 Docker 缓存
COPY pyproject.toml ./
COPY src/ ./src/

# 安装项目及依赖
RUN pip install --prefix=/install -i https://pypi.tuna.tsinghua.edu.cn/simple . || \
    pip install --prefix=/install .

# ONNX 模型处理
# 策略1: 如果有预下载的模型文件，直接复制
ONBUILD ARG ONNX_MODEL_PATH=""
ONBUILD RUN echo "ONNX model will be handled at runtime stage"

# 如果指定了 DOWNLOAD_ONNX=true，构建时下载
COPY docker_download_onnx.py /tmp/download_onnx.py
RUN if [ "$DOWNLOAD_ONNX" = "true" ]; then \
        pip install httpx && python3 /tmp/download_onnx.py || echo "ONNX download failed, will retry at runtime"; \
    else \
        echo "ONNX download skipped (DOWNLOAD_ONNX=false)"; \
    fi

# ---------- 阶段 3：运行时 ----------
FROM python:3.11-slim AS runtime

ARG DOWNLOAD_ONNX=false
ARG ONNX_MODEL_PATH=""

# 元数据
LABEL maintainer="Han Team" \
      org.opencontainers.image.title="SmartRouter" \
      org.opencontainers.image.description="Intelligent LLM Model Routing Engine v2.0 - Dual-path ML/RL + Scoring" \
      org.opencontainers.image.version="2.0.0"

# 安装运行时必要系统依赖（libgomp1 用于 onnxruntime, gosu 用于权限降级）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
        gosu \
    && rm -rf /var/lib/apt/lists/*

# 复制 Python 依赖
COPY --from=backend-builder /install /usr/local

# 创建非 root 用户
RUN useradd -m -u 1000 -s /bin/bash smartrouter

# 工作目录
WORKDIR /app

# 复制项目文件
COPY --chown=smartrouter:smartrouter main.py .
COPY --chown=smartrouter:smartrouter config.yaml .
COPY --chown=smartrouter:smartrouter src/ ./src/

# 复制前端构建产物到 web/dist
COPY --from=frontend-builder --chown=smartrouter:smartrouter /frontend/dist ./web/dist/

# ONNX 模型处理
# 策略1: 从本地 models/ 目录复制预下载的模型文件（model.onnx 或 minilm.onnx）
# 策略2: 从构建阶段复制（如果 DOWNLOAD_ONNX=true 且下载成功）
# 策略3: 都没有则运行时自动检测并下载(降级到hash embedding)
#
# 使用方式：
#   默认(使用本地模型): 先下载到 models/model.onnx，再 docker compose build
#   构建时下载:         docker compose build --build-arg DOWNLOAD_ONNX=true
#   无模型(运行时下载): 直接 docker compose build（无本地模型文件时）

# 从本地 models/ 复制模型文件（如果存在）
COPY models/ ./models/

# 从构建阶段覆盖（如果构建时下载了更新的版本）
COPY --from=backend-builder --chown=smartrouter:smartrouter /build/model.onnx* ./models/

# 创建数据目录和必要目录
RUN mkdir -p /app/data /app/data/models /app/models /app/config_backups && \
    chown -R smartrouter:smartrouter /app

# 复制启动脚本
COPY --chown=smartrouter:smartrouter docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# 注意：不在此处切换 USER，entrypoint 以 root 启动以修复挂载目录权限
# entrypoint 内部会使用 gosu 降权到 smartrouter 用户运行应用

# 环境变量
ENV SMARTROUTER_HOST=0.0.0.0 \
    SMARTROUTER_PORT=8000 \
    SMARTROUTER_LOG_LEVEL=info \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src:/app

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
ENTRYPOINT ["/app/docker-entrypoint.sh"]
