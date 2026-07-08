"""Дашборд статистики (этап 6).

Доход за период со сравнением с предыдущим периодом равной длины; активные
подписчики; кто скоро заканчивается; число платежей и средний чек. График дохода —
серверный inline-SVG (синий — текущий период, зелёный пунктир — предыдущий), без
внешних JS-библиотек. Плюс переключаемые графики: регистрации, активность (bar),
распределение подписчиков (donut). Весь доход — подписки (билетов/событий нет).

Периоды: 30d (по умолчанию) · year · all · custom [from, to].
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Request

from ..db import get_pool
from .deps import current_admin, templates
from . import stats_repo

router = APIRouter()

PERIOD_LABELS = {"30d": "30 дней", "year": "Год", "all": "Всё время", "custom": "Период"}
_RU_MONTHS_SHORT = (
    "", "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)


# ── Период ───────────────────────────────────────────────────────────────────
def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.strptime(s.strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc)


def resolve_period(
    period: str, frm: str | None, to: str | None, now: datetime,
    first_payment: datetime | None,
):
    """Возвращает (period, start, end, prev_start, prev_end, label, custom_from, custom_to)."""
    if period == "year":
        start, end = now - timedelta(days=365), now
        return "year", start, end, start - timedelta(days=365), start, "Год", None, None
    if period == "all":
        start = (first_payment or now).replace(hour=0, minute=0, second=0, microsecond=0)
        return "all", start, now, None, None, "Всё время", None, None
    if period == "custom":
        d_from = _parse_date(frm)
        d_to = _parse_date(to)
        if d_from and d_to and d_from <= d_to:
            start = d_from
            end = d_to + timedelta(days=1)  # включительно по выбранную дату
            span = end - start
            label = f"{start:%d.%m.%Y} – {d_to:%d.%m.%Y}"
            return ("custom", start, end, start - span, start, label,
                    frm.strip(), to.strip())
        # некорректный диапазон → откатываемся к 30 дням
    start, end = now - timedelta(days=30), now
    return "30d", start, end, start - timedelta(days=30), start, "30 дней", None, None


# ── Корзины времени для графика ──────────────────────────────────────────────
def _trunc(dt: datetime, gran: str) -> datetime:
    dt = dt.replace(minute=0, second=0, microsecond=0)
    if gran == "month":
        return dt.replace(day=1, hour=0)
    return dt.replace(hour=0)


def _next_bucket(dt: datetime, gran: str) -> datetime:
    if gran == "month":
        return dt.replace(year=dt.year + 1, month=1) if dt.month == 12 \
            else dt.replace(month=dt.month + 1)
    return dt + timedelta(days=1)


def _axis(start: datetime, end: datetime, gran: str) -> list[datetime]:
    out: list[datetime] = []
    cur = _trunc(start, gran)
    while cur < end:
        out.append(cur)
        cur = _next_bucket(cur, gran)
    return out


def _series(buckets: dict[datetime, Decimal], axis: list[datetime]) -> list[float]:
    return [float(buckets.get(b, 0) or 0) for b in axis]


def _bucket_label(dt: datetime, gran: str) -> str:
    if gran == "month":
        return f"{_RU_MONTHS_SHORT[dt.month]} {dt.year % 100:02d}"
    return f"{dt.day:02d}.{dt.month:02d}"


# ── SVG-график ───────────────────────────────────────────────────────────────
def _nice_max(value: float) -> float:
    """Округляет максимум вверх до «красивого» (1/2/5 × 10^k) — ровная шкала."""
    if value <= 0:
        return 1.0
    import math
    exp = math.floor(math.log10(value))
    base = 10 ** exp
    for m in (1, 2, 2.5, 5, 10):
        if value <= m * base:
            return m * base
    return 10 * base


def _fmt_axis_value(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:g}М"
    if v >= 1_000:
        return f"{v / 1_000:g}к"
    return f"{v:g}"


def build_chart_svg(
    labels: list[str], current: list[float], previous: list[float] | None
) -> str:
    """Inline-SVG: синяя линия+заливка (текущий период) и зелёная пунктирная (предыдущий)."""
    W, H = 760, 260
    pad_l, pad_r, pad_t, pad_b = 52, 14, 14, 30
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    n = len(current)

    peak = max([*current, *(previous or []), 0.0])
    top = _nice_max(peak)

    def x(i: int) -> float:
        if n <= 1:
            return pad_l + plot_w / 2
        return pad_l + plot_w * i / (n - 1)

    def y(v: float) -> float:
        return pad_t + plot_h * (1 - v / top)

    parts: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" class="chart-svg" role="img" '
        f'preserveAspectRatio="xMidYMid meet">'
    ]
    for k in range(5):
        v = top * k / 4
        yy = y(v)
        parts.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{W - pad_r}" y2="{yy:.1f}" '
            f'class="chart-grid"/>'
        )
        parts.append(
            f'<text x="{pad_l - 8}" y="{yy + 4:.1f}" class="chart-ytick" '
            f'text-anchor="end">{_fmt_axis_value(v)}</text>'
        )
    if previous and any(previous):
        pts_prev = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(previous))
        parts.append(f'<polyline points="{pts_prev}" class="chart-line-prev"/>')
    if n >= 1:
        area = (
            f"{x(0):.1f},{y(0):.1f} "
            + " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(current))
            + f" {x(n - 1):.1f},{y(0):.1f}"
        )
        parts.append(f'<polygon points="{area}" class="chart-area"/>')
        pts_cur = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(current))
        parts.append(f'<polyline points="{pts_cur}" class="chart-line"/>')
    parts.append(
        f'<line x1="{pad_l}" y1="{y(0):.1f}" x2="{W - pad_r}" y2="{y(0):.1f}" '
        f'class="chart-axis"/>'
    )
    if n:
        step = max(1, round(n / 7))
        for i in range(0, n, step):
            parts.append(
                f'<text x="{x(i):.1f}" y="{H - 8}" class="chart-xtick" '
                f'text-anchor="middle">{labels[i]}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def build_bar_svg(labels: list[str], values: list[float], color: str = "#2563eb") -> str:
    """Столбчатый inline-SVG по корзинам времени (шкала как у графика дохода)."""
    W, H = 760, 260
    pad_l, pad_r, pad_t, pad_b = 52, 14, 14, 30
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    n = len(values)
    top = _nice_max(max([*values, 0.0]))

    def y(v: float) -> float:
        return pad_t + plot_h * (1 - v / top)

    parts: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" class="chart-svg" role="img" '
        f'preserveAspectRatio="xMidYMid meet">'
    ]
    for k in range(5):
        v = top * k / 4
        yy = y(v)
        parts.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{W - pad_r}" y2="{yy:.1f}" class="chart-grid"/>'
        )
        parts.append(
            f'<text x="{pad_l - 8}" y="{yy + 4:.1f}" class="chart-ytick" '
            f'text-anchor="end">{_fmt_axis_value(v)}</text>'
        )
    if n:
        slot = plot_w / n
        bw = max(1.0, min(slot * 0.7, 46))
        for i, v in enumerate(values):
            cx = pad_l + slot * (i + 0.5)
            h = plot_h * (v / top)
            parts.append(
                f'<rect x="{cx - bw / 2:.1f}" y="{y(v):.1f}" width="{bw:.1f}" '
                f'height="{h:.1f}" rx="2" fill="{color}"/>'
            )
    parts.append(
        f'<line x1="{pad_l}" y1="{y(0):.1f}" x2="{W - pad_r}" y2="{y(0):.1f}" class="chart-axis"/>'
    )
    if n:
        slot = plot_w / n
        step = max(1, round(n / 7))
        for i in range(0, n, step):
            cx = pad_l + slot * (i + 0.5)
            parts.append(
                f'<text x="{cx:.1f}" y="{H - 8}" class="chart-xtick" '
                f'text-anchor="middle">{labels[i]}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _bar(title: str, key: str, labels: list[str], values: list[float],
         color: str, total: int) -> dict:
    return {
        "title": title, "key": key, "kind": "bar",
        "svg": build_bar_svg(labels, values, color), "total": total,
    }


def build_donut_svg(segments: list[dict]) -> str:
    """Кольцевой (donut) график из сегментов [{value, color}]."""
    size, stroke = 200, 30
    r = (size - stroke) / 2
    cx = cy = size / 2
    import math
    circ = 2 * math.pi * r
    total = sum(max(0, s["value"]) for s in segments)

    parts = [
        f'<svg viewBox="0 0 {size} {size}" class="donut-svg" role="img" '
        f'preserveAspectRatio="xMidYMid meet">',
        f'<circle cx="{cx}" cy="{cy}" r="{r:.2f}" fill="none" '
        f'stroke="#e5e7eb" stroke-width="{stroke}"/>',
    ]
    if total > 0:
        offset = 0.0
        for s in segments:
            val = max(0, s["value"])
            if val == 0:
                continue
            seg_len = circ * val / total
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{r:.2f}" fill="none" '
                f'stroke="{s["color"]}" stroke-width="{stroke}" '
                f'stroke-dasharray="{seg_len:.2f} {circ - seg_len:.2f}" '
                f'stroke-dashoffset="{-offset:.2f}" '
                f'transform="rotate(-90 {cx} {cy})"/>'
            )
            offset += seg_len
    parts.append(
        f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" class="donut-total">{total}</text>'
        f'<text x="{cx}" y="{cy + 16}" text-anchor="middle" class="donut-total-sub">всего</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _donut(title: str, key: str, raw_segments: list[dict]) -> dict:
    total = sum(max(0, s["value"]) for s in raw_segments)
    legend = [
        {
            "label": s["label"], "color": s["color"], "value": s["value"],
            "pct": (s["value"] / total * 100) if total else 0,
        }
        for s in raw_segments
    ]
    return {
        "title": title, "key": key, "kind": "donut",
        "svg": build_donut_svg(raw_segments), "legend": legend, "total": total,
    }


# ── Форматирование ───────────────────────────────────────────────────────────
def fmt_money(v) -> str:
    """1234567.5 → '1 234 568 ₽' (без копеек, неразрывные пробелы тысяч)."""
    n = int(round(float(v or 0)))
    s = f"{n:,}".replace(",", " ")
    return f"{s} ₽"


def delta_info(cur: Decimal, prev: Decimal) -> dict:
    """Сравнение с предыдущим периодом: знак, проценты, направление."""
    cur_f, prev_f = float(cur or 0), float(prev or 0)
    diff = cur_f - prev_f
    if prev_f == 0:
        pct = None if cur_f == 0 else 100.0
    else:
        pct = diff / prev_f * 100
    return {
        "diff": diff, "pct": pct, "up": diff > 0, "down": diff < 0,
        "pct_text": "—" if pct is None else f"{'+' if pct >= 0 else '−'}{abs(pct):.0f}%",
    }


# ── Маршрут ──────────────────────────────────────────────────────────────────
@router.get("/")
async def dashboard(request: Request):
    admin = current_admin(request)
    period = request.query_params.get("period", "30d")
    frm = request.query_params.get("from")
    to = request.query_params.get("to")
    pool = get_pool()
    now = datetime.now(timezone.utc)
    first_payment = await stats_repo.first_payment_at(pool)

    period, start, end, prev_start, prev_end, label, cf, ct = resolve_period(
        period, frm, to, now, first_payment
    )

    total = await stats_repo.revenue_total(pool, start, end)
    pay_count = await stats_repo.payments_count(pool, start, end)
    subs = await stats_repo.active_subscribers(pool)
    soon = await stats_repo.expiring_soon(pool, days=7)

    has_prev = prev_start is not None
    prev_total = await stats_repo.revenue_total(pool, prev_start, prev_end) \
        if has_prev else Decimal(0)

    # График дохода: единая гранулярность для текущего и предыдущего периодов.
    span_days = max(1, (end - start).days)
    gran = "day" if span_days <= 92 else "month"
    axis = _axis(start, end, gran)
    cur_buckets = await stats_repo.revenue_buckets(pool, start, end, gran)
    cur_series = _series(cur_buckets, axis)
    if has_prev:
        prev_axis = _axis(prev_start, prev_end, gran)
        prev_buckets = await stats_repo.revenue_buckets(pool, prev_start, prev_end, gran)
        prev_series = _series(prev_buckets, prev_axis)
        prev_series = (prev_series + [0.0] * len(axis))[: len(axis)]
    else:
        prev_series = None
    chart_labels = [_bucket_label(b, gran) for b in axis]
    chart_svg = build_chart_svg(chart_labels, cur_series, prev_series)

    avg_check = (float(total) / pay_count) if pay_count else 0

    # Переключаемые графики: распределение подписчиков (donut) + динамика (bar).
    sub_dist = await stats_repo.subscriber_distribution(pool)
    charts = [
        _donut(
            "Распределение подписчиков", "subs_dist",
            [
                {"label": "С активной подпиской", "value": sub_dist["active"], "color": "#16a34a"},
                {"label": "Ушедшие (была подписка)", "value": sub_dist["former"], "color": "#f59e0b"},
                {"label": "Без подписки", "value": sub_dist["none"], "color": "#94a3b8"},
            ],
        ),
    ]
    reg_buckets = await stats_repo.registrations_buckets(pool, start, end, gran)
    act_buckets = await stats_repo.activity_buckets(pool, start, end, gran)
    act_total = await stats_repo.active_users_total(pool, start, end)
    reg_series = _series(reg_buckets, axis)
    act_series = _series(act_buckets, axis)
    charts.append(_bar(
        "Регистрации в боте по датам", "registrations",
        chart_labels, reg_series, "#16a34a", int(sum(reg_series)),
    ))
    charts.append(_bar(
        "Активность в боте по датам", "activity",
        chart_labels, act_series, "#0ea5e9", act_total,
    ))

    ctx = {
        "active": "dashboard",
        "admin": admin,
        "period": period,
        "period_label": label,
        "custom_from": cf or "",
        "custom_to": ct or "",
        "total_text": fmt_money(total),
        "has_prev": has_prev,
        "delta": delta_info(total, prev_total) if has_prev else None,
        "prev_total_text": fmt_money(prev_total),
        "subs": subs,
        "soon": soon,
        "pay_count": pay_count,
        "avg_check_text": fmt_money(avg_check),
        "chart_svg": chart_svg,
        "period_options": PERIOD_LABELS,
        "charts": charts,
    }
    return templates.TemplateResponse(request, "dashboard.html", ctx)
