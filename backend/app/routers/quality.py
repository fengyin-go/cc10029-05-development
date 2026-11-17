"""质量报告 分析看板接口：汇总指标、参数运行与结果导出。"""
from __future__ import annotations

import csv
import io
import random
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.disposal_engine import ensure_seed_package
from app.services.quality import QualityService
from app.services.quality_dataset import dataset_service

router = APIRouter(prefix="/api/quality", tags=["质量报告"])
service = QualityService()
LIST_FIELDS = ["指标编号", "指标名称", "统计口径", "阈值下界", "阈值上界", "统计周期", "样本数量", "达标率"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    return {"metrics": [
        {"label": "覆盖数据源", "value": random.randint(12, 45), "trend": random.randint(-12, 25)},
        {"label": "最新指标值", "value": round(random.uniform(0.72, 0.99), 3), "trend": random.randint(-8, 15)},
        {"label": "异常记录", "value": random.randint(0, 8), "trend": -random.randint(1, 10)},
    ]}


@router.get("")
def list_results() -> dict[str, Any]:
    return {"items": service.list_results()}


@router.post("/run")
def run_analysis(payload: EntryPayload) -> dict[str, Any]:
    results = service.run_analysis(payload.values)
    return {"items": results}


# ---------------- 质量指标数据集（读取校验异常数据处置包生成） ----------------

@router.get("/datasets")
def list_datasets(batch: str | None = Query(default=None, description="按校验批次过滤")) -> dict[str, Any]:
    """质量指标数据集清单：数字直接取自处置包明细，已替换包不进入下游清单。"""
    ensure_seed_package()
    return {"items": dataset_service.list_datasets(batch)}


@router.get("/datasets/{batch}")
def get_dataset(batch: str) -> dict[str, Any]:
    """下游模块只允许读取一份绑定规则版本的完整生效处置包。"""
    ensure_seed_package()
    dataset = dataset_service.active_dataset(batch)
    if dataset is None:
        from app.services.disposal import ERR_PACKAGE_MISSING, repo
        repo.add_defect("处置包丢失",
                        f"质量报告读取批次 {batch} 时未找到生效处置包，指标无法生成",
                        batch=batch, code=ERR_PACKAGE_MISSING)
        raise HTTPException(status_code=404,
                            detail=f"{ERR_PACKAGE_MISSING} 批次 {batch} 没有生效的校验异常数据处置包")
    return dataset


@router.post("/datasets/sync")
def sync_datasets() -> ActionResult:
    """把处置包结论同步到质量指标数据集清单，并核对口径一致性。"""
    ensure_seed_package()
    datasets = dataset_service.list_datasets()
    drifts = dataset_service.consistency_check()
    if drifts:
        from app.services.disposal import repo
        for drift in drifts:
            repo.add_defect("指标口径错位", drift["问题"], batch=drift["校验批次"],
                            package_no=drift["处置编号"], code="E4009")
        return ActionResult(ok=False,
                            message=f"同步发现 {len(drifts)} 项指标与处置包明细不一致，已进入缺陷清单",
                            entry={"datasets": datasets, "drifts": drifts})
    return ActionResult(ok=True,
                        message=f"处置包结论已同步到质量指标数据集，共 {len(datasets)} 条，口径与明细一致",
                        entry={"datasets": datasets})


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.list_results()
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
                             headers={"Content-Disposition": "attachment; filename=quality.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"质量指标 {entry_id} 不存在")
    return entry
