"""Тесты интеграции с Продамус: подпись, разбор вебхука, сборка ссылки.

Самодостаточный скрипт (без pytest): `python -m tests.test_prodamus`.

Важно: здесь проверяется ВНУТРЕННЯЯ согласованность нашей реализации алгоритма
Продамуса (что приёмник примет ровно то, что подписал наш код по документированному
алгоритму) и корректность разбора bracket-ключей. Сверку с «живой» подписью самого
Продамуса можно сделать только на реальном вебхуке с боевым секретным ключом
(заказчица — после подключения payform и деплоя).
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from src.services import prodamus

DEMO_SECRET = "2y2aw4oknnke80bp1a8fniwuuq7tdkwmmuq7vwi4nzbr8z1182ftbn6p8mhw3bhz"


def test_sign_roundtrip() -> None:
    data = {"order_num": "md-1-abc", "sum": "990.00", "payment_status": "success"}
    s = prodamus.sign(data, DEMO_SECRET)
    assert prodamus.verify(data, DEMO_SECRET, s) is True
    # Регистронезависимо (Продамус шлёт hex в нижнем регистре, но проверим верх).
    assert prodamus.verify(data, DEMO_SECRET, s.upper()) is True
    # Битая подпись и пустой заголовок — отвергаются.
    assert prodamus.verify(data, DEMO_SECRET, "deadbeef") is False
    assert prodamus.verify(data, DEMO_SECRET, None) is False


def test_sign_order_independent() -> None:
    a = {"b": "2", "a": "1", "c": "3"}
    b = {"c": "3", "a": "1", "b": "2"}
    assert prodamus.sign(a, DEMO_SECRET) == prodamus.sign(b, DEMO_SECRET)


def test_sign_ignores_body_signature_field() -> None:
    # Если Продамус продублирует подпись в теле — она не должна влиять на проверку.
    data = {"order_num": "x", "sum": "100.00"}
    s = prodamus.sign(data, DEMO_SECRET)
    with_field = {**data, "signature": "whatever"}
    assert prodamus.verify(with_field, DEMO_SECRET, s) is True


def test_parse_webhook_form_nested_products() -> None:
    items = [
        ("date", "2020-07-27T12:31:01+03:00"),
        ("order_id", "300155"),
        ("order_num", "md-42-deadbeef0001"),
        ("sum", "990.00"),
        ("customer_phone", "+79999999999"),
        ("products[0][name]", 'Доступ в клуб "Fit it"'),
        ("products[0][price]", "990.00"),
        ("products[0][quantity]", "1"),
        ("products[0][sum]", "990.00"),
        ("payment_status", "success"),
        ("payment_status_description", "Успешная оплата"),
    ]
    data = prodamus.parse_webhook_form(items)
    assert data["order_num"] == "md-42-deadbeef0001"
    assert isinstance(data["products"], list) and len(data["products"]) == 1
    assert data["products"][0]["name"] == 'Доступ в клуб "Fit it"'
    assert data["products"][0]["price"] == "990.00"


def test_parse_then_verify_endtoend() -> None:
    """Приёмник примет тело, подписанное нашим кодом (эмуляция вебхука)."""
    items = [
        ("order_num", "md-7-abcdef012345"),
        ("sum", "2370.00"),
        ("payment_status", "success"),
        ("products[0][name]", "Подписка в клуб — 3 месяца"),
        ("products[0][price]", "2370.00"),
        ("products[0][quantity]", "1"),
    ]
    data = prodamus.parse_webhook_form(items)
    sign_header = prodamus.sign(data, DEMO_SECRET)
    # Приёмник заново парсит те же items и проверяет — должно совпасть.
    reparsed = prodamus.parse_webhook_form(items)
    assert prodamus.verify(reparsed, DEMO_SECRET, sign_header) is True
    # Другой секрет не проходит.
    assert prodamus.verify(reparsed, "wrong-secret", sign_header) is False


def test_slash_escaping_matches_php() -> None:
    """Слэши в значениях экранируются как в PHP json_encode Продамуса ('/'→'\\/').

    Регрессия к реальному расхождению: payment_type «Visa/MasterCard/МИР» давал
    другую подпись, пока слэши не экранировались. Эталон посчитан демо-ключом
    (публичный ключ демо-формы из офф-документации, не боевой).
    """
    body = {
        "order_num": "x1",
        "sum": "55.00",
        "payment_type": "Visa/MasterCard/МИР, RUB",
        "payment_status": "success",
    }
    # В строке для подписи слэши экранированы.
    assert "Visa\\/MasterCard\\/МИР" in prodamus.sign_payload(body)
    # И подпись стабильна (совместима с PHP-стороной Продамуса).
    assert prodamus.sign(body, DEMO_SECRET) == (
        "f86ffe5875778e2954ee5cd261cc6373c624f1d3911f96062b3d38525079cff7"
    )


def test_build_payment_url() -> None:
    params = prodamus.build_link_params(
        order_id="md-1-xyz",
        product_name="Подписка в клуб — 1 месяц",
        price="990.00",
        customer_extra="tg_id=1",
        do="pay",
    )
    url = prodamus.build_payment_url(
        "https://dolbikfit.payform.ru", params, secret=DEMO_SECRET
    )
    assert url.startswith("https://dolbikfit.payform.ru/?")
    q = dict(parse_qsl(urlsplit(url).query))
    assert q["order_id"] == "md-1-xyz"
    assert q["products[0][price]"] == "990.00"
    assert q["products[0][quantity]"] == "1"
    assert q["do"] == "pay"
    assert len(q["signature"]) == 64  # hex sha256


def _run() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nВсе тесты пройдены: {len(tests)}")


if __name__ == "__main__":
    _run()
