"""
scripts/download_minilm.py
==========================
下载并转换 all-MiniLM-L6-v2 为 ONNX 格式。

运行方式（需要联网 + transformers + torch）：
    pip install transformers torch onnx
    python scripts/download_minilm.py

输出：models/minilm.onnx （约 22MB）

注意：本脚本仅在需要重新生成 ONNX 模型时运行。
项目已内置降级机制，ONNX 缺失时自动使用哈希特征。
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
        import onnx
        from onnx import helper
    except ImportError:
        print(
            "请先安装依赖：pip install transformers torch onnx",
            file=sys.stderr,
        )
        return 1

    model_name = "sentence-transformers/all-MiniLM-L6-v2"
    output_dir = Path(__file__).resolve().parent.parent / "models"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "minilm.onnx"

    print(f"加载模型: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name)
    model.eval()

    # 示例输入
    dummy_input = tokenizer(
        "hello world",
        return_tensors="pt",
        padding="max_length",
        max_length=128,
        truncation=True,
    )

    print(f"导出 ONNX: {output_path}")
    torch.onnx.export(
        model,
        (dummy_input["input_ids"], dummy_input["attention_mask"], dummy_input.get("token_type_ids")),
        str(output_path),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "sequence"},
            "attention_mask": {0: "batch", 1: "sequence"},
            "token_type_ids": {0: "batch", 1: "sequence"},
            "last_hidden_state": {0: "batch", 1: "sequence"},
        },
        opset_version=14,
    )

    print(f"完成: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
