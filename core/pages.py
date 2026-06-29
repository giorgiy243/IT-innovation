"""HTML-страницы платформы (Jinja2).

/login          — страница входа (всегда доступна)
/               — дашборд (редирект на /login если нет сессии делает JS)
/vendors        — список вендоров (JS проверяет сессию + права)
/vendors/{id}   — карточка вендора
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html")


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html")


@router.get("/vendors", response_class=HTMLResponse)
def vendors_list_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "vendors_list.html")


@router.get("/vendors/{vendor_id}", response_class=HTMLResponse)
def vendor_detail_page(request: Request, vendor_id: int) -> HTMLResponse:
    return templates.TemplateResponse(request, "vendor_detail.html")


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin_users.html")


@router.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "employees.html")
