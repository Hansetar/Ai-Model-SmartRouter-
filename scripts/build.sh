#!/bin/bash
# =====================================================================
# OpenClaw SmartRouter Docker 镜像构建脚本
# =====================================================================
# 用法：
#   ./build.sh              构建镜像
#   ./build.sh run          构建并启动容器
#   ./build.sh test         构建并运行测试
#   ./build.sh clean        清理镜像和容器
# =====================================================================

set -e

IMAGE_NAME="openclaw/smart-router"
IMAGE_TAG="1.0.0"
CONTAINER_NAME="openclaw-smart-router"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "============================================================"
echo "  OpenClaw SmartRouter Docker 镜像构建"
echo "  镜像: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  目录: ${PROJECT_DIR}"
echo "============================================================"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "错误: 未安装 Docker，请先安装 Docker"
    exit 1
fi

# 验证文件
echo ""
echo "[1/4] 验证项目文件..."
python scripts/validate_dockerfile.py

# 构建镜像
echo ""
echo "[2/4] 构建 Docker 镜像..."
docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" -t "${IMAGE_NAME}:latest" .

echo ""
echo "[3/4] 镜像信息："
docker images "${IMAGE_NAME}"

# 根据参数执行后续操作
case "${1:-build}" in
    build)
        echo ""
        echo "[4/4] 构建完成！"
        echo ""
        echo "启动容器："
        echo "  docker run -d --name ${CONTAINER_NAME} -p 8000:8000 ${IMAGE_NAME}:${IMAGE_TAG}"
        echo ""
        echo "或使用 docker-compose："
        echo "  docker compose up -d"
        ;;
    run)
        echo ""
        echo "[4/4] 启动容器..."
        docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
        docker run -d --name "${CONTAINER_NAME}" \
            -p 8000:8000 \
            -v "${PROJECT_DIR}/config.yaml:/app/config.yaml:ro" \
            -v "${PROJECT_DIR}/data:/app/data" \
            "${IMAGE_NAME}:${IMAGE_TAG}"
        echo ""
        echo "容器已启动：http://localhost:8000/admin"
        echo "查看日志：docker logs -f ${CONTAINER_NAME}"
        ;;
    test)
        echo ""
        echo "[4/4] 运行容器内测试..."
        docker run --rm "${IMAGE_NAME}:${IMAGE_TAG}" python -m pytest tests/ -v
        ;;
    clean)
        echo ""
        echo "[4/4] 清理..."
        docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
        docker rmi "${IMAGE_NAME}:${IMAGE_TAG}" "${IMAGE_NAME}:latest" 2>/dev/null || true
        echo "清理完成"
        ;;
    *)
        echo "用法: $0 {build|run|test|clean}"
        exit 1
        ;;
esac

echo ""
echo "============================================================"
echo "  完成！"
echo "============================================================"
