# ONNX 模型占位说明

本目录用于存放 `all-MiniLM-L6-v2` 的 ONNX 静态特征提取模型（约 22MB）。

由于模型文件较大且需联网下载，本仓库未直接包含。系统已内置降级机制：
- 当 `models/minilm.onnx` 不存在时，预测器自动使用哈希特征 + 线性模型
- 功能完全可用，仅特征质量略低

## 生成真实 ONNX 模型

```bash
pip install transformers torch onnx
python scripts/download_minilm.py
```

生成后文件位于 `models/minilm.onnx`，系统会自动加载并启用完整 ONNX 特征提取。
