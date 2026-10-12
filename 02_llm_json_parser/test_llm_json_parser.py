"""Юнит-тесты для ``llm_json_parser.safe_parse`` (и хелперов)."""

from __future__ import annotations

import logging

import pytest

from llm_json_parser import extract_json, repair_json, safe_parse


# ----------------------------- safe_parse ----------------------------- #


def test_safe_parse_clean_json() -> None:
    """Чистый JSON-объект без какой-либо обёртки."""
    text = '{"name": "Alice", "salary": 250000}'
    assert safe_parse(text) == {"name": "Alice", "salary": 250000}


def test_safe_parse_markdown_wrapped() -> None:
    """JSON внутри markdown-кода с меткой ```json...``` и пояснением вокруг."""
    text = (
        "Вот твой ответ:\n\n"
        "```json\n"
        '{"city": "Moscow", "salary": 400000}\n'
        "```\n\n"
        "Если нужно — обращайся."
    )
    assert safe_parse(text) == {"city": "Moscow", "salary": 400000}


def test_safe_parse_single_quotes_repair() -> None:
    """Сломанный JSON с одинарными кавычками — repair должен починить."""
    text = "{'skill': 'AI/ML', 'level': 'junior'}"
    assert safe_parse(text) == {"skill": "AI/ML", "level": "junior"}


def test_safe_parse_trailing_comma_repair() -> None:
    """Trailing comma перед ``}`` — repair уберёт её."""
    text = '{"items": ["a", "b", "c",], "ok": True,}'
    assert safe_parse(text) == {"items": ["a", "b", "c"], "ok": True}


def test_safe_parse_returns_none_and_logs_on_garbage(caplog: pytest.LogCaptureFixture) -> None:
    """Полный мусор — возвращается ``None`` и логируется ошибка."""
    with caplog.at_level(logging.ERROR, logger="llm_json_parser"):
        result = safe_parse("lorem ipsum dolor sit amet, никакого json здесь нет")
    assert result is None
    assert any(
        "не удалось распарсить" in record.message for record in caplog.records
    ), f"Ожидался лог об ошибке, получили: {[r.message for r in caplog.records]}"


# ----------------------- extract_json / repair_json --------------------- #


def test_extract_json_prefers_dict_over_list() -> None:
    """В тексте есть валидный список, но не dict — возвращаем None."""
    text = '[1, 2, 3]'
    assert extract_json(text) is None


def test_repair_json_handles_python_literals_and_garbage() -> None:
    """Repair снимает обёртку и заменяет ``None/True/False`` на JSON-литералы."""
    text = "model says: {active: True, role: None, name: 'Bob'} end."
    repaired = repair_json(text)
    assert "'" not in repaired
    assert "None" not in repaired and "True" not in repaired
    import json as _json

    assert _json.loads(repaired) == {"active": True, "role": None, "name": "Bob"}
