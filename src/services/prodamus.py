"""Интеграция с Продамус (payform): подпись, ссылка на оплату, разбор вебхука.

Продамус не даёт готового Python-SDK — здесь воспроизведён его алгоритм подписи
(тот же, что в PHP-библиотеке `Hmac` из офф-документации и в рабочей python-либе
prodamuspy):

  1. все значения приводятся к строкам (аналог PHP strval: bool → "1"/"" , None → "");
  2. структура сериализуется в компактный JSON с сортировкой ключей
     (`json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False)`);
  3. HMAC-SHA256 по этой строке секретным ключом → hex.

Массив товаров в вебхуке приходит как последовательный (`products[0][name]=…`) —
после разбора это список словарей, поэтому JSON даёт массив, как в PHP.

Поток этапа 2 (разовый платёж):
  · build_payment_url — собрать GET-ссылку на payform по тарифу (с подписью);
  · parse_webhook_form / verify — принять и проверить уведомление об оплате.
Реккуренты (этап 8) лягут сюда же: подписочные поля Продамуса тем же алгоритмом.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Iterable
from urllib.parse import urlencode


# ── Подпись ───────────────────────────────────────────────────────────────────
def _stringify(value: Any) -> Any:
    """Рекурсивно приводит значения к строкам, как PHP strval перед json_encode.

    bool → "1"/"" , None → "" , числа → их строковое представление. Структуры
    (dict/list) обходятся вглубь, сохраняя тип контейнера.
    """
    if isinstance(value, bool):
        return "1" if value else ""
    if value is None:
        return ""
    if isinstance(value, dict):
        return {k: _stringify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify(v) for v in value]
    return str(value)


def sign_payload(data: dict) -> str:
    """Строка (компактный отсортированный JSON), по которой считается подпись.

    Вынесено отдельно для отладки: позволяет сравнить, что именно подписываем,
    со строкой на стороне Продамуса при расхождении подписи.
    """
    prepared = _stringify(data)
    payload = json.dumps(
        prepared, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    # PHP json_encode Продамуса (JSON_UNESCAPED_UNICODE, но БЕЗ UNESCAPED_SLASHES)
    # экранирует прямой слэш: '/' → '\/'. Воспроизводим — иначе подпись расходится
    # на значениях со слэшами (например payment_type "Visa/MasterCard/МИР, RUB").
    return payload.replace("/", "\\/")


def sign(data: dict, secret: str) -> str:
    """HMAC-SHA256-подпись данных секретным ключом платёжной страницы (hex)."""
    payload = sign_payload(data)
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify(data: dict, secret: str, sign_header: str | None) -> bool:
    """True, если подпись из заголовка Sign совпадает с пересчитанной по телу.

    Поле 'signature', если оно вдруг пришло в теле, в подпись не входит.
    Сравнение — постоянного времени и без учёта регистра (Продамус шлёт hex lower).
    """
    if not sign_header:
        return False
    body = {k: v for k, v in data.items() if k != "signature"}
    expected = sign(body, secret)
    return hmac.compare_digest(expected, sign_header.strip().lower())


# ── Сборка ссылки на оплату ───────────────────────────────────────────────────
def _flatten(prefix: str, value: Any, out: list[tuple[str, str]]) -> None:
    """Разворачивает вложенную структуру в пары bracket-ключей для query-строки.

    {'products': [{'name': 'X'}]} → products[0][name]=X (формат PHP-массивов,
    который ожидает payform).
    """
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]" if prefix else str(k), v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    else:
        out.append((prefix, "" if value is None else str(value)))


def build_link_params(
    *,
    order_id: str,
    product_name: str,
    price: str,
    quantity: int = 1,
    customer_extra: str = "",
    url_return: str = "",
    url_success: str = "",
    url_notification: str = "",
    do: str = "pay",
    extra: dict | None = None,
) -> dict:
    """Собирает словарь параметров платёжной ссылки (без подписи).

    price — строка вида "990.00" (сумма к оплате за весь период тарифа).
    do='pay' — сразу вести покупателя на оплату (для нашей кнопки-ссылки).
    """
    params: dict[str, Any] = {
        "order_id": order_id,
        "products": [
            {"name": product_name, "price": price, "quantity": str(quantity)}
        ],
        "do": do,
    }
    if customer_extra:
        params["customer_extra"] = customer_extra
    if url_return:
        params["urlReturn"] = url_return
    if url_success:
        params["urlSuccess"] = url_success
    if url_notification:
        params["urlNotification"] = url_notification
    if extra:
        params.update(extra)
    return params


def build_payment_url(base_url: str, params: dict, secret: str | None = None) -> str:
    """Строит полный URL платёжной страницы из base_url и параметров.

    Если задан секретный ключ — добавляет параметр signature (нужен, когда на
    платёжной странице включена «Оплата только по прямой ссылке» с подписью).
    """
    data = dict(params)
    if secret:
        data["signature"] = sign(
            {k: v for k, v in data.items() if k != "signature"}, secret
        )
    pairs: list[tuple[str, str]] = []
    for key, value in data.items():
        _flatten(key, value, pairs)
    query = urlencode(pairs)
    return f"{base_url.rstrip('/')}/?{query}"


# ── Разбор тела вебхука ───────────────────────────────────────────────────────
def parse_webhook_form(items: Iterable[tuple[str, str]]) -> dict:
    """Плоские bracket-ключи формы вебхука → вложенный словарь/список.

    products[0][name]=X&products[0][price]=100 → {'products': [{'name': 'X',
    'price': '100'}]}. Строго последовательные числовые ключи (0..n-1)
    превращаются в список — так же, как PHP разбирает $_POST в массив, что важно
    для точного совпадения подписи.
    """
    root: dict = {}
    for key, value in items:
        path = _split_key(key)
        _assign(root, path, value)
    return _listify(root)


def _split_key(key: str) -> list[str]:
    """order_id → ['order_id']; products[0][name] → ['products', '0', 'name']."""
    if "[" not in key:
        return [key]
    head, _, rest = key.partition("[")
    parts = [head]
    for chunk in rest.split("["):
        parts.append(chunk.rstrip("]"))
    return [p for p in parts if p != ""]


def _assign(container: dict, path: list[str], value: str) -> None:
    node = container
    for seg in path[:-1]:
        node = node.setdefault(seg, {})
        if not isinstance(node, dict):  # конфликт структуры — не должно случаться
            return
    node[path[-1]] = value


def _listify(node: Any) -> Any:
    """dict со строго последовательными числовыми ключами 0..n-1 → list (рекурсивно)."""
    if not isinstance(node, dict):
        return node
    converted = {k: _listify(v) for k, v in node.items()}
    keys = list(converted.keys())
    if keys and all(k.isdigit() for k in keys):
        ordered = sorted(keys, key=int)
        if [int(k) for k in ordered] == list(range(len(ordered))):
            return [converted[k] for k in ordered]
    return converted
