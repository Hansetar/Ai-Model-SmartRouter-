"""ML/RL Router - machine learning and reinforcement learning routing (Path A).

Dual sub-paths:
1. Supervised learning: SGD classifier/regressor for difficulty prediction + model recommendation
2. Reinforcement learning: Online policy learning from feedback signals

Both sub-paths run in parallel, with RL gradually taking over as it accumulates experience.

Persistence:
- SGD models saved via joblib to data/ml/
- RL Q-table saved as JSON to data/ml/
- Auto-loaded on startup, auto-saved on update
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..config import get_settings, ModelConfig

logger = logging.getLogger(__name__)

try:
    from sklearn.linear_model import SGDClassifier, SGDRegressor
    import joblib
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

try:
    import onnxruntime as ort
    _HAS_ONNX = True
except ImportError:
    _HAS_ONNX = False


# Difficulty classes 1-100
_DIFF_CLASSES = np.array(list(range(1, 101)))

# Default persistence directory
_DEFAULT_ML_DIR = Path("data/ml")


class MLRouter:
    """ML/RL dual-path router with persistence and auto-tuning.

    Architecture:
    - ONNX embedder (all-MiniLM-L6-v2) for 384-dim feature extraction
    - SGD classifier/regressor for supervised difficulty prediction
    - RL policy (Q-table) for online learning from feedback
    - Hash-based fallback when ONNX/sklearn unavailable
    - Model persistence: auto-save/load from data/ml/
    - Exploration rate decay: decreases as training samples accumulate
    - Batch retraining: periodic full retrain from TrainingSample table
    """

    def __init__(self, onnx_path: Optional[str] = None) -> None:
        self._onnx_path = onnx_path
        self._embedder = None
        self._is_initialized = False
        self._lock = threading.Lock()

        # Persistence directory
        self._ml_dir = _DEFAULT_ML_DIR
        self._ml_dir.mkdir(parents=True, exist_ok=True)

        # Supervised learning models
        if _HAS_SKLEARN:
            self._clf = SGDClassifier(loss="log_loss", max_iter=1, tol=1e-3)
            self._reg = SGDRegressor(max_iter=1, tol=1e-3)
        else:
            self._clf = None
            self._reg = None

        # RL policy
        self._rl_policy: Dict[str, Dict[str, float]] = {}  # task_type -> {model_name: q_value}
        self._rl_exploration_rate = 0.1
        self._rl_discount = 0.95
        self._rl_learning_rate = 0.01
        self._rl_total_updates = 0

        # Training queue
        self._train_queue: List[Tuple] = []
        self._total_trained = 0

        # Prediction cache
        self._cache: Dict[str, Tuple[float, Tuple[int, int]]] = {}
        self._cache_ttl = 300

        # Auto-tuning state
        self._last_retrain_time = 0.0
        self._retrain_count = 0
        self._auto_tune_enabled = False

        # Load persisted models
        self._load_persisted_models()

        # Load ONNX
        self._load_embedder()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _load_persisted_models(self) -> None:
        """Load SGD models and RL policy from disk."""
        # Load SGD models
        if _HAS_SKLEARN:
            clf_path = self._ml_dir / "sgd_classifier.joblib"
            reg_path = self._ml_dir / "sgd_regressor.joblib"
            if clf_path.exists() and reg_path.exists():
                try:
                    self._clf = joblib.load(clf_path)
                    self._reg = joblib.load(reg_path)
                    self._is_initialized = True
                    logger.info("Loaded persisted SGD models from %s", self._ml_dir)
                except Exception as e:
                    logger.warning("Failed to load SGD models: %s", e)
                    # Recreate if load fails
                    self._clf = SGDClassifier(loss="log_loss", max_iter=1, tol=1e-3)
                    self._reg = SGDRegressor(max_iter=1, tol=1e-3)

        # Load RL policy
        rl_path = self._ml_dir / "rl_policy.json"
        if rl_path.exists():
            try:
                with open(rl_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._rl_policy = data.get("policy", {})
                self._rl_exploration_rate = data.get("exploration_rate", 0.1)
                self._rl_total_updates = data.get("total_updates", 0)
                self._total_trained = data.get("total_trained", 0)
                if "auto_tune_enabled" in data:
                    self._auto_tune_enabled = data["auto_tune_enabled"]
                logger.info("Loaded RL policy: %d task types, %d Q-values",
                           len(self._rl_policy),
                           sum(len(v) for v in self._rl_policy.values()))
            except Exception as e:
                logger.warning("Failed to load RL policy: %s", e)

    def save_models(self) -> None:
        """Save SGD models and RL policy to disk."""
        self._ml_dir.mkdir(parents=True, exist_ok=True)

        # Save SGD models
        if _HAS_SKLEARN and self._is_initialized:
            try:
                joblib.dump(self._clf, self._ml_dir / "sgd_classifier.joblib")
                joblib.dump(self._reg, self._ml_dir / "sgd_regressor.joblib")
                logger.info("Saved SGD models to %s", self._ml_dir)
            except Exception as e:
                logger.warning("Failed to save SGD models: %s", e)

        # Save RL policy
        try:
            data = {
                "policy": self._rl_policy,
                "exploration_rate": self._rl_exploration_rate,
                "total_updates": self._rl_total_updates,
                "total_trained": self._total_trained,
                "auto_tune_enabled": self._auto_tune_enabled,
                "saved_at": time.time(),
            }
            with open(self._ml_dir / "rl_policy.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("Saved RL policy: %d task types", len(self._rl_policy))
        except Exception as e:
            logger.warning("Failed to save RL policy: %s", e)

    # ------------------------------------------------------------------ #
    # ONNX embedder
    # ------------------------------------------------------------------ #

    def _load_embedder(self) -> None:
        """Load ONNX embedding model with auto-download and backup support.

        Strategy:
        1. If onnxruntime not available → hash embedding fallback
        2. Try minilm.onnx (split format: minilm.onnx + minilm.onnx.data)
        3. Try model.onnx (single file format)
        4. If ONNX file exists but corrupted → backup, then download
        5. If ONNX file missing → download in background, use hash fallback until ready
        """
        if not _HAS_ONNX:
            logger.info("onnxruntime not available, using hash features")
            return

        # Determine ONNX path - try multiple formats
        if not self._onnx_path:
            # Priority 1: minilm.onnx (split format)
            minilm_path = Path("models/minilm.onnx")
            if minilm_path.exists():
                self._onnx_path = str(minilm_path)
                logger.info("Using split-format ONNX model: %s", minilm_path)

            # Priority 2: model.onnx (single file format)
            if not self._onnx_path:
                model_path = Path("models/model.onnx")
                if model_path.exists():
                    self._onnx_path = str(model_path)
                    logger.info("Using single-file ONNX model: %s", model_path)

            # Priority 3: try to download
            if not self._onnx_path:
                self._onnx_path = str(self._ensure_onnx_model())
                if not self._onnx_path:
                    logger.info("ONNX model not available, using hash features")
                    return

        onnx_file = Path(self._onnx_path)

        # Try to load
        try:
            if not onnx_file.exists():
                # Download
                result = self._download_onnx_model(onnx_file)
                if not result:
                    logger.info("ONNX model download failed, using hash features")
                    return

            self._embedder = ort.InferenceSession(
                self._onnx_path, providers=["CPUExecutionProvider"]
            )
            # Verify it works with a dummy input
            dummy = np.zeros((1, 128), dtype=np.int64)
            input_name = self._embedder.get_inputs()[0].name
            self._embedder.run(None, {input_name: dummy})
            logger.info("ONNX model loaded: %s", self._onnx_path)
        except Exception as e:
            logger.warning("ONNX load failed: %s, backing up and attempting re-download", e)
            self._embedder = None
            # Backup corrupted file
            if onnx_file.exists():
                self._backup_file(onnx_file)
            # Try to re-download
            result = self._download_onnx_model(onnx_file)
            if result:
                try:
                    self._embedder = ort.InferenceSession(
                        self._onnx_path, providers=["CPUExecutionProvider"]
                    )
                    logger.info("ONNX model re-downloaded and loaded successfully")
                except Exception as e2:
                    logger.warning("ONNX re-download load failed: %s", e2)
                    self._embedder = None

    def _ensure_onnx_model(self) -> Optional[str]:
        """Ensure ONNX model exists, download if needed. Returns path or None."""
        models_dir = Path("models")
        models_dir.mkdir(parents=True, exist_ok=True)
        onnx_path = models_dir / "model.onnx"

        if onnx_path.exists():
            return str(onnx_path)

        # Try to download
        if self._download_onnx_model(onnx_path):
            return str(onnx_path)
        return None

    def _download_onnx_model(self, target_path: Path) -> bool:
        """Download all-MiniLM-L6-v2 ONNX model.

        Downloads from HuggingFace (sentence-transformers/all-MiniLM-L6-v2).
        Falls back to a simpler approach: download the model and convert to ONNX.
        """
        if target_path.exists():
            return True

        try:
            import httpx
            logger.info("Downloading ONNX model (all-MiniLM-L6-v2)...")

            # Try downloading pre-converted ONNX from HuggingFace
            # The model is available as ONNX in the ONNX model repo
            urls = [
                "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/model.onnx",
                "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx",
            ]

            for url in urls:
                try:
                    resp = httpx.get(url, timeout=120.0, follow_redirects=True)
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(target_path, "wb") as f:
                            f.write(resp.content)
                        logger.info("ONNX model downloaded: %s (%d bytes)",
                                   target_path, len(resp.content))
                        return True
                except Exception:
                    continue

            logger.warning("Failed to download ONNX model from all URLs")
            return False

        except Exception as e:
            logger.warning("ONNX model download error: %s", e)
            return False

    @staticmethod
    def _backup_file(filepath: Path) -> None:
        """Backup a corrupted file with timestamp."""
        if not filepath.exists():
            return
        timestamp = int(time.time())
        backup_path = filepath.parent / f"{filepath.name}.bak.{timestamp}"
        try:
            import shutil
            shutil.move(str(filepath), str(backup_path))
            logger.info("Backed up corrupted file: %s -> %s", filepath, backup_path)
        except Exception as e:
            logger.warning("Failed to backup file: %s", e)

    # ------------------------------------------------------------------ #
    # Prediction
    # ------------------------------------------------------------------ #

    def predict(self, prompt: str) -> Tuple[int, int]:
        """Predict difficulty (1-100) and estimated output tokens."""
        if not isinstance(prompt, str):
            prompt = str(prompt) if prompt is not None else ""

        # Cache check
        cache_key = hashlib.md5(prompt.encode("utf-8")).hexdigest()
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0] < self._cache_ttl):
            return cached[1]

        # Cold start fallback
        if not self._is_initialized or self._clf is None or self._reg is None:
            return 50, 500

        try:
            embedding = self._get_embedding(prompt)
            with self._lock:
                difficulty = int(self._clf.predict(embedding)[0])
                est_tokens = max(1, int(self._reg.predict(embedding)[0]))
        except Exception as e:
            logger.warning("Prediction failed: %s", e)
            return 50, 500

        # Update cache
        self._cache[cache_key] = (time.time(), (difficulty, est_tokens))
        if len(self._cache) > 10000:
            now = time.time()
            self._cache = {k: v for k, v in self._cache.items() if now - v[0] < self._cache_ttl}

        return difficulty, est_tokens

    # ------------------------------------------------------------------ #
    # Model selection
    # ------------------------------------------------------------------ #

    def select_model(
        self,
        prompt: str,
        difficulty: int,
        est_tokens: int,
        task_type: Optional[str] = None,
        exclude: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
        is_global_key: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Select model using ML/RL approach."""
        settings = get_settings()
        exclude = exclude or []

        available = [
            m for m in settings.models
            if m.enabled and m.is_active_now and m.name not in exclude
            and m.effective_capability >= difficulty
        ]

        if not available:
            return None

        # Filter by tenant allowed/blocked models
        if tenant_id and not is_global_key:
            tenant = settings.get_tenant(tenant_id)
            if tenant:
                mode = getattr(tenant, 'model_filter_mode', 'whitelist')
                if mode == 'blacklist':
                    if tenant.blocked_models:
                        available = [m for m in available if m.name not in tenant.blocked_models]
                    if tenant.allowed_models:
                        available = [m for m in available if m.name in tenant.allowed_models]
                else:
                    # Whitelist: empty = deny all
                    if not tenant.allowed_models:
                        available = []
                    else:
                        available = [m for m in available if m.name in tenant.allowed_models]

                # Check tenant balance: if in arrears, no models available
                try:
                    from ..storage import get_session as _get_session, TenantBalance as _TenantBalance
                    with _get_session() as _session:
                        _balance_record = _session.get(_TenantBalance, tenant.tenant_id)
                        if _balance_record and not getattr(_balance_record, 'unlimited', False) and _balance_record.balance <= 0:
                            available = []
                except Exception:
                    pass

        # Try RL policy first
        if task_type and task_type in self._rl_policy:
            model = self._rl_select(task_type, available, settings)
            if model:
                return settings.get_enriched_model(model.name)

        # Supervised recommendation
        pred_diff, pred_tokens = self.predict(prompt)
        best = None
        best_margin = float("inf")
        for m in available:
            margin = m.effective_capability - pred_diff
            if margin >= 0 and margin < best_margin:
                if m.is_free:
                    return settings.get_enriched_model(m.name)
                best_margin = margin
                best = m

        if best:
            return settings.get_enriched_model(best.name)

        return settings.get_enriched_model(available[0].name) if available else None

    def rank_models(
        self,
        prompt: str,
        difficulty: int,
        est_tokens: int,
        task_type: Optional[str] = None,
        exclude: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
        is_global_key: bool = False,
    ) -> List[Dict[str, Any]]:
        """Rank all available models by ML/RL confidence (for fusion layer)."""
        settings = get_settings()
        exclude = exclude or []

        available = [
            m for m in settings.models
            if m.enabled and m.is_active_now and m.name not in exclude
            and m.effective_capability >= difficulty
        ]

        if not available:
            return []

        # Filter by tenant allowed/blocked models
        if tenant_id and not is_global_key:
            tenant = settings.get_tenant(tenant_id)
            if tenant:
                mode = getattr(tenant, 'model_filter_mode', 'whitelist')
                if mode == 'blacklist':
                    if tenant.blocked_models:
                        available = [m for m in available if m.name not in tenant.blocked_models]
                    if tenant.allowed_models:
                        available = [m for m in available if m.name in tenant.allowed_models]
                else:
                    if not tenant.allowed_models:
                        available = []
                    else:
                        available = [m for m in available if m.name in tenant.allowed_models]

                # Check tenant balance
                try:
                    from ..storage import get_session as _get_session, TenantBalance as _TenantBalance
                    with _get_session() as _session:
                        _balance_record = _session.get(_TenantBalance, tenant.tenant_id)
                        if _balance_record and not getattr(_balance_record, 'unlimited', False) and _balance_record.balance <= 0:
                            available = []
                except Exception:
                    pass

        results = []
        for m in available:
            q_value = self._get_q_value(task_type, m.name)
            score = -q_value
            results.append({
                "model": settings.get_enriched_model(m.name),
                "combined_score": score,
                "q_value": q_value,
            })

        results.sort(key=lambda x: x["combined_score"])
        return results

    # ------------------------------------------------------------------ #
    # RL policy
    # ------------------------------------------------------------------ #

    def _rl_select(
        self,
        task_type: str,
        available: List[ModelConfig],
        settings,
    ) -> Optional[ModelConfig]:
        """Epsilon-greedy RL model selection with exploration rate decay."""
        import random

        policy = self._rl_policy.get(task_type, {})

        # Exploration: random selection
        if random.random() < self._rl_exploration_rate:
            free = [m for m in available if m.is_free]
            if free:
                return random.choice(free)
            return random.choice(available) if available else None

        # Exploitation: select model with highest Q-value
        best_model = None
        best_q = float("-inf")
        for m in available:
            q = policy.get(m.name, 0.0)
            if q > best_q:
                best_q = q
                best_model = m

        return best_model

    def _get_q_value(self, task_type: Optional[str], model_name: str) -> float:
        """Get RL Q-value for a task_type + model combination."""
        if not task_type or task_type not in self._rl_policy:
            return 0.0
        return self._rl_policy[task_type].get(model_name, 0.0)

    def update_rl_policy(
        self,
        task_type: Optional[str],
        model_name: str,
        reward: float,
    ) -> None:
        """Update RL policy with reward signal."""
        if not task_type:
            return

        with self._lock:
            if task_type not in self._rl_policy:
                self._rl_policy[task_type] = {}

            current_q = self._rl_policy[task_type].get(model_name, 0.0)
            new_q = current_q + self._rl_learning_rate * (reward - current_q)
            self._rl_policy[task_type][model_name] = new_q
            self._rl_total_updates += 1

            # Exploration rate decay: decrease as we accumulate experience
            self._decay_exploration_rate()

            # Periodic save (every 50 updates)
            if self._rl_total_updates % 50 == 0:
                self.save_models()

    def _decay_exploration_rate(self) -> None:
        """Decay exploration rate as training progresses.

        Formula: rate = max(min_rate, initial_rate * decay^updates)
        """
        settings = get_settings()
        initial_rate = settings.rl_config.exploration_rate
        min_rate = 0.01
        decay = 0.9999  # Slow decay
        self._rl_exploration_rate = max(
            min_rate,
            initial_rate * (decay ** self._rl_total_updates)
        )

    # ------------------------------------------------------------------ #
    # Training
    # ------------------------------------------------------------------ #

    def add_sample(
        self,
        prompt: str,
        actual_difficulty: int,
        actual_tokens: int,
        task_type: Optional[str] = None,
        model_name: Optional[str] = None,
        success: bool = True,
    ) -> None:
        """Add training sample for supervised learning + update RL policy."""
        self._train_queue.append((prompt, actual_difficulty, actual_tokens, task_type, model_name, success))
        self._process_training_queue()

        # Update RL policy
        if task_type and model_name:
            reward = 1.0 if success else -1.0
            self.update_rl_policy(task_type, model_name, reward)

    def _process_training_queue(self) -> None:
        """Process pending training samples."""
        if not self._train_queue:
            return

        while self._train_queue:
            item = self._train_queue.pop(0)
            prompt, diff, tokens, task_type, model_name, success = item

            if self._clf is None or self._reg is None:
                continue

            try:
                embedding = self._get_embedding(prompt)
                y_clf = np.array([diff])
                y_reg = np.array([tokens], dtype=np.float64)

                with self._lock:
                    if not self._is_initialized:
                        self._clf.partial_fit(embedding, y_clf, classes=_DIFF_CLASSES)
                        self._reg.partial_fit(embedding, y_reg)
                        self._is_initialized = True
                    else:
                        self._clf.partial_fit(embedding, y_clf, classes=_DIFF_CLASSES)
                        self._reg.partial_fit(embedding, y_reg)
                    self._total_trained += 1

                    # Periodic save (every 100 samples)
                    if self._total_trained % 100 == 0:
                        self.save_models()

            except Exception as e:
                logger.warning("Training failed: %s", e)

    def batch_retrain(self, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Batch retrain from a list of samples.

        Each sample dict should have: prompt, difficulty, est_tokens, task_type, model_name, success
        Returns training report.
        """
        if not _HAS_SKLEARN or self._clf is None or self._reg is None:
            return {"error": "sklearn not available", "trained": 0}

        start_time = time.time()
        trained_count = 0
        errors = 0

        # Collect all embeddings and labels first for batch training
        X_list = []
        y_clf_list = []
        y_reg_list = []

        for s in samples:
            try:
                prompt = s.get("prompt", "")
                difficulty = s.get("difficulty", 50)
                est_tokens = s.get("est_tokens", 500)

                embedding = self._get_embedding(prompt)
                X_list.append(embedding[0])
                y_clf_list.append(difficulty)
                y_reg_list.append(float(est_tokens))
            except Exception:
                errors += 1

        if not X_list:
            return {"error": "no valid samples", "trained": 0}

        X = np.array(X_list)
        y_clf = np.array(y_clf_list)
        y_reg = np.array(y_reg_list)

        try:
            with self._lock:
                if not self._is_initialized:
                    self._clf.partial_fit(X, y_clf, classes=_DIFF_CLASSES)
                    self._reg.partial_fit(X, y_reg)
                    self._is_initialized = True
                else:
                    self._clf.partial_fit(X, y_clf, classes=_DIFF_CLASSES)
                    self._reg.partial_fit(X, y_reg)

                trained_count = len(X_list)
                self._total_trained += trained_count
                self._retrain_count += 1
                self._last_retrain_time = time.time()

            # Save after batch retrain
            self.save_models()

        except Exception as e:
            logger.error("Batch retrain failed: %s", e)
            return {"error": str(e), "trained": 0}

        elapsed = time.time() - start_time
        return {
            "trained": trained_count,
            "errors": errors,
            "elapsed_seconds": round(elapsed, 2),
            "total_trained": self._total_trained,
            "retrain_count": self._retrain_count,
        }

    def check_auto_retrain(self) -> Optional[Dict[str, Any]]:
        """Check if auto-retrain should be triggered and execute it.

        Returns None if no retrain needed, or the retrain report.
        """
        if not self._auto_tune_enabled:
            return None

        settings = get_settings()
        rl_config = settings.rl_config

        # Check time-based trigger
        interval = rl_config.batch_retrain_interval_hours * 3600
        if time.time() - self._last_retrain_time < interval:
            return None

        # Check sample threshold
        min_samples = rl_config.min_samples_for_retrain
        try:
            from ..storage import get_session, TrainingSample
            with get_session() as session:
                sample_count = session.query(TrainingSample).count()

            if sample_count < min_samples:
                return None

            # Load samples from DB
            samples = session.query(TrainingSample).limit(10000).all()
            sample_dicts = [
                {
                    "prompt": s.prompt,
                    "difficulty": s.difficulty,
                    "est_tokens": s.est_tokens,
                    "task_type": s.task_type,
                    "model_name": s.model_name,
                }
                for s in samples
            ]
        except Exception as e:
            logger.warning("Auto-retrain sample loading failed: %s", e)
            return None

        logger.info("Auto-retrain triggered: %d samples", len(sample_dicts))
        return self.batch_retrain(sample_dicts)

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #

    def _get_embedding(self, prompt: str) -> np.ndarray:
        """Extract 384-dim feature vector."""
        if self._embedder is not None:
            try:
                tokens = self._simple_tokenize(prompt)
                inputs = {self._embedder.get_inputs()[0].name: tokens}
                output = self._embedder.run(None, inputs)[0]
                return np.mean(output, axis=0, keepdims=True).astype(np.float32)
            except Exception as e:
                logger.warning("ONNX inference failed: %s", e)

        return self._hash_embedding(prompt)

    @staticmethod
    def _simple_tokenize(text: str) -> np.ndarray:
        """Simple character-level tokenization for ONNX model input."""
        max_len = 128
        chars = list(text)[:max_len]
        ids = [ord(c) % 30000 for c in chars]
        while len(ids) < max_len:
            ids.append(0)
        return np.array([ids], dtype=np.int64)

    @staticmethod
    def _hash_embedding(prompt: str, dim: int = 384) -> np.ndarray:
        """Hash-based fallback feature extraction (TF-IDF-like n-gram features)."""
        if not isinstance(prompt, str):
            prompt = str(prompt) if prompt is not None else ""
        vec = np.zeros((1, dim), dtype=np.float32)
        text = prompt.lower()
        for i in range(len(text)):
            for n in (1, 2, 3):
                gram = text[i:i + n]
                if not gram:
                    continue
                h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
                vec[0, h % dim] += 1.0
        norm = np.linalg.norm(vec) + 1e-9
        return vec / norm

    # ------------------------------------------------------------------ #
    # Status and management
    # ------------------------------------------------------------------ #

    @property
    def is_ready(self) -> bool:
        return self._is_initialized

    @property
    def has_rl_policy(self) -> bool:
        return len(self._rl_policy) > 0

    def get_status(self) -> Dict[str, Any]:
        """Get ML router status."""
        return {
            "is_ready": self._is_initialized,
            "has_onnx": self._embedder is not None,
            "has_sklearn": self._clf is not None,
            "total_trained": self._total_trained,
            "rl_policy_size": sum(len(v) for v in self._rl_policy.values()),
            "rl_task_types": list(self._rl_policy.keys()),
            "rl_exploration_rate": round(self._rl_exploration_rate, 4),
            "rl_total_updates": self._rl_total_updates,
            "retrain_count": self._retrain_count,
            "last_retrain_time": self._last_retrain_time,
            "auto_tune_enabled": self._auto_tune_enabled,
            "cache_size": len(self._cache),
        }

    def get_rl_policy_detail(self) -> Dict[str, Any]:
        """Get detailed RL policy for visualization."""
        return {
            "policy": self._rl_policy,
            "exploration_rate": self._rl_exploration_rate,
            "learning_rate": self._rl_learning_rate,
            "discount_factor": self._rl_discount,
            "total_updates": self._rl_total_updates,
        }

    def set_rl_params(
        self,
        learning_rate: Optional[float] = None,
        exploration_rate: Optional[float] = None,
        discount_factor: Optional[float] = None,
    ) -> None:
        """Update RL parameters in real-time."""
        if learning_rate is not None:
            self._rl_learning_rate = learning_rate
        if exploration_rate is not None:
            self._rl_exploration_rate = exploration_rate
        if discount_factor is not None:
            self._rl_discount = discount_factor
        self.save_models()

    def set_auto_tune(self, enabled: bool) -> None:
        """Enable or disable auto-tuning."""
        self._auto_tune_enabled = enabled
        self.save_models()

    def reset_models(self) -> None:
        """Reset all ML models and RL policy."""
        with self._lock:
            if _HAS_SKLEARN:
                self._clf = SGDClassifier(loss="log_loss", max_iter=1, tol=1e-3)
                self._reg = SGDRegressor(max_iter=1, tol=1e-3)
            self._is_initialized = False
            self._rl_policy = {}
            self._rl_exploration_rate = 0.1
            self._rl_total_updates = 0
            self._total_trained = 0
            self._retrain_count = 0
            self._train_queue = []
            self._cache = {}

        # Remove persisted files
        for f in self._ml_dir.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass
        logger.info("ML models and RL policy reset")
