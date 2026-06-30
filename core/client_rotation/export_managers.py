"""Выгрузка для МОП: standalone HTML-досье для принимающих менеджеров.

Порт client-rotate (exportForManagers / buildManagerDoc) на ORM платформы.
Самодостаточный HTML-файл (инлайн-CSS): оглавление по принимающим менеджерам
и карточка-дело на каждого назначенного клиента (score, уровень, разбивка
скоринга, обороты по кварталам, контакт, источники УКБ/ДСП/СП, разворот
холдинга списком ЮЛ, комментарий РОПа). Открывается в браузере, печатается.

Выгружаются клиенты с назначенным принимающим менеджером
(assignments.assigned_to_employee_id задан), tenant-wide - как выгрузка в 1С.
Холдинг НЕ разворачивается в отдельные карточки: состав ЮЛ показывается
заметкой внутри карточки головы (как в оригинале).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from core.models import (
    Assignment,
    ClientHandover,
    ClientRotationData,
    Company,
    Employee,
    Summary,
)

_MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def esc(s) -> str:
    """Экранирование для вставки в HTML (XSS-safe в скачанном файле)."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _tier_color(score) -> str:
    """Цвет score/вердикта по порогам 70/45 (tier().c из оригинала)."""
    s = score or 0
    if s >= 70:
        return "#C0463B"
    if s >= 45:
        return "#9A6B1E"
    return "#5B6472"


def _verdict_word(score) -> str:
    s = score or 0
    if s >= 70:
        return "Высокий приоритет"
    if s >= 45:
        return "Средний приоритет"
    return "Низкий приоритет"


_LVL_META = {
    "Ключевой":      {"dot": "#4B54D6", "bg": "#EEEFFD", "fg": "#3942B0"},
    "Корпоративный": {"dot": "#2F7D52", "bg": "#E9F4EE", "fg": "#256B45"},
    "Малый бизнес":  {"dot": "#9A6B1E", "bg": "#FBF1E2", "fg": "#7A551A"},
    "Микробизнес":   {"dot": "#82858C", "bg": "#F1F1F3", "fg": "#62656C"},
}


def _lvl_meta(level: str | None) -> dict:
    return _LVL_META.get(level or "", _LVL_META["Микробизнес"])


def _plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return one
    if 2 <= m10 <= 4 and (m100 < 10 or m100 >= 20):
        return few
    return many


def _inn_show(inn) -> str:
    """Суррогатный ИНН (начинается с '|') показываем как прочерк."""
    return inn if (inn and not str(inn).startswith("|")) else "-"


def _as_dict(v, default: dict) -> dict:
    return v if isinstance(v, dict) else default


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


