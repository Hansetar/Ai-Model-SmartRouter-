# =====================================================================
# OpenClaw SmartRouter Dockerfile
# =====================================================================
# 多阶段构建：builder 安装依赖，runtime 仅复制必要文件
# 镜像大小约 350MB（含 onnxruntime + scikit-learn）
# =====================================================================

# ---------- 阶段 1：依赖构建 ----------
FROM python:3.11-slim AS builder

# 设置 pip 镜像加速（国内构建）
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 先复制依赖文件，利用 Docker 缓存
COPY requirements.txt .

# 安装依赖到 /install 目录
RUN pip install --prefix=/install -r requirements.txt

# ---------- 阶段 2：运行时 ----------
FROM python:3.11-slim AS runtime

# 元数据
LABEL maintainer="Ai SmartRouter Team" \
      org.opencontainers.image.title="Ai SmartRouter" \
      org.opencontainers.image.description="双模式智能路由插件 - 独立 API 代理网关" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.source="https://github.com/Hansetar/Ai-Model-SmartRouter-.git"

# 安装运行时必要系统依赖（libgomp1 用于 onnxruntime）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 复制 Python 依赖
COPY --from=builder /install /usr/local

# 创建非 root 用户
RUN useradd -m -u 1000 -s /bin/bash smartrouter

# 工作目录
WORKDIR /app

# 复制项目文件
COPY --chown=smartrouter:smartrouter . /app/

# 创建数据目录
RUN mkdir -p /app/data /app/models && \
    chown -R smartrouter:smartrouter /app

# 切换非 root 用户
USER smartrouter

# 环境变量
ENV SMARTROUTER_HOST=0.0.0.0 \
    SMARTROUTER_PORT=8000 \
    SMARTROUTER_LOG_LEVEL=info \
    SMARTROUTER_DB_PATH=/app/data/smart_router.db \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
CMD ["python", "main.py"]
