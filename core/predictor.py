"""
core/predictor.py
=================
毫秒级在线学习预测引擎。

架构：静态层 ONNX(all-MiniLM-L6-v2) 提取 384 维向量 + 动态层 SGD 增量学习。
- predict() 同步执行，要求 <30ms
- add_sample() 异步入队，后台线程 partial_fit 增量训练
- 冷启动降级：未初始化时返回 (3, 500)
- ONNX 模型缺失时自动降级为哈希特征 + 线性模型，保证可运行
"""

from __future__ import annotations

import hashlib
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

try:
    import onnxruntime as ort  # type: ignore

    _HAS_ONNX = True
except Exception:  # noqa: BLE001
    _HAS_ONNX = False

try:
    from sklearn.linear_model import SGDClassifier, SGDRegressor  # type: ignore

    _HAS_SKLEARN = True
except Exception:  # noqa: BLE001
    _HAS_SKLEARN = False


# 难度等级 1-5
_DIFF_CLASSES = np.array([1, 2, 3, 4, 5])


class OnlinePredictor:
    """毫秒级在线学习预测器。"""

    def __init__(self, onnx_path: Optional[str] = None) -> None:
        self._onnx_path: Optional[str] = onnx_path
        self._embedder = None
        self._fallback_dim = 384
        self._is_initialized = False
        self._lock = threading.Lock()

        # 初始化分类器与回归器
        if _HAS_SKLEARN:
            self._clf = SGDClassifier(loss="log_loss", max_iter=1, tol=1e-3)
            self._reg = SGDRegressor(max_iter=1, tol=1e-3)
        else:
            self._clf = None
            self._reg = None

        # 加载 ONNX 静态特征提取器
        self._load_embedder()

        # 训练队列 + 后台线程
        self._train_queue: "queue.Queue[tuple]" = queue.Queue()
        self._stop_event = threading.Event()
        self._total_trained = 0
        self._trainer_thread = threading.Thread(
            target=self._bg_trainer, daemon=True, name="online-trainer"
        )
        self._trainer_thread.start()

        # Prompt 缓存（5 分钟），实现 0ms 拦截
        self._cache: dict[str, Tuple[float, Tuple[int, int]]] = {}
        self._cache_ttl = 300

    # ------------------------------------------------------------------ #
    # ONNX 加载
    # ------------------------------------------------------------------ #
    def _load_embedder(self) -> None:
        if not _HAS_ONNX:
            print(
                "[Predictor] onnxruntime 未安装，启用降级模式（哈希特征）",
                file=sys.stderr,
            )
            return
        if not self._onnx_path or not Path(self._onnx_path).exists():
            print(
                f"[Predictor] ONNX 模型不存在: {self._onnx_path}，启用降级模式",
                file=sys.stderr,
            )
            return
        try:
            self._embedder = ort.InferenceSession(
                self._onnx_path, providers=["CPUExecutionProvider"]
            )
            print(f"[Predictor] ONNX 模型加载成功: {self._onnx_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"[Predictor] ONNX 加载失败: {exc}，启用降级模式", file=sys.stderr)
            self._embedder = None

    # ------------------------------------------------------------------ #
    # 特征提取
    # ------------------------------------------------------------------ #
    def _get_embedding(self, prompt: str) -> np.ndarray:
        """提取 384 维特征向量。ONNX 不可用时降级为哈希特征。"""
        if self._embedder is not None:
            try:
                # all-MiniLM-L6-v2 tokenizer 简化处理：按字符切分
                # 真实场景应使用 transformers tokenizer
                tokens = self._simple_tokenize(prompt)
                inputs = {self._embedder.get_inputs()[0].name: tokens}
                output = self._embedder.run(None, inputs)[0]
                # 平均池化
                vec = np.mean(output, axis=0, keepdims=True).astype(np.float32)
                return vec
            except Exception as exc:  # noqa: BLE001
                print(f"[Predictor] ONNX 推理失败: {exc}", file=sys.stderr)

        # 降级：基于哈希的 384 维稀疏特征
        return self._hash_embedding(prompt)

    def _simple_tokenize(self, text: str) -> np.ndarray:
        """简化分词：按字符 + 空格切分，截断/填充到 128。"""
        max_len = 128
        # 简单按字符切（中英文都适用）
        chars = list(text)[:max_len]
        ids = [ord(c) % 30000 for c in chars]
        while len(ids) < max_len:
            ids.append(0)
        return np.array([ids], dtype=np.int64)

    def _hash_embedding(self, prompt: str, dim: int = 384) -> np.ndarray:
        """哈希降级特征：稳定、零依赖。"""
        vec = np.zeros((1, dim), dtype=np.float32)
        # 字符 n-gram 哈希
        text = prompt.lower()
        for i in range(len(text)):
            for n in (1, 2, 3):
                gram = text[i : i + n]
                if not gram:
                    continue
                h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
                vec[0, h % dim] += 1.0
        # L2 归一化
        norm = np.linalg.norm(vec) + 1e-9
        return vec / norm

    # ------------------------------------------------------------------ #
    # 同步预测（要求 <30ms）
    # ------------------------------------------------------------------ #
    def predict(self, prompt: str) -> Tuple[int, int]:
        """同步预测难度(1-5)与预估输出 Token 数。"""
        t0 = time.perf_counter()

        # 缓存命中
        cache_key = hashlib.md5(prompt.encode("utf-8")).hexdigest()
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0] < self._cache_ttl):
            return cached[1]

        # 冷启动降级
        if not self._is_initialized or self._clf is None or self._reg is None:
            return 3, 500

        try:
            embedding = self._get_embedding(prompt)
            with self._lock:
                difficulty = int(self._clf.predict(embedding)[0])
                est_tokens = max(1, int(self._reg.predict(embedding)[0]))
        except Exception as exc:  # noqa: BLE001
            print(f"[Predictor] predict failed: {exc}", file=sys.stderr)
            return 3, 500

        # 写缓存
        self._cache[cache_key] = (time.time(), (difficulty, est_tokens))
        # 清理过期缓存
        if len(self._cache) > 10000:
            now = time.time()
            self._cache = {
                k: v for k, v in self._cache.items() if now - v[0] < self._cache_ttl
            }

        elapsed_ms = (time.perf_counter() - t0) * 1000
        if elapsed_ms > 50:
            print(
                f"[Predictor] slow predict: {elapsed_ms:.1f}ms",
                file=sys.stderr,
            )
        return difficulty, est_tokens

    def predict_with_model(self, prompt: str) -> dict:
        """预测难度、Token 数，并返回推荐模型及候选模型列表。

        :return: dict with keys: difficulty, est_tokens, task_type, expected_model, candidate_models
        """
        from .router import detect_task_type, router

        diff, est_tokens = self.predict(prompt)
        task_type = detect_task_type(prompt)
        selected = router.select_model(diff, est_tokens, est_tokens, task_type=task_type)
        expected_model = selected["name"] if selected else None

        # 获取候选模型列表（按综合评分排序的前3名）
        candidate_models = []
        try:
            candidates = router.select_model_candidates(diff, est_tokens, est_tokens, task_type=task_type, top_k=3)
            candidate_models = [
                {"name": c["model"]["name"], "combined_score": round(c["combined_score"], 4), "cost": round(c["cost"], 6)}
                for c in candidates
            ]
        except Exception:
            pass

        return {
            "difficulty": diff,
            "est_tokens": est_tokens,
            "task_type": task_type,
            "expected_model": expected_model,
            "candidate_models": candidate_models,
        }

    # ------------------------------------------------------------------ #
    # 异步训练
    # ------------------------------------------------------------------ #
    def add_sample(self, prompt: str, actual_difficulty: int, actual_tokens: int,
                   task_type: Optional[str] = None, source: str = "auto",
                   model_name: Optional[str] = None) -> None:
        """请求结束后，将真实结果送入队列实时训练，并异步持久化到数据库。"""
        self._train_queue.put((prompt, int(actual_difficulty), int(actual_tokens), task_type, source, model_name))

    def _bg_trainer(self) -> None:
        """后台线程：实时增量训练 + 异步持久化样本到数据库。"""
        while not self._stop_event.is_set():
            try:
                item = self._train_queue.get(timeout=1)
                # 兼容新旧队列格式
                if len(item) == 3:
                    prompt, diff, tokens = item
                    task_type, source, model_name = None, "auto", None
                elif len(item) == 5:
                    prompt, diff, tokens, task_type, source = item
                    model_name = None
                else:
                    prompt, diff, tokens, task_type, source, model_name = item
            except queue.Empty:
                continue
            try:
                embedding = self._get_embedding(prompt)
                y_clf = np.array([diff])
                y_reg = np.array([tokens], dtype=np.float64)
                with self._lock:
                    if not self._is_initialized:
                        if self._clf is not None and self._reg is not None:
                            # 首次训练：partial_fit 需要传入 classes 参数
                            self._clf.partial_fit(embedding, y_clf, classes=_DIFF_CLASSES)
                            self._reg.partial_fit(embedding, y_reg)
                            self._is_initialized = True
                    else:
                        self._clf.partial_fit(embedding, y_clf, classes=_DIFF_CLASSES)
                        self._reg.partial_fit(embedding, y_reg)
                    self._total_trained += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[Predictor] train failed: {exc}", file=sys.stderr)
            # 异步持久化到数据库
            try:
                from .database import db
                from .config import config
                db.add_training_sample(
                    prompt=prompt,
                    difficulty=diff,
                    est_tokens=tokens,
                    task_type=task_type,
                    model_name=model_name,
                    source=source,
                    new_mark_ttl=float(config.new_mark_ttl_seconds),
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[Predictor] persist sample failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    @property
    def is_ready(self) -> bool:
        return self._is_initialized

    @property
    def queue_size(self) -> int:
        return self._train_queue.qsize()

    @property
    def has_onnx(self) -> bool:
        """ONNX 模型是否已加载。"""
        return self._embedder is not None

    @property
    def has_sklearn(self) -> bool:
        """sklearn 是否可用。"""
        return self._clf is not None

    @property
    def cache_size(self) -> int:
        """当前缓存条目数。"""
        return len(self._cache)

    @property
    def total_trained(self) -> int:
        """已训练的样本总数。"""
        return self._total_trained

    def get_status(self) -> dict:
        """获取预测器完整状态。"""
        return {
            "is_ready": self._is_initialized,
            "queue_size": self._train_queue.qsize(),
            "has_onnx": self._embedder is not None,
            "has_sklearn": self._clf is not None,
            "onnx_path": self._onnx_path,
            "cache_size": len(self._cache),
            "cache_ttl": self._cache_ttl,
            "total_trained": self._total_trained,
            "embedding_dim": self._fallback_dim,
        }

    def reset(self) -> None:
        """重置预测引擎：清空模型权重、缓存和训练计数。"""
        with self._lock:
            self._is_initialized = False
            self._total_trained = 0
            self._cache.clear()
            # 重新创建分类器和回归器
            try:
                from sklearn.linear_model import SGDClassifier, SGDRegressor
                self._clf = SGDClassifier(loss="hinge", random_state=42)
                self._reg = SGDRegressor(random_state=42)
            except ImportError:
                self._clf = None
                self._reg = None
        # 清空训练队列
        while not self._train_queue.empty():
            try:
                self._train_queue.get_nowait()
            except Exception:
                break

    def shutdown(self) -> None:
        self._stop_event.set()


# 全局单例
def _create_global_instance() -> OnlinePredictor:
    """创建全局预测器单例。"""
    from .config import config

    onnx_path = config.get("onnx_model_path")
    if onnx_path:
        full_path = Path(__file__).resolve().parent.parent / onnx_path
        if not full_path.exists():
            onnx_path = None
    return OnlinePredictor(onnx_path=onnx_path)


predictor = _create_global_instance()
