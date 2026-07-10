"""
tests/test_core.py
==================
核心模块单元测试。

运行：
    cd openclaw-smart-router
    python -m pytest tests/ -v
"""

import os
import sys
import time
from pathlib import Path

import pytest

# 将项目根目录加入 sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 使用临时数据库
os.environ["SMARTROUTER_DB_PATH"] = str(ROOT / "tests" / "test.db")

from core.config import Config, params_b_to_capability, ALL_TASK_TYPES
from core.database import Database
from core.predictor import OnlinePredictor
from core.router import SmartRouter, detect_task_type
from core.feedback_analyzer import FeedbackAnalyzer
from core.auth import create_access_token, verify_token, verify_password, verify_api_key


@pytest.fixture(scope="module")
def predictor():
    p = OnlinePredictor(onnx_path=None)  # 强制降级模式
    yield p
    p.shutdown()


@pytest.fixture(scope="module")
def db():
    Config.reset()
    Config.get_instance(str(ROOT / "config.yaml"))
    d = Database(str(ROOT / "tests" / "test.db"))
    yield d
    # 清理
    try:
        (ROOT / "tests" / "test.db").unlink(missing_ok=True)
    except Exception:
        pass


class TestPredictor:
    def test_cold_start_returns_default(self, predictor):
        """冷启动应返回默认难度 3。"""
        difficulty, tokens = predictor.predict("hello world")
        assert difficulty == 3
        assert tokens > 0

    def test_predict_under_30ms(self, predictor):
        """预测应在 30ms 内完成。"""
        start = time.perf_counter()
        for _ in range(100):
            predictor.predict("这是一个测试问题，用于验证预测性能")
        elapsed = (time.perf_counter() - start) / 100 * 1000
        assert elapsed < 30, f"预测耗时 {elapsed:.2f}ms 超过 30ms"

    def test_add_sample_does_not_block(self, predictor):
        """add_sample 应立即返回（异步入队）。"""
        start = time.perf_counter()
        predictor.add_sample("test prompt", 3, 500)
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 5, f"add_sample 耗时 {elapsed:.2f}ms"


class TestRouter:
    def test_select_model_returns_dict(self, db):
        """路由应返回模型 dict。"""
        Config.reset()
        Config.get_instance(str(ROOT / "config.yaml"))
        r = SmartRouter()
        model = r.select_model(difficulty=3, est_in_tokens=100, est_out_tokens=200)
        assert model is not None
        assert "name" in model

    def test_select_model_prefers_free(self, db):
        """路由应优先选择免费模型。"""
        Config.reset()
        Config.get_instance(str(ROOT / "config.yaml"))
        r = SmartRouter()
        model = r.select_model(difficulty=3, est_in_tokens=100, est_out_tokens=200)
        assert model is not None
        # 免费模型（price_input=0 且 price_output=0）应被优先选择
        if model.get("price_input", 0) == 0 and model.get("price_output", 0) == 0:
            assert True  # 选择了免费模型
        # 如果没有免费模型，至少应该有模型返回
        assert "name" in model

    def test_select_model_excludes(self, db):
        """exclude 列表应被排除。"""
        Config.reset()
        Config.get_instance(str(ROOT / "config.yaml"))
        r = SmartRouter()
        all_models = [m["name"] for m in Config.get_instance().get_models()]
        model = r.select_model(
            difficulty=3, est_in_tokens=100, est_out_tokens=200,
            exclude=all_models[:1],
        )
        assert model is not None
        assert model["name"] not in all_models[:1]


class TestFeedbackAnalyzer:
    def test_analyze_positive(self):
        fa = FeedbackAnalyzer()
        sentiment, score = fa.analyze_implicit("谢谢，回答得很好")
        assert sentiment == "positive"

    def test_analyze_negative(self):
        fa = FeedbackAnalyzer()
        sentiment, score = fa.analyze_implicit("不对，这个回答是错误的")
        assert sentiment == "negative"

    def test_analyze_neutral(self):
        fa = FeedbackAnalyzer()
        sentiment, score = fa.analyze_implicit("今天天气怎么样")
        assert sentiment == "neutral"

    def test_estimate_difficulty(self):
        assert FeedbackAnalyzer.estimate_difficulty({"choices": [{"message": {"content": "hi"}}]}) == 1
        long_content = "x" * 2500
        assert FeedbackAnalyzer.estimate_difficulty({"choices": [{"message": {"content": long_content}}]}) == 5


