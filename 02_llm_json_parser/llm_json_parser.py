"""Утилиты для извлечения и починки JSON из ответов LLM.

Типичный случай: модель оборачивает JSON в markdown-блок ```json ... ```,
либо добавляет «garbage» вокруг, либо ставит одинарные кавычки и
trailing-запятые. Этот модуль даёт три уровня защиты:

1. ``extract_json`` — честный парсинг с поддержкой markdown-обёртки.
2. ``repair_json`` — эвристический «ремонт» типичных ошибок LLM.
3. ``safe_parse`` — сначала пробует ``extract_json``, иначе чинит и пробует ещё раз.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Markdown-блок с указанием языка json: ```json ... ```
_MARKDOWN_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)
# Самая первая фигурная скобка
_FIRST_BRACE_RE = re.compile(r"[\{\[]")
# Последняя закрывающая скобка
_LAST_BRACKET_RE = re.compile(r"[\}\]]")
# Trailing comma перед ] или }, возможно с переносами/пробелами
_TRAILING_COMMA_RE = re.compile(r",\s*([\]\}])")
# JSON5-style комментарии
_JSONC_COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.DOTALL | re.MULTILINE)
# Не-обёрнутые ключи: , foo: или { bar:
_UNQUOTED_KEY_RE = re.compile(r"([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)")


def _balanced_json_slice(text: str, start: int) -> Optional[str]:
    """Возвращает подстроку с первого ``{``/``[`` до парной закрывающей скобки.

    Корректно работает с вложенными скобками и строками (включая экранированные кавычки).
    Возвращает ``None``, если баланс не сошёлся или встретился мусор после ``}``.
    """
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                slice_ = text[start : i + 1]
                # после закрывающей скобки допускаем только пробельные символы
                tail = text[i + 1 :].lstrip()
                return slice_ if not tail else slice_
    return None


def _try_parse(text: str) -> Optional[dict | list]:
    """Парсит ``text`` как JSON. Возвращает только dict/list, иначе ``None``."""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, (dict, list)) else None


def extract_json(text: str) -> Optional[dict]:
    """Достаёт JSON из текста: с markdown-обёрткой ```json ... ``` или «голый».

    :param text: исходная строка (часть ответа LLM).
    :returns: распарсенный ``dict`` или ``None``, если ничего не нашлось.
    """
    if not text:
        return None

    # 1) Пробуем markdown-блок ```json ... ```
    match = _MARKDOWN_JSON_RE.search(text)
    if match:
        result = _try_parse(match.group(1))
        if isinstance(result, dict):
            return result
        # если попался валидный JSON, но не dict (например, список),
        # всё равно пытаемся найти dict внутри текста ниже

    # 2) Иначе ищем первое вхождение { или [ и режем по балансу скобок
    obj_match = _FIRST_BRACE_RE.search(text)
    if obj_match:
        slice_ = _balanced_json_slice(text, obj_match.start())
        if slice_:
            result = _try_parse(slice_)
            if isinstance(result, dict):
                return result

    logger.debug("extract_json: ничего не нашлось в тексте длиной %d", len(text))
    return None


def repair_json(text: str) -> str:
    """Чинит типичные ошибки LLM в JSON-выводе.

    Применяемые эвристики (по порядку):

    1. Удаляет JSONC-комментарии (``// ...`` и ``/* ... */``).
    2. Срезает мусор вокруг — оставляет только фрагмент от первого ``{``/``[``
       до последней соответствующей закрывающей скобки.
    3. Заменяет Python-литералы ``None/True/False`` на JSON-``null/true/false``.
    4. Если в тексте **нет** двойных кавычек, но есть одинарные — заменяет
       все ``'`` на ``"`` (частый случай для LLM, которые путают Python и JSON).
    5. Убирает trailing-запятые перед ``]`` и ``}``.

    :param text: сырой ответ LLM.
    :returns: исправленная строка (не гарантирует, что валидна).
    """
    if not text:
        return text

    # 1) комментарии
    repaired = _JSONC_COMMENT_RE.sub("", text)

    # 2) срезаем мусор вокруг по первому { и последнему парному }
    first = _FIRST_BRACE_RE.search(repaired)
    last = _LAST_BRACKET_RE.search(repaired)
    if first is not None and last is not None and last.start() > first.start():
        slice_ = _balanced_json_slice(repaired, first.start())
        if slice_:
            repaired = slice_

    # 3) Python-литералы -> JSON
    repaired = re.sub(r"\bNone\b", "null", repaired)
    repaired = re.sub(r"\bTrue\b", "true", repaired)
    repaired = re.sub(r"\bFalse\b", "false", repaired)

    # 4) одинарные кавычки -> двойные, только если двойных нет вообще
    if '"' not in repaired and "'" in repaired:
        repaired = repaired.replace("'", '"')

    # 5) trailing comma перед ] или }
    repaired = _TRAILING_COMMA_RE.sub(r"\1", repaired)

    # 6) ключи без кавычек: {foo: 1, bar: 2} -> {"foo": 1, "bar": 2}
    repaired = _UNQUOTED_KEY_RE.sub(r'\1"\2"\3', repaired)

    return repaired


def safe_parse(text: str) -> Optional[dict]:
    """Извлекает dict из ответа LLM: сначала честный парсинг, иначе ремонт + повтор.

    :param text: ответ LLM (возможно, с markdown-обёрткой и «грязным» JSON).
    :returns: распарсенный ``dict`` или ``None``, если распарсить не удалось.
    """
    if not text:
        return None

    parsed = extract_json(text)
    if parsed is not None:
        return parsed

    logger.info("safe_parse: чистый парсинг не удался, пробуем repair")
    repaired = repair_json(text)
    parsed = extract_json(repaired)
    if parsed is not None:
        return parsed

    logger.error("safe_parse: не удалось распарсить JSON. Фрагмент: %.200s", text)
    return None
