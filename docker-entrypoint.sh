#!/bin/bash
# =====================================================================
# SmartRouter v2.0 Docker Entrypoint
# =====================================================================
# 功能：
#   1. 以 root 修复挂载目录权限
#   2. 检测数据目录，如果映射目录为空则从镜像内复制标准模型
#   3. 检测数据库文件，如果不存在则初始化
#   4. 使用 gosu 降权到 smartrouter 用户运行应用
#   5. 容错启动：模型/配置缺失时不crash，降级启动Web服务
# =====================================================================

echo "[entrypoint] SmartRouter v2.0 starting..."

# --- 修复挂载目录权限 ---
# docker-compose 挂载的目录可能属于 root，需要修复为 smartrouter
if [ "$(id -u)" = "0" ]; then
    echo "[entrypoint] Running as root, fixing permissions..."
    chown -R smartrouter:smartrouter /app/data 2>/dev/null || true
    chown smartrouter:smartrouter /app/config.yaml 2>/dev/null || true
fi

# --- 模型文件持久化 ---
# 镜像内标准模型保存在 /app/models/ (构建时复制)
# 持久化目录为 /app/data/models/ (docker-compose映射)
# 如果持久化目录为空，从镜像内复制标准模型

MODELS_SRC="/app/models"
MODELS_DST="/app/data/models"

if [ -d "$MODELS_SRC" ] && [ "$(ls -A $MODELS_SRC 2>/dev/null)" ]; then
    echo "[entrypoint] Found built-in models in $MODELS_SRC"
    
    # Create destination if not exists
    mkdir -p "$MODELS_DST" 2>/dev/null || true
    
    # If destination is empty, copy from source
    if [ -z "$(ls -A $MODELS_DST 2>/dev/null)" ]; then
        echo "[entrypoint] Persistent models directory is empty, copying built-in models..."
        if cp -v "$MODELS_SRC"/* "$MODELS_DST/" 2>/dev/null; then
            echo "[entrypoint] Built-in models copied to $MODELS_DST"
        else
            # Try with explicit file copy (glob may fail on permissions)
            COPY_OK=0
            for f in "$MODELS_SRC"/*; do
                [ -f "$f" ] || continue
                fname=$(basename "$f")
                if cp "$f" "$MODELS_DST/$fname" 2>/dev/null; then
                    echo "[entrypoint]   Copied: $fname"
                    COPY_OK=1
                else
                    echo "[entrypoint]   WARNING: Failed to copy $fname"
                fi
            done
            if [ "$COPY_OK" -eq 1 ]; then
                echo "[entrypoint] Built-in models partially copied to $MODELS_DST"
            else
                echo "[entrypoint] WARNING: Failed to copy built-in models, will use hash fallback"
            fi
        fi
    else
        echo "[entrypoint] Persistent models directory has files, skipping copy"
    fi
else
    echo "[entrypoint] WARNING: No built-in models found in $MODELS_SRC, will use hash fallback"
    mkdir -p "$MODELS_DST" 2>/dev/null || true
fi

# --- 数据库初始化 ---
# 数据库默认路径: /app/data/smart_router.db
# 如果不存在，将在应用启动时自动创建

DATA_DIR="/app/data"
mkdir -p "$DATA_DIR" 2>/dev/null || {
    echo "[entrypoint] WARNING: Failed to create data directory $DATA_DIR"
}

if [ -f "$DATA_DIR/smart_router.db" ]; then
    echo "[entrypoint] Database file exists: $DATA_DIR/smart_router.db"
else
    echo "[entrypoint] Database file not found, will be created on first run"
fi

# --- 配置文件检测 ---
CONFIG_FILE="/app/config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    echo "[entrypoint] Config file exists: $CONFIG_FILE"
else
    echo "[entrypoint] WARNING: No config.yaml found, will use defaults"
fi

# --- 启动应用 ---
# 容错启动：即使应用启动失败，也尝试保持容器运行
echo "[entrypoint] Starting SmartRouter..."

# 如果以 root 运行，使用 gosu 降权到 smartrouter
if [ "$(id -u)" = "0" ]; then
    exec gosu smartrouter python main.py || {
        EXIT_CODE=$?
        echo "[entrypoint] ERROR: SmartRouter exited with code $EXIT_CODE"
        echo "[entrypoint] Keeping container alive for debugging..."
        sleep infinity
    }
else
    python main.py || {
        EXIT_CODE=$?
        echo "[entrypoint] ERROR: SmartRouter exited with code $EXIT_CODE"
        echo "[entrypoint] Keeping container alive for debugging..."
        sleep infinity
    }
fi
