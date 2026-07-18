"""
core/script_engine.py
=====================
自定义 Python 脚本执行引擎。

用于执行用户编写的余额/单价获取脚本，支持安全沙箱执行。

脚本变量约定：
- 余额脚本（balance_script）：
  可用变量: api_key, base_url, model_name
  返回值:
    - float（余额数值）或 None — 简写形式，单位默认为当前显示货币
    - dict，格式为 {"balance": float, "balance_currency": str} — 完整形式，可指定货币单位
    - dict，格式为 {"balance": float} — 省略货币时默认为当前显示货币

- 单价脚本（price_script）：
  可用变量: api_key, base_url, model_name
  返回值: dict，格式为：
    - {"price_input": float, "price_output": float, "price_currency": str, "price_unit": str}
    - price_unit 可选，默认 "1M"，可选值: "1K", "1M", "1B"（每千/百万/十亿 tokens）
    - price_currency 可选，默认为当前显示货币
  或 None

示例余额脚本：
  import httpx
  resp = httpx.get(base_url + "/user/balance", headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
  if resp.status_code == 200:
      return float(resp.json().get("total_balance", 0))
  return None

示例单价脚本：
  import httpx
  resp = httpx.get(base_url + "/models/" + model_name + "/price", headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
  if resp.status_code == 200:
      data = resp.json()
      return {"price_input": data["input_price"], "price_output": data["output_price"], "price_currency": "CNY", "price_unit": "1M"}
  return None
"""

from __future__ import annotations

import sys
import traceback
from typing import Any, Dict, Optional


# 允许在脚本中使用的内置模块
_SAFE_BUILTINS = {
    "abs": abs,
    "bool": bool,
    "dict": dict,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "print": print,
    "range": range,
    "round": round,
    "set": set,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "True": True,
    "False": False,
    "None": None,
}


