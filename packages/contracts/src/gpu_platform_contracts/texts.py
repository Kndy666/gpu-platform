from typing import Any

from pydantic import Field

from .base import Contract


class Message(Contract):
    code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_.-]+$")
    parameters: dict[str, Any] = Field(default_factory=dict)


class LocalizedText(Contract):
    en: str = Field(min_length=1)
    zh_hans: str | None = None

    def resolve(self, language: str = "en") -> str:
        return self.zh_hans if language.lower().startswith("zh") and self.zh_hans else self.en