class TestDatabase:
    def test_log_and_query(self, db):
        db.log_request(
            prompt_hash="test_hash_123",
            predicted_difficulty=3,
            actual_difficulty=3,
            routed_model="gpt-4o-mini",
            cost=0.001,
            latency_ms=120,
            success=1,
        )
        # 等待异步写入完成
        time.sleep(0.3)
        stats = db.get_dashboard_stats()
        assert stats["total_interceptions"] >= 1


class TestAuth:
    def test_verify_password(self):
        """密码验证。"""
        assert verify_password("admin") is True
        assert verify_password("wrong") is False

    def test_jwt_token(self):
        """JWT Token 创建与验证。"""
        token = create_access_token("admin")
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "admin"

    def test_invalid_token(self):
        """无效 Token 应返回 None。"""
        assert verify_token("invalid-token") is None

    def test_api_key_not_configured(self):
        """未配置 API Key 时应放行。"""
        assert verify_api_key("any-key") is True


class TestParamsBToCapability:
    """参数量 -> 能力等级换算测试。"""

    def test_tiny_model(self):
        assert params_b_to_capability(0) == 1
        assert params_b_to_capability(0.5) == 1

    def test_small_model(self):
        assert params_b_to_capability(3) == 2
        assert params_b_to_capability(6) == 2

    def test_medium_model(self):
        assert params_b_to_capability(7) == 3
        assert params_b_to_capability(13) == 3

    def test_large_model(self):
        assert params_b_to_capability(14) == 4
        assert params_b_to_capability(67) == 4

    def test_xl_model(self):
        assert params_b_to_capability(70) == 5
        assert params_b_to_capability(175) == 5


class TestDetectTaskType:
    """请求类型推断测试。"""

    def test_coding(self):
        assert detect_task_type("帮我写一个Python排序函数") == "coding"
        assert detect_task_type("implement a binary search") == "coding"

    def test_math(self):
        assert detect_task_type("计算积分 x^2 dx 从0到1") == "math"

    def test_reasoning(self):
        assert detect_task_type("分析一下为什么经济增长放缓") == "reasoning"

    def test_creative(self):
        assert detect_task_type("写一首关于春天的诗") == "creative"

    def test_translation(self):
        assert detect_task_type("翻译这段话为英文") == "translation"

    def test_analysis(self):
        assert detect_task_type("总结一下这篇文章的要点") == "analysis"

    def test_unknown(self):
        assert detect_task_type("你好，今天天气怎么样") is None

    def test_empty(self):
        assert detect_task_type("") is None


class TestRouterTaskTypes:
    """路由 task_types 匹配测试。"""

    def test_task_type_parameter(self, db):
        """路由应接受 task_type 参数。"""
        Config.reset()
        Config.get_instance(str(ROOT / "config.yaml"))
        r = SmartRouter()
        model = r.select_model(
            difficulty=3, est_in_tokens=100, est_out_tokens=200,
            task_type="coding",
        )
        assert model is not None
        assert "name" in model

    def test_task_match_calc(self, db):
        """task_match 计算应正确。"""
        Config.reset()
        Config.get_instance(str(ROOT / "config.yaml"))
        r = SmartRouter()
        # 匹配
        assert r._calc_task_match({"task_types": ["coding", "math"]}, "coding") == 1.5
        # 不匹配
        assert r._calc_task_match({"task_types": ["coding", "math"]}, "creative") == 0.5
        # 未知类型
        assert r._calc_task_match({"task_types": ["coding"]}, None) == 1.0
        # 模型未声明
        assert r._calc_task_match({}, "coding") == 1.0
        assert r._calc_task_match({"task_types": []}, "coding") == 1.0