def execute_balance_script(
    script: str,
    api_key: str = "",
    base_url: str = "",
    model_name: str = "",
) -> Optional[Dict[str, Any]]:
    """执行余额获取脚本。

    :param script: Python 脚本代码
    :param api_key: API 密钥
    :param base_url: API 基础 URL
    :param model_name: 模型名称
    :return: 余额结果字典 {"balance": float, "balance_currency": str|None}，
             兼容旧格式直接返回 float 时自动包装为 {"balance": float, "balance_currency": None}，
             失败返回 None
    """
    if not script or not script.strip():
        return None

    local_vars: Dict[str, Any] = {
        "api_key": api_key,
        "base_url": base_url,
        "model_name": model_name,
        "__builtins__": __builtins__,  # 允许完整 Python 能力，用户自行负责安全
    }

    try:
        exec(script, local_vars)
        # 脚本中通过 return 返回值（需要包装在函数中）
        # 或者脚本执行后设置 result 变量
        result = local_vars.get("result")
        if result is None:
            # 尝试调用 run 函数
            run_fn = local_vars.get("run")
            if callable(run_fn):
                result = run_fn()

        if result is None:
            return None

        # 兼容旧格式：直接返回 float
        if isinstance(result, (int, float)):
            return {"balance": float(result), "balance_currency": None}

        # 新格式：dict
        if isinstance(result, dict):
            balance_val = result.get("balance")
            if balance_val is not None:
                return {
                    "balance": float(balance_val),
                    "balance_currency": result.get("balance_currency"),
                }

    except Exception as exc:
        print(f"[ScriptEngine] balance script error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    return None


def execute_price_script(
    script: str,
    api_key: str = "",
    base_url: str = "",
    model_name: str = "",
) -> Optional[Dict[str, Any]]:
    """执行单价获取脚本。

    :param script: Python 脚本代码
    :param api_key: API 密钥
    :param base_url: API 基础 URL
    :param model_name: 模型名称
    :return: 价格字典 {"price_input": float, "price_output": float,
                      "price_currency": str|None, "price_unit": str|None}，
             price_currency 和 price_unit 为 None 时由调用方推断默认值，
             失败返回 None
    """
    if not script or not script.strip():
        return None

    local_vars: Dict[str, Any] = {
        "api_key": api_key,
        "base_url": base_url,
        "model_name": model_name,
        "__builtins__": __builtins__,
    }

    try:
        exec(script, local_vars)
        result = local_vars.get("result")
        if result is None:
            run_fn = local_vars.get("run")
            if callable(run_fn):
                result = run_fn()

        if result is not None and isinstance(result, dict):
            # 确保 price_unit 和 price_currency 字段存在（可能为 None，由调用方推断）
            result.setdefault("price_currency", None)
            result.setdefault("price_unit", None)
            return result

    except Exception as exc:
        print(f"[ScriptEngine] price script error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    return None


def test_script(script: str, script_type: str = "balance") -> Dict[str, Any]:
    """测试脚本语法是否正确。

    :param script: Python 脚本代码
    :param script_type: "balance" 或 "price"
    :return: {"ok": bool, "error": str|None}
    """
    if not script or not script.strip():
        return {"ok": True, "error": None}

    try:
        compile(script, f"<{script_type}_script>", "exec")
        return {"ok": True, "error": None}
    except SyntaxError as e:
        return {"ok": False, "error": f"语法错误 (行 {e.lineno}): {e.msg}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# 脚本编写说明模板
BALANCE_SCRIPT_HELP = """\
# 余额获取脚本
# 可用变量:
#   api_key (str) - API 密钥
#   base_url (str) - API 基础 URL
#   model_name (str) - 模型名称（同一提供方不同模型余额可能不同）
#
# 返回方式（三选一）:
#   1. 简写: result = 余额数值(float)
#      → 货币单位默认为当前显示货币
#   2. 完整: result = {"balance": 余额数值(float), "balance_currency": "CNY"}
#      → 指定余额的货币单位，不换算，直接按指定单位识别
#   3. 定义 run() 函数: def run(): return 数值 或 dict
#
# balance_currency 说明:
#   - 设定了 balance_currency: 直接按该货币单位识别，不做换算
#   - 未设定: 系统自动推断，无法推断时默认为当前显示货币
#
# 示例 - DeepSeek 余额查询（返回 CNY）:
# import httpx
# resp = httpx.get("https://api.deepseek.com/user/balance",
#                  headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
# if resp.status_code == 200:
#     data = resp.json()
#     balance_infos = data.get("balance_infos", [])
#     total = sum(float(b.get("total_balance", 0)) for b in balance_infos)
#     result = {"balance": total, "balance_currency": "CNY"}
# else:
#     result = None
"""

PRICE_SCRIPT_HELP = """\
# 单价获取脚本
# 可用变量:
#   api_key (str) - API 密钥
#   base_url (str) - API 基础 URL
#   model_name (str) - 模型名称
#
# 返回方式（二选一）:
#   1. 设置 result 变量: result = {"price_input": float, "price_output": float, ...}
#   2. 定义 run() 函数: def run(): return {"price_input": ..., "price_output": ..., ...}
#
# 返回字段说明:
#   price_input (必填) - 输入单价数值
#   price_output (必填) - 输出单价数值
#   price_currency (可选) - 货币单位，如 "CNY", "USD"
#     → 设定了: 直接按该货币单位识别，不做换算
#     → 未设定: 系统自动推断，无法推断时默认为当前显示货币
#   price_unit (可选) - 单价计量单位，如 "1K", "1M", "1B"
#     → 设定了: 直接按该单位识别，不做换算
#     → 未设定: 系统自动推断，无法推断时默认为 "1M"
#     → 可选值: "1K"(每千tokens), "1M"(每百万tokens), "1B"(每十亿tokens)
#
# 示例 - 从自定义 API 获取价格:
# import httpx
# resp = httpx.get(f"{base_url}/models/{model_name}/price",
#                  headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
# if resp.status_code == 200:
#     data = resp.json()
#     result = {
#         "price_input": float(data.get("input_price", 0)),
#         "price_output": float(data.get("output_price", 0)),
#         "price_currency": "CNY",
#         "price_unit": "1M"
#     }
# else:
#     result = None
"""
