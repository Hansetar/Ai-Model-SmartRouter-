"""
core/notifier.py
================
统一通知系统。

支持渠道：Webhook / 邮件 / 钉钉 / 企业微信 / 飞书 / Telegram / Slack
严重级别：info / warning / critical
"""

from __future__ import annotations

import json
import smtplib
import time
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import httpx

from .config import config


# ---------------------------------------------------------------------- #
# 通知渠道
# ---------------------------------------------------------------------- #

def _send_webhook(url: str, payload: Dict[str, Any], timeout: float = 10.0) -> bool:
    """发送 Webhook 通知。"""
    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
        return resp.status_code < 400
    except Exception:
        return False


def _send_dingtalk(webhook_url: str, title: str, content: str) -> bool:
    """发送钉钉机器人通知。"""
    try:
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": content},
        }
        resp = httpx.post(webhook_url, json=payload, timeout=10.0)
        return resp.status_code < 400
    except Exception:
        return False


def _send_wecom(webhook_url: str, content: str) -> bool:
    """发送企业微信机器人通知。"""
    try:
        payload = {"msgtype": "text", "text": {"content": content}}
        resp = httpx.post(webhook_url, json=payload, timeout=10.0)
        return resp.status_code < 400
    except Exception:
        return False


def _send_feishu(webhook_url: str, title: str, content: str) -> bool:
    """发送飞书机器人通知。"""
    try:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "markdown", "content": content}],
            },
        }
        resp = httpx.post(webhook_url, json=payload, timeout=10.0)
        return resp.status_code < 400
    except Exception:
        return False


def _send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
    """发送 Telegram 通知。"""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        resp = httpx.post(url, json=payload, timeout=10.0)
        return resp.status_code < 400
    except Exception:
        return False


def _send_slack(webhook_url: str, text: str) -> bool:
    """发送 Slack 通知。"""
    try:
        payload = {"text": text}
        resp = httpx.post(webhook_url, json=payload, timeout=10.0)
        return resp.status_code < 400
    except Exception:
        return False


def _send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    from_addr: str,
    to_addrs: List[str],
    subject: str,
    body: str,
    use_tls: bool = True,
) -> bool:
    """发送邮件通知。"""
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if use_tls:
                server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------- #
# 通知管理器
# ---------------------------------------------------------------------- #

class Notifier:
    """统一通知管理器。"""

    # 通知冷却：同一事件在冷却期内不重复发送
    _cooldowns: Dict[str, float] = {}
    _default_cooldown: float = 300.0  # 5 分钟

    def notify(
        self,
        event: str,
        severity: str = "info",
        title: str = "",
        message: str = "",
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """发送通知。

        :param event: 事件标识，用于冷却去重
        :param severity: 严重级别 info/warning/critical
        :param title: 通知标题
        :param message: 通知内容
        :param data: 附加数据
        :return: 是否至少有一个渠道发送成功
        """
        # 冷却检查
        cooldown_key = f"{event}:{severity}"
        now = time.time()
        last_sent = self._cooldowns.get(cooldown_key, 0)
        cooldown = self._get_cooldown_for_severity(severity)
        if now - last_sent < cooldown:
            return False

        notify_config = config.data.get("notifications", {}) if hasattr(config, "data") else {}
        if not notify_config.get("enabled", False):
            return False

        # 检查严重级别是否达到通知阈值
        severity_levels = {"info": 0, "warning": 1, "critical": 2}
        min_level = severity_levels.get(notify_config.get("min_severity", "warning"), 1)
        current_level = severity_levels.get(severity, 0)
        if current_level < min_level:
            return False

        success = False
        channels = notify_config.get("channels", [])

        for channel in channels:
            ch_type = channel.get("type", "")
            try:
                if ch_type == "webhook":
                    ok = _send_webhook(
                        channel["url"],
                        {"event": event, "severity": severity, "title": title, "message": message, "data": data},
                    )
                elif ch_type == "dingtalk":
                    ok = _send_dingtalk(channel["url"], title, message)
                elif ch_type == "wecom":
                    ok = _send_wecom(channel["url"], message)
                elif ch_type == "feishu":
                    ok = _send_feishu(channel["url"], title, message)
                elif ch_type == "telegram":
                    ok = _send_telegram(channel["bot_token"], channel["chat_id"], f"*{title}*\n{message}")
                elif ch_type == "slack":
                    ok = _send_slack(channel["url"], f"*{title}*\n{message}")
                elif ch_type == "email":
                    ok = _send_email(
                        channel.get("smtp_host", ""),
                        int(channel.get("smtp_port", 587)),
                        channel.get("smtp_user", ""),
                        channel.get("smtp_pass", ""),
                        channel.get("from", ""),
                        channel.get("to", "").split(","),
                        title,
                        message,
                    )
                else:
                    ok = False
                if ok:
                    success = True
            except Exception:
                pass

        if success:
            self._cooldowns[cooldown_key] = now

        return success

    def _get_cooldown_for_severity(self, severity: str) -> float:
        """根据严重级别返回冷却时间。"""
        if severity == "critical":
            return 60.0  # critical 1分钟冷却
        elif severity == "warning":
            return 300.0  # warning 5分钟冷却
        return 600.0  # info 10分钟冷却

    def clear_cooldown(self, event: str = "") -> None:
        """清除冷却记录。"""
        if event:
            keys_to_remove = [k for k in self._cooldowns if k.startswith(event)]
            for k in keys_to_remove:
                del self._cooldowns[k]
        else:
            self._cooldowns.clear()


# 全局单例
notifier = Notifier()