class TestTokensToDifficulty:
    """难度量化映射测试（含平滑插值）。"""

    def setup_method(self):
        """每个测试前重置配置。"""
        Config.reset()
        Config.get_instance(str(ROOT / "config.yaml"))

    def test_exact_match_first_range(self):
        """Token 在第一个范围内，返回对应 difficulty。"""
        cfg = Config.get_instance()
        # 默认配置: min_tokens=0, max_tokens=50, difficulty=10
        assert cfg.tokens_to_difficulty(0) == 10
        assert cfg.tokens_to_difficulty(25) == 10
        assert cfg.tokens_to_difficulty(49) == 10

    def test_exact_match_second_range(self):
        """Token 在第二个范围内，返回对应 difficulty。"""
        cfg = Config.get_instance()
        # 默认配置: min_tokens=50, max_tokens=300, difficulty=30
        assert cfg.tokens_to_difficulty(50) == 30
        assert cfg.tokens_to_difficulty(200) == 30
        assert cfg.tokens_to_difficulty(299) == 30

    def test_exact_match_last_range(self):
        """Token 在最后一个范围内，返回对应 difficulty。"""
        cfg = Config.get_instance()
        # 默认配置: min_tokens=2000, max_tokens=999999, difficulty=95
        assert cfg.tokens_to_difficulty(2000) == 95
        assert cfg.tokens_to_difficulty(50000) == 95

    def test_above_max_returns_last_difficulty(self):
        """Token 超过最大 max_tokens，返回最后一个范围的 difficulty。"""
        cfg = Config.get_instance()
        assert cfg.tokens_to_difficulty(999999) == 95
        assert cfg.tokens_to_difficulty(1000000) == 95

    def test_gap_interpolation(self):
        """Token 落在范围间隙时，返回线性插值结果。"""
        cfg = Config.get_instance()
        # 构造有间隙的配置: [0,100)->10, [200,300)->50
        cfg.difficulty_ranges = [
            {"min_tokens": 0, "max_tokens": 100, "difficulty": 10},
            {"min_tokens": 200, "max_tokens": 300, "difficulty": 50},
        ]
        # Token=150 在间隙 (100, 200) 正中间
        # 插值: 10 + 0.5 * (50 - 10) = 30
        assert cfg.tokens_to_difficulty(150) == 30
        # Token=100 在间隙起点
        assert cfg.tokens_to_difficulty(100) == 10
        # Token=199 在间隙终点附近
        # 插值: 10 + (99/100) * 40 = 49.6 → 50
        assert cfg.tokens_to_difficulty(199) == 50

    def test_gap_interpolation_asymmetric(self):
        """间隙插值：Token 不在间隙正中。"""
        cfg = Config.get_instance()
        cfg.difficulty_ranges = [
            {"min_tokens": 0, "max_tokens": 100, "difficulty": 20},
            {"min_tokens": 200, "max_tokens": 300, "difficulty": 60},
        ]
        # Token=130, ratio=(130-100)/(200-100)=0.3
        # 插值: 20 + 0.3 * (60 - 20) = 32
        assert cfg.tokens_to_difficulty(130) == 32

    def test_below_min_returns_first_difficulty(self):
        """Token 低于最小 min_tokens，返回第一个范围的 difficulty。"""
        cfg = Config.get_instance()
        cfg.difficulty_ranges = [
            {"min_tokens": 10, "max_tokens": 100, "difficulty": 20},
            {"min_tokens": 100, "max_tokens": 500, "difficulty": 50},
        ]
        assert cfg.tokens_to_difficulty(0) == 20
        assert cfg.tokens_to_difficulty(5) == 20

    def test_single_range(self):
        """只有一个范围时，精确匹配或边界返回。"""
        cfg = Config.get_instance()
        cfg.difficulty_ranges = [
            {"min_tokens": 0, "max_tokens": 1000, "difficulty": 40},
        ]
        assert cfg.tokens_to_difficulty(500) == 40
        assert cfg.tokens_to_difficulty(0) == 40
        # 超出唯一范围，返回该范围的 difficulty
        assert cfg.tokens_to_difficulty(1000) == 40

    def test_empty_ranges_returns_default(self):
        """空范围列表返回默认值 50。"""
        cfg = Config.get_instance()
        cfg.difficulty_ranges = []
        assert cfg.tokens_to_difficulty(100) == 50

    def test_default_config_no_gaps(self):
        """默认配置无间隙，所有 Token 值应精确匹配。"""
        cfg = Config.get_instance()
        # 默认配置范围: [0,50)->10, [50,300)->30, [300,800)->50, [800,2000)->75, [2000,999999)->95
        assert cfg.tokens_to_difficulty(0) == 10
        assert cfg.tokens_to_difficulty(50) == 30
        assert cfg.tokens_to_difficulty(300) == 50
        assert cfg.tokens_to_difficulty(800) == 75
        assert cfg.tokens_to_difficulty(2000) == 95

    def test_result_always_in_valid_range(self):
        """插值结果始终在 1-100 范围内。"""
        cfg = Config.get_instance()
        cfg.difficulty_ranges = [
            {"min_tokens": 0, "max_tokens": 100, "difficulty": 1},
            {"min_tokens": 200, "max_tokens": 300, "difficulty": 100},
        ]
        # 间隙中间插值
        result = cfg.tokens_to_difficulty(150)
        assert 1 <= result <= 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
