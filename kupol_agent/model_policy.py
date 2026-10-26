"""DeepSeek-only policy with an optional explicitly pinned served model.

Both operating profiles use the available DeepSeek model by default. A precise
version is an opt-in configuration constraint, not a mandatory final-game gate.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from .llm import PlannerError


def _deepseek_served(value):
    return isinstance(value, str) and bool(re.fullmatch(r"deepseek[-a-zA-Z0-9_.]{0,120}", value, re.I))


class ModelPolicy:
    def __init__(self, profile="training", *, model=None, base_url=None, final_model=None, final_served_model=None,
                 organizer_endpoint_approved=None):
        if profile not in {"training", "competition"}:
            raise ValueError("Неизвестный профиль работы")
        self.profile = profile
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        self.final_model = final_model if final_model is not None else os.getenv("DEEPSEEK_FINAL_MODEL", "")
        self.expected_served = final_served_model if final_served_model is not None else os.getenv("DEEPSEEK_FINAL_SERVED_MODEL", "")
        self.model = self.final_model if profile == "competition" and self.final_model else model or os.getenv("DEEPSEEK_MODEL") or "deepseek-flash"
        self.organizer_endpoint_approved = (os.getenv("DEEPSEEK_ORGANIZER_ENDPOINT_APPROVED") == "1"
                                             if organizer_endpoint_approved is None else organizer_endpoint_approved is True)

    @property
    def allow_custom_endpoint(self):
        return self.organizer_endpoint_approved

    def readiness_error(self):
        try:
            parsed = urlsplit(self.base_url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment):
                return "Endpoint DeepSeek должен быть HTTPS без ключей, query или fragment в адресе"
            if parsed.hostname == "api.deepseek.com" and parsed.port not in (None, 443):
                return "Публичный DeepSeek API разрешён только на HTTPS-порту 443"
            if parsed.hostname != "api.deepseek.com" and not self.organizer_endpoint_approved:
                return "Отдельный endpoint DeepSeek организаторов должен быть явно указан в конфигурации"
        except ValueError:
            return "Некорректный адрес endpoint DeepSeek"
        if not isinstance(self.model, str) or not re.fullmatch(r"deepseek[-a-zA-Z0-9_.]{0,120}", self.model):
            return "Некорректный идентификатор модели DeepSeek"
        if not isinstance(self.expected_served, str) or (self.expected_served and not _deepseek_served(self.expected_served)):
            return "Необязательная фиксация версии должна содержать идентификатор модели DeepSeek"
        return None

    def ensure_ready(self):
        error = self.readiness_error()
        if error:
            raise PlannerError(error)

    def validate_response(self, served_model):
        self.ensure_ready()
        if not _deepseek_served(served_model):
            raise PlannerError("API не подтвердил модель DeepSeek; решение отклонено до исполнения")
        if self.expected_served and served_model.casefold() != self.expected_served.casefold():
            raise PlannerError("API вернул другую явно закреплённую версию DeepSeek; решение отклонено до исполнения")

    def public_status(self):
        error = self.readiness_error()
        return {"profile": self.profile, "model": self.model, "required_family": "DeepSeek",
                "expected_served_model": self.expected_served or None, "ready": error is None,
                "version_pinned": bool(self.expected_served), "reason": error}
