"""Capabilities for the browser-facing transport, independent of its vendor.

The local named-pipe manager is intentionally outside this policy. Public
transport has structured ChatGPT controls and a view-only window surface.
This is an application capability boundary, not a sandbox for ChatGPT itself.
"""
from __future__ import annotations


class PolicyError(ValueError):
    pass


class RemotePolicy:
    def __init__(self, public: bool):
        self.public = bool(public)

    def capabilities(self) -> dict:
        return {
            'scope': 'chatgpt-window' if self.public else 'legacy-local',
            'desktop': not self.public,
            'rawInput': not self.public,
            'windowView': True,
            'conversation': True,
            'nativeQueue': True,
            'composerOptions': True,
        }

    def require_window(self, mode: str) -> None:
        if self.public and mode != 'window':
            raise PolicyError('公网连接仅允许所选 ChatGPT 窗口，请在电脑端恢复窗口模式。')

    def allow_desktop(self) -> None:
        if self.public:
            raise PolicyError('公网连接不提供 Windows 全桌面控制。')

    def allow_action(self, data: dict, mode: str) -> None:
        if not self.public:
            return
        self.require_window(mode)
        if data.get('kind') not in ('message', 'navigate', 'draft', 'composer', 'queue'):
            raise PolicyError('公网画面仅供查看，请使用对话、模型和原生队列控件。')
        if data.get('kind') == 'composer' and data.get('operation') not in ('invoke', 'dismiss'):
            raise PolicyError('该选项需要在电脑端完成；公网不开放原始坐标控制。')
