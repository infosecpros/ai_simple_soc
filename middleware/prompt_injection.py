#!/usr/bin/env python3
"""
Защита от prompt injection атак.
"""

import re
import logging
from typing import Optional

from config.settings import get_config

logger = logging.getLogger(__name__)

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|commands|directions)", re.I),
    re.compile(r"forget\s+(everything|all|previous)", re.I),
    re.compile(r"you\s+are\s+(now|not)\s+", re.I),
    re.compile(r"system\s+(prompt|message|instruction)", re.I),
    re.compile(r"<\|im_start\|>", re.I),
    re.compile(r"<\|\w+\|>", re.I),
    re.compile(r"\[INST\]", re.I),
    re.compile(r"\[\/INST\]", re.I),
    re.compile(r"bypass\s+(the\s+)?(restrictions|rules|safety|guidelines)", re.I),
    re.compile(r"act\s+as\s+(if|though)\s+you\s+are", re.I),
    re.compile(r"{{[^}]+}}", re.I),
    re.compile(r"\{\s*[#/].*\}"),
]

SAFE_CONTEXTS = [
    "about ignoring", "about system", "about prompt", "about instruction",
]

# Фразы, которые разрешены в запросе (не блокируем)
ALLOWED_PHRASES = [
    "что такое system prompt", "как работает system prompt",
    "что такое ignore", "как работает ignore",
    "про system prompt", "про ignore",
]


def check_prompt_injection(text: str) -> Optional[str]:
    if not get_config().security.prompt_injection_check:
        return None

    text_lower = text.lower()

    # Проверяем разрешённые фразы
    for phrase in ALLOWED_PHRASES:
        if phrase in text_lower:
            return None

    for pattern in INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            matched = match.group()

            # Если в тексте есть пояснение про безопасность / вопрос — пропускаем
            if any(ctx.lower() in text_lower for ctx in SAFE_CONTEXTS):
                logger.warning(f"Low confidence injection: '{matched}'")
                continue

            logger.warning(f"Prompt injection detected: '{matched}'")
            return f"Prompt injection detected (pattern: {matched[:50]})"

    return None