def _num(v) -> float:
    """Безопасная коэрцификация числа из JSON (мусор/строка/None -> 0).

    Досье не должно ронять эндпоинт на кривых данных оборотов/разбивки.
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _handed_pairs(db: DBSession, tenant_id: int) -> set[tuple[int, int]]:
    """Множество (company_id, employee_id), уже переданных (есть запись журнала)."""
    rows = db.execute(
        select(ClientHandover.company_id, ClientHandover.employee_id)
        .where(ClientHandover.tenant_id == tenant_id, ClientHandover.employee_id.isnot(None))
    ).all()
    return {(cid, eid) for cid, eid in rows}


def _client_rows(db: DBSession, tenant_id: int) -> list[tuple[str, dict]]:
    """[(crm_name принимающего, поля клиента)] для всех назначенных клиентов.

    JOIN: companies + client_rotation_data + назначение + принимающий сотрудник
    (+ summaries опц.). Фильтр - assignment с заданным принимающим сотрудником.
    Каждая строка помечена флагом `handed` (передан ли уже текущему МОП) +
    несёт company_id/employee_id для записи в журнал передач.
    """
    handed = _handed_pairs(db, tenant_id)
    stmt = (
        select(Company, ClientRotationData, Summary, Assignment, Employee.crm_name)
        .join(ClientRotationData, ClientRotationData.company_id == Company.id)
        .join(Assignment, Assignment.company_id == Company.id)
        .outerjoin(Summary, Summary.company_id == Company.id)
        .join(Employee, Employee.id == Assignment.assigned_to_employee_id)
        .where(Company.tenant_id == tenant_id, Employee.crm_name.isnot(None))
    )
    out: list[tuple[str, dict]] = []
    for company, crd, summary, assignment, mgr in db.execute(stmt).all():
        emp_id = assignment.assigned_to_employee_id
        out.append((mgr, {
            "company_id": company.id,
            "employee_id": emp_id,
            "handed": (company.id, emp_id) in handed,
            "name": company.name,
            "inn": company.inn,
            "city": company.city,
            "is_holding_head": company.is_holding_head,
            "current_manager": crd.current_manager,
            "level": crd.level,
            "industry": crd.industry,
            "score": crd.score,
            "in_sp": crd.in_sp,
            "in_dsp": crd.in_dsp,
            "days_no_contact": crd.days_no_contact,
            "days_no_kp": crd.days_no_kp,
            "days_no_shipment": crd.days_no_shipment,
            "phone": crd.phone,
            "contact_person": crd.contact_person,
            "email": crd.email,
            "site": crd.site,
            "employees": crd.employees,
            "activity": crd.activity,
            "dsp_info": crd.dsp_info,
            "sp_info": crd.sp_info,
            "notes": crd.notes,
            "summary": crd.summary,
            "score_breakdown": _as_dict(crd.score_breakdown_json, {}),
            "turnover": _as_list(crd.turnover_json),
            "holding_members": _as_list(crd.holding_members_json),
            "comment": assignment.comment,
            "summary_llm": summary.summary if summary else None,
            "v_contact_name": summary.contact_name if summary else None,
            "v_contact_phone": summary.contact_phone if summary else None,
        }))
    return out


def _sum_text(c: dict) -> str:
    return c.get("summary_llm") or c.get("summary") or ""


def _bd_row(label: str, weight: str, val: int, mx: int, col: str) -> str:
    pct = min(100, round((val or 0) / mx * 100))
    return (
        f'<div class="bd"><div class="bdh"><span class="bdl">{esc(label)} <i>· {weight}</i></span>'
        f'<span class="bdv">{val or 0} / {mx}</span></div>'
        f'<div class="track"><div class="fill" style="width:{pct}%;background:{col};"></div></div></div>'
    )


def _card(c: dict) -> str:
    color = _tier_color(c["score"])
    lvl = _lvl_meta(c["level"])
    bd = c["score_breakdown"]
    to = c["turnover"]
    max_to = max([1.0] + [_num(v) for v in to])
    c_name = c.get("v_contact_name") or c.get("contact_person") or ""
    c_phone = c.get("v_contact_phone") or c.get("phone") or ""
    c_verified = bool(c.get("v_contact_phone") or c.get("v_contact_name"))
    score_disp = "-" if c["score"] is None else c["score"]

    # Обороты по кварталам (скрыто при пустом массиве).
    bars = ""
    if to:
        cols = ""
        for i, v in enumerate(to):
            h = max(6, round(_num(v) / max_to * 100))
            last = i == len(to) - 1
            bar_col = "#4B54D6" if last else "#C8CBE8"
            cols += (
                f'<div class="bcol"><div class="bwrap"><div class="bar" '
                f'style="height:{h}%;background:{bar_col};"></div></div>'
                f'<span class="blab">Q{i + 1}</span></div>'
            )
        bars = f'<div class="turn"><div class="turnh">Обороты по кварталам, ₽</div><div class="bars">{cols}</div></div>'

    ukb_grid = (
        '<div class="src"><div class="srch"><span class="tag ukb">УКБ</span> CRM-выгрузка</div><div class="srcb">'
        '<div class="kv3">'
        f'<div><dt>Отрасль</dt><dd>{esc(c.get("industry") or "-")}</dd></div>'
        f'<div><dt>Уровень</dt><dd>{esc(c.get("level") or "-")}</dd></div>'
        f'<div><dt>Город</dt><dd>{esc(c.get("city") or "-")}</dd></div>'
        '</div>'
        + bars
        + '<div class="days">'
        f'<div class="day{" bad" if (c.get("days_no_contact") or 0) >= 90 else ""}"><b>{c.get("days_no_contact") or 0}</b><span>дней без контакта</span></div>'
        f'<div class="day"><b>{c.get("days_no_kp") or 0}</b><span>дней без КП</span></div>'
        f'<div class="day"><b>{c.get("days_no_shipment") or 0}</b><span>дней без отгрузки</span></div>'
        '</div>'
        + (f'<blockquote class="q gray">{esc(c["notes"])}</blockquote>' if c.get("notes") else "")
        + '</div></div>'
    )

    dsp_grid = ""
    if c.get("in_dsp"):
        dsp_grid = (
            '<div class="src"><div class="srch"><span class="tag dsp">ДСП</span> Досье клиента</div><div class="srcb">'
            '<div class="kv2">'
            f'<div><dt>Телефон (авто)</dt><dd class="num">{esc(c.get("phone") or "-")}</dd></div>'
            f'<div><dt>Контактное лицо</dt><dd>{esc(c.get("contact_person") or "-")}</dd></div>'
            f'<div><dt>Email</dt><dd>{esc(c.get("email") or "-")}</dd></div>'
            f'<div><dt>Сайт</dt><dd>{esc(c.get("site") or "-")}</dd></div>'
            f'<div><dt>Вид деятельности</dt><dd>{esc(c.get("activity") or "-")}</dd></div>'
            f'<div><dt>Сотрудников</dt><dd class="num">{esc(c.get("employees") or "-")}</dd></div>'
            '</div>'
            + (f'<div class="qt">Что выяснил / действие</div><blockquote class="q amber">{esc(c["dsp_info"])}</blockquote>' if c.get("dsp_info") else "")
            + '</div></div>'
        )

    sp_block = ""
    if c.get("in_sp"):
        sp_block = (
            '<div class="src"><div class="srch"><span class="tag sp">СП</span> Стадия / ситуация</div><div class="srcb">'
            + (f'<blockquote class="q green">{esc(c["sp_info"])}</blockquote>' if c.get("sp_info") else '<div class="muted">Отмечен в СП без текста ситуации.</div>')
            + '</div></div>'
        )

    contact = ""
    if c_name or c_phone:
        contact = (
            f'<div class="contact{" verified" if c_verified else ""}">'
            f'<div class="ct">{"Контакт · проверенный" if c_verified else "Контакт · из ДСП"}</div>'
            f'<div class="cb"><span class="cn">{esc(c_name or "-")}</span>'
            + (f'<span class="cp num">{esc(c_phone)}</span>' if c_phone else "")
            + '</div></div>'
        )

    rop_comment = ""
    if c.get("comment"):
        rop_comment = (
            f'<div class="rop"><div class="ropl">Комментарий РОПа · зачем передаём</div>'
            f'<div class="ropt">{esc(c["comment"])}</div></div>'
        )

    hmem = c["holding_members"]
    holding_note = ""
    if c.get("is_holding_head") and hmem:
        names = " &middot; ".join(esc(m.get("name") if isinstance(m, dict) else m) for m in hmem)
        holding_note = (
            '<div style="margin:14px 0 0;padding:11px 15px;border-radius:11px;background:#F4F5FE;border:1px solid #DEE0F8;">'
            f'<div style="font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#3942B0;margin-bottom:5px;">Холдинг · передаётся целиком · {len(hmem) + 1} ЮЛ</div>'
            f'<div style="font-size:12.5px;color:#3A3C42;line-height:1.5;">{names}</div></div>'
        )

    summary = _sum_text(c)
    chips = (
        f'<span class="chip lvl" style="background:{lvl["bg"]};color:{lvl["fg"]};">'
        f'<i style="background:{lvl["dot"]};"></i>{esc(c.get("level") or "-")}</span>'
        + ('<span class="chip">СП</span>' if c.get("in_sp") else "")
        + ('<span class="chip">ДСП</span>' if c.get("in_dsp") else "")
    )

    return (
        '<article class="card">'
        '<header class="ch"><div class="chl">'
        f'<div class="chips">{chips}</div>'
        f'<h3>{esc(c["name"])}</h3>'
        f'<div class="meta num">ИНН {esc(_inn_show(c.get("inn")))} &middot; {esc(c.get("city") or "-")} &middot; передан от: {esc(c.get("current_manager") or "-")}</div>'
        '</div>'
        f'<div class="chs"><div class="score num" style="color:{color};">{score_disp}<span>/100</span></div>'
        f'<div class="verdict" style="color:{color};">{_verdict_word(c["score"])}</div></div>'
        '</header>'
        + holding_note
        + rop_comment
        + (f'<div class="summary"><div class="sl">Саммари по комментариям</div><p>{esc(summary)}</p></div>' if summary else "")
        + contact
        + '<div class="bdwrap">'
        + _bd_row("Размер бизнеса", "70%", int(_num(bd.get("size"))), 70, "#4B54D6")
        + _bd_row("Вовлечённость", "15%", int(_num(bd.get("engagement"))), 15, "#9A6B1E")
        + _bd_row("Свежесть", "15%", int(_num(bd.get("freshness"))), 15, "#2F7D52")
        + '</div>'
        + f'<div class="sources">{ukb_grid}{dsp_grid}{sp_block}</div>'
        + '</article>'
    )


def _manager_doc(mgr: str, clients: list[dict], date_str: str) -> str:
    """Самодостаточное HTML-досье для ОДНОГО принимающего менеджера."""
    cnt = len(clients)
    head = (
        '<header class="dochead"><div class="dh-l">'
        '<div class="brand"><span class="logo"></span>Ротация клиентской базы</div>'
        f'<h1>Досье для МОП: {esc(mgr) or "без МОП"}</h1>'
        f'<div class="dhmeta">Сформировано {esc(date_str)} &middot; '
        f'{cnt} {_plural(cnt, "клиент", "клиента", "клиентов")}</div>'
        '</div></header>'
    )
    cards = "".join(_card(c) for c in clients)
    title = f"Ротация - досье для {mgr or 'без МОП'} · {date_str}"
    return (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)}</title>'
        f'<style>{_CSS}</style></head><body>'
        '<div class="page">'
        + head
        + f'<section class="mgr">{cards}</section>'
        + '<footer class="docfoot">Конфиденциально · внутренний документ. Приоритет = очерёдность обзвона по score.</footer>'
        + '</div></body></html>'
    )


def _render_docs(rows: list[tuple[str, dict]]) -> list[tuple[str, str]]:
    """[(crm_name, HTML-досье)] - отдельный файл на каждого МОП.

    Клиенты группируются по принимающему менеджеру (внутри - по score DESC),
    каждому отдаётся своя самодостаточная HTML-страница. Отсортировано по МОП.
    """
    today = date.today()
    date_str = f"{today.day} {_MONTHS_GEN[today.month - 1]} {today.year}"
    groups: dict[str, list[dict]] = {}
    for mgr, c in rows:
        groups.setdefault(mgr or "", []).append(c)
    mgr_names = sorted(groups, key=lambda m: m.lower())
    for m in mgr_names:
        groups[m].sort(key=lambda c: (c["score"] or 0), reverse=True)
    return [(m, _manager_doc(m, groups[m], date_str)) for m in mgr_names]


def build_manager_export(
    db: DBSession, tenant_id: int, *, only_pending: bool
) -> tuple[list[tuple[str, str]], list[tuple[int, int, str]]]:
    """Готовит выгрузку для МОП и список передач к фиксации.

    only_pending=True  - в файлы попадают только ещё не переданные текущему МОП
    клиенты («Только новые»); False - все назначенные («Все», перевыпуск).

    Возвращает (docs, to_mark):
      docs    - [(crm_name, HTML)] по каждому принимающему МОП;
      to_mark - [(company_id, employee_id, crm_name)] для записи в журнал передач
                (всегда только ещё НЕ переданные среди вошедших - без дублей).
    """
    rows = _client_rows(db, tenant_id)
    included = [(m, c) for (m, c) in rows if (not only_pending) or (not c["handed"])]
    docs = _render_docs(included)
    to_mark = [
        (c["company_id"], c["employee_id"], m)
        for (m, c) in included
        if not c["handed"]
    ]
    return docs, to_mark


# CSS перенесён дословно из client-rotate (managerDocCSS).
_CSS = (
    "*{box-sizing:border-box;margin:0;padding:0;}"
    "body{font-family:'Golos Text',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#F4F5F6;color:#1B1C1F;-webkit-font-smoothing:antialiased;line-height:1.45;}"
    ".num{font-variant-numeric:tabular-nums;}"
    ".page{max-width:920px;margin:0 auto;padding:34px 30px 60px;}"
    ".dochead{padding:0 0 24px;border-bottom:1px solid #E4E5E8;margin-bottom:26px;}"
    ".brand{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:600;color:#62656C;margin-bottom:12px;}"
    ".logo{width:16px;height:16px;border-radius:5px;background:linear-gradient(150deg,#5862E0,#4049B8);}"
    ".dochead h1{font-size:27px;font-weight:700;letter-spacing:-.02em;color:#16171B;}"
    ".dhmeta{margin-top:8px;font-size:13px;color:#82858C;}"
    ".toc{background:#fff;border:1px solid #EAEBEE;border-radius:14px;padding:18px 22px;margin-bottom:30px;}"
    ".tocl{font-size:10.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#9EA1A8;margin-bottom:12px;}"
    ".toc ul{list-style:none;display:grid;grid-template-columns:1fr 1fr;gap:2px 28px;}"
    ".toc li{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid #F2F3F4;}"
    ".toc a{color:#2A2C31;text-decoration:none;font-size:14px;font-weight:500;}"
    ".toc a:hover{color:#4B54D6;}"
    ".toc li span{font-size:12px;color:#9EA1A8;font-weight:600;}"
    ".mgr{margin-bottom:40px;}"
    ".mgrh{display:flex;align-items:baseline;gap:12px;padding:0 2px 14px;position:sticky;top:0;background:#F4F5F6;}"
    ".mgrh h2{font-size:20px;font-weight:700;letter-spacing:-.01em;color:#16171B;}"
    ".cnt{font-size:12.5px;color:#82858C;font-weight:500;}"
    ".card{background:#fff;border:1px solid #EAEBEE;border-radius:16px;padding:22px 24px;margin-bottom:16px;page-break-inside:avoid;break-inside:avoid;}"
    ".ch{display:flex;align-items:flex-start;gap:18px;}"
    ".chl{flex:1;min-width:0;}"
    ".chips{display:flex;gap:6px;margin-bottom:9px;flex-wrap:wrap;}"
    ".chip{display:inline-flex;align-items:center;gap:5px;height:21px;padding:0 9px;border-radius:6px;background:#F0F1F3;color:#71747C;font-size:11px;font-weight:600;}"
    ".chip i{width:5px;height:5px;border-radius:50%;}"
    ".ch h3{font-size:21px;font-weight:600;letter-spacing:-.02em;color:#16171B;line-height:1.15;}"
    ".meta{margin-top:6px;font-size:12.5px;color:#82858C;}"
    ".chs{flex:none;text-align:right;}"
    ".score{font-size:38px;font-weight:700;letter-spacing:-.03em;line-height:1;}"
    ".score span{font-size:14px;font-weight:500;color:#B4B6BC;margin-left:1px;}"
    ".verdict{margin-top:5px;font-size:11px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;}"
    ".rop{margin:16px 0 0;padding:13px 16px;border-radius:11px;background:#F4F5FE;border:1px solid #DEE0F8;border-left:3px solid #4B54D6;}"
    ".ropl{font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#3942B0;margin-bottom:5px;}"
    ".ropt{font-size:14px;color:#26272B;font-weight:500;line-height:1.5;}"
    ".summary{margin-top:16px;}"
    ".sl{font-size:10.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#9EA1A8;margin-bottom:6px;}"
    ".summary p{font-size:13.5px;color:#3A3C42;line-height:1.5;}"
    ".contact{margin-top:14px;padding:11px 15px;border-radius:11px;background:#FAFAFB;border:1px solid #EEEFF1;}"
    ".contact.verified{background:#F6FBF8;border-color:#DDEEE4;}"
    ".ct{font-size:10px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:#9EA1A8;margin-bottom:4px;}"
    ".contact.verified .ct{color:#2F7D52;}"
    ".cb{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;}"
    ".cn{font-size:14.5px;font-weight:600;color:#1B1C1F;}"
    ".cp{font-size:13.5px;color:#3A3C42;}"
    ".bdwrap{margin-top:18px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;}"
    ".bd{}"
    ".bdh{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px;gap:8px;}"
    ".bdl{font-size:11.5px;font-weight:600;color:#3A3C42;}"
    ".bdl i{color:#A6A8AE;font-weight:500;font-style:normal;}"
    ".bdv{font-size:11px;color:#82858C;font-weight:600;white-space:nowrap;}"
    ".track{height:6px;border-radius:4px;background:#EFEFF1;overflow:hidden;}"
    ".fill{height:100%;border-radius:4px;}"
    ".sources{margin-top:18px;display:flex;flex-direction:column;gap:11px;}"
    ".src{border:1px solid #EEEFF1;border-radius:12px;overflow:hidden;}"
    ".srch{display:flex;align-items:center;gap:8px;padding:9px 15px;background:#FAFAFB;border-bottom:1px solid #F0F0F2;font-size:12px;font-weight:600;color:#4A4C52;}"
    ".tag{font-size:9.5px;font-weight:700;letter-spacing:.05em;padding:3px 7px;border-radius:5px;}"
    ".tag.ukb{color:#4B54D6;background:#EEEFFD;}"
    ".tag.dsp{color:#8A5A1E;background:#FBF1E2;}"
    ".tag.sp{color:#2F7D52;background:#E8F4ED;}"
    ".srcb{padding:14px 15px;}"
    ".kv3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px 16px;margin-bottom:14px;}"
    ".kv2{display:grid;grid-template-columns:1fr 1fr;gap:11px 16px;}"
    "dt{font-size:10.5px;color:#9EA1A8;font-weight:500;margin-bottom:2px;}"
    "dd{font-size:13px;color:#26272B;font-weight:500;}"
    ".turn{margin-bottom:14px;}"
    ".turnh{font-size:10.5px;color:#9EA1A8;font-weight:500;margin-bottom:9px;}"
    ".bars{display:flex;align-items:flex-end;gap:8px;height:46px;}"
    ".bcol{flex:1;display:flex;flex-direction:column;align-items:center;gap:5px;}"
    ".bwrap{width:100%;display:flex;align-items:flex-end;height:32px;}"
    ".bar{width:100%;border-radius:4px 4px 2px 2px;}"
    ".blab{font-size:9.5px;color:#A6A8AE;}"
    ".days{display:flex;gap:8px;margin-bottom:13px;}"
    ".day{flex:1;text-align:center;padding:9px 6px;border:1px solid #F0F0F2;border-radius:9px;}"
    ".day b{display:block;font-size:18px;font-weight:600;color:#3A3C42;font-variant-numeric:tabular-nums;}"
    ".day.bad b{color:#C0463B;}"
    ".day.bad{background:#FCF3F2;}"
    ".day span{font-size:9.5px;color:#9EA1A8;}"
    ".qt{font-size:10.5px;color:#9EA1A8;font-weight:500;margin:12px 0 6px;}"
    ".q{padding:10px 14px;border-radius:0 9px 9px 0;font-size:12.5px;line-height:1.5;color:#54565C;font-style:italic;border-left:2.5px solid #D9DAE0;background:#FAFAFB;}"
    ".q.amber{border-left-color:#E3C58E;background:#FCF8F1;}"
    ".q.green{border-left-color:#A9D4BC;background:#F6FBF8;}"
    ".muted{font-size:12.5px;color:#9EA1A8;font-style:italic;}"
    ".emptydoc{background:#fff;border:1px dashed #D9DAE0;border-radius:14px;padding:40px;text-align:center;color:#82858C;font-size:14px;}"
    ".docfoot{margin-top:30px;padding-top:18px;border-top:1px solid #E4E5E8;font-size:11.5px;color:#A6A8AE;text-align:center;}"
    "@media print{"
    "  body{background:#fff;}"
    "  .page{max-width:none;padding:0;}"
    "  .toc{page-break-after:always;}"
    "  .mgrh{position:static;}"
    "  .mgr{page-break-before:auto;}"
    "  .card{box-shadow:none;border-color:#D9DAE0;}"
    "  a{color:inherit;text-decoration:none;}"
    "  @page{margin:14mm;}"
    "}"
)
