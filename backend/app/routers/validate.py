"""采集校验 分析看板接口：汇总指标、参数运行、结果导出与校验异常数据处置包。"""
from __future__ import annotations

import csv
import io
import random
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.disposal_engine import ensure_seed_package, service
from app.services.validate import ValidateService

router = APIRouter(prefix="/api/validate", tags=["采集校验"])
validate_service = ValidateService()
LIST_FIELDS = ["规则编号", "校验字段", "期望格式", "空值阈值", "重复判定", "异常策略", "阻断级别", "校验批次"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


def csv_safe(value: Any) -> str:
    """交给 csv.writer 自动加引号，单元格只做公式注入转义。"""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    return {"metrics": [
        {"label": "覆盖数据源", "value": random.randint(12, 45), "trend": random.randint(-12, 25)},
        {"label": "最新指标值", "value": round(random.uniform(0.72, 0.99), 3), "trend": random.randint(-8, 15)},
        {"label": "异常记录", "value": random.randint(0, 8), "trend": -random.randint(1, 10)},
    ]}


@router.get("")
def list_results() -> dict[str, Any]:
    return {"items": validate_service.list_results()}


@router.post("/run")
def run_analysis(payload: EntryPayload) -> dict[str, Any]:
    results = validate_service.run_analysis(payload.values)
    return {"items": results}


# ---------------- 校验异常数据处置包 ----------------

@router.get("/disposal/batches")
def list_batches() -> dict[str, Any]:
    """返回全部出现过处置包的校验批次，供操作人员按批次查看。"""
    ensure_seed_package()
    return {"items": service.list_batches()}


@router.get("/disposal/packages")
def list_packages(batch: str | None = Query(default=None, description="按校验批次过滤")) -> dict[str, Any]:
    """处置包清单：同一批次只暴露一个生效版本，已替换包仅用于审计追溯。"""
    ensure_seed_package()
    return {"items": service.list_packages(batch)}


@router.get("/disposal/packages/{package_no}")
def get_package(package_no: str) -> dict[str, Any]:
    package = service.get_package(package_no)
    if package is None:
        # 处置包丢失/错位进入缺陷清单，再按既有错误码返回 404。
        from app.services.disposal import ERR_PACKAGE_MISSING, repo
        repo.add_defect("处置包丢失", f"处置包 {package_no} 不存在或已错位，下游读取失败",
                        package_no=package_no, code=ERR_PACKAGE_MISSING)
        raise HTTPException(status_code=404, detail=f"{ERR_PACKAGE_MISSING} 校验异常数据处置包 {package_no} 不存在")
    return package


@router.post("/disposal/packages")
def submit_disposal_package(payload: dict[str, Any]) -> dict[str, Any]:
    """按校验规则计算异常样本处置结果并生成处置包。

    并发/连续提交同一批次时幂等：同规则版本只追加证据、沿用处置编号；
    新版本产生冲突时旧包标记“已替换”，下游只保留一份生效包。
    """
    ensure_seed_package()
    result = service.submit(payload)
    # 沿用既有动作契约 {ok, message, entry}，错误码放在 code 字段。
    return result


@router.get("/disposal/defects")
def list_defects(batch: str | None = Query(default=None)) -> dict[str, Any]:
    """缺陷清单：处置包报错、残留、不生效、丢失、错位与阻断冲突都在这里。"""
    ensure_seed_package()
    return {"items": service.list_defects(batch)}


@router.post("/disposal/defects/{defect_id}/resolve")
def resolve_defect(defect_id: int) -> ActionResult:
    defect = service.resolve_defect(defect_id)
    if defect is None:
        raise HTTPException(status_code=404, detail=f"缺陷 {defect_id} 不存在")
    return ActionResult(ok=True, message=f"缺陷 {defect['缺陷编号']} 已标记修复", entry=defect)


@router.post("/disposal/audit")
def audit_packages() -> dict[str, Any]:
    """巡检处置包：不生效、丢失、错位或重复发放时登记缺陷。"""
    found = service.audit()
    return {"ok": True, "message": f"巡检完成，新增缺陷 {len(found)} 条", "items": found}


@router.get("/disposal/export")
def export_disposal_packages(batch: str | None = Query(default=None)) -> StreamingResponse:
    """导出当前批次处置包明细（含处置编号、失败字段、阻断级别、规则版本）。"""
    ensure_seed_package()
    packages = service.list_packages(batch)
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    headers = ["处置编号", "校验批次", "规则版本", "证据编号", "样本编号", "失败字段",
               "异常类型", "阻断级别", "规则编号", "处置状态", "包状态"]
    writer.writerow(headers)
    for package in packages:
        for detail in package.get("明细", []):
            writer.writerow([
                csv_safe(package.get("处置编号", "")),
                csv_safe(package.get("校验批次", "")),
                csv_safe(package.get("规则版本", "")),
                csv_safe(detail.get("证据编号", "")),
                csv_safe(detail.get("样本编号", "")),
                csv_safe(detail.get("失败字段", "")),
                csv_safe(detail.get("异常类型", "")),
                csv_safe(detail.get("阻断级别", "")),
                csv_safe(detail.get("规则编号", "")),
                csv_safe(detail.get("处置状态", "")),
                csv_safe(package.get("包状态", "")),
            ])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=validate-disposal.csv"})


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = validate_service.list_results()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=validate.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = validate_service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"校验规则 {entry_id} 不存在")
    return entry
