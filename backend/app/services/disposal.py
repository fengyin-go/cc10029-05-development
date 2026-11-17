"""校验异常数据处置包：按校验规则计算异常样本处置结果，并按校验批次归档。

设计要点（对应验收口径）：
- 处置包必须与校验规则版本同时保存，缺少规则依据的样本不进入待处置范围；
- 同一批次重复校验只追加新的异常证据，不复制整份处置包、不重复发放处置编号；
- 两份处置包并发提交时只保留一个生效版本（进程内互斥锁 + 批次幂等键）；
- 处置包报错或残留、不生效/丢失/错位统一进入缺陷清单；
- 新旧规则阻断结论冲突时，旧包标记为“已替换”，下游只读到一份绑定规则版本的完整包。
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Any

# ---- 常量与错误码（沿用既有 {ok, message} 契约，错误码集中在 message 前缀） ----

EVIDENCE_TYPES = ("空值", "格式", "重复")
LEVELS = ("阻断", "告警", "提示")
PACKAGE_STATUSES = ("生效", "已替换")
EVIDENCE_STATUSES = ("待处置", "处置中", "已处置")

ERR_NO_BATCH = "E4001"          # 缺少校验批次
ERR_NO_RULE = "E4002"           # 缺少规则依据（规则或规则版本缺失）
ERR_NO_SAMPLE = "E4003"         # 缺少待校验样本
ERR_RULE_INVALID = "E4004"      # 规则字段不完整/阻断级别非法
ERR_CONFLICT_LEVEL = "E4009"    # 同一样本新旧规则阻断结论冲突
ERR_PACKAGE_BROKEN = "E5001"    # 处置包报错或残留
ERR_PACKAGE_MISSING = "E4041"   # 处置包不生效、丢失或错位

RULE_REQUIRED = ("规则编号", "校验字段", "规则版本")
FORMAT_PATTERNS = {
    "整数": r"^[+-]?\d+$",
    "数字": r"^[+-]?\d+(\.\d+)?$",
    "金额": r"^[+-]?\d+(\.\d{1,2})?$",
    "邮箱": r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    "URL": r"^https?://[^\s]+$",
    "手机号": r"^1\d{10}$",
    "日期": r"^\d{4}-\d{2}-\d{2}$",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_level(value: Any) -> str | None:
    text = str(value or "").strip()
    for level in LEVELS:
        if text == level or text.startswith(level):
            return level
    return None


def normalize_format(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "文本"


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


class DisposalRepository:
    """处置包内存仓库：自带互斥锁，保证并发提交只产生一个生效版本。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._packages: list[dict[str, Any]] = []
        self._defects: list[dict[str, Any]] = []
        self._seq_package = 0
        self._seq_evidence = 0
        self._seq_defect = 0

    # ---------- 缺陷登记 ----------
    def add_defect(self, defect_type: str, detail: str, *, batch: str | None = None,
                   package_no: str | None = None, code: str = ERR_PACKAGE_BROKEN) -> dict[str, Any]:
        with self._lock:
            self._seq_defect += 1
            defect = {
                "id": self._seq_defect,
                "缺陷编号": f"DEF-{self._seq_defect:04d}",
                "缺陷类型": defect_type,
                "错误码": code,
                "校验批次": batch or "",
                "处置编号": package_no or "",
                "缺陷说明": detail,
                "状态": "待处理",
                "登记时间": _now(),
            }
            self._defects.append(defect)
            return dict(defect)

    def list_defects(self, batch: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(d) for d in self._defects]
        if batch:
            rows = [d for d in rows if d["校验批次"] == batch]
        return rows

    def resolve_defect(self, defect_id: int) -> dict[str, Any] | None:
        with self._lock:
            for defect in self._defects:
                if defect["id"] == defect_id:
                    defect["状态"] = "已修复"
                    defect["修复时间"] = _now()
                    return dict(defect)
        return None

    # ---------- 处置包存取 ----------
    def lock(self) -> threading.RLock:
        return self._lock

    def next_package_no(self) -> str:
        self._seq_package += 1
        return f"DSP-{datetime.now().strftime('%Y%m%d')}-{self._seq_package:04d}"

    def next_evidence_no(self) -> str:
        self._seq_evidence += 1
        return f"EVD-{self._seq_evidence:06d}"

    def save_package(self, package: dict[str, Any]) -> None:
        with self._lock:
            self._packages.append(package)

    def all_packages(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(p) for p in self._packages]

    def find_by_no(self, package_no: str) -> dict[str, Any] | None:
        with self._lock:
            for package in self._packages:
                if package["处置编号"] == package_no:
                    return package
        return None

    def active_for_batch(self, batch: str) -> dict[str, Any] | None:
        with self._lock:
            for package in self._packages:
                if package["校验批次"] == batch and package["包状态"] == "生效":
                    return package
        return None

    def list_batches(self) -> list[str]:
        with self._lock:
            seen: list[str] = []
            for package in self._packages:
                if package["校验批次"] not in seen:
                    seen.append(package["校验批次"])
            return seen

    def mark_superseded(self, package: dict[str, Any], reason: str) -> None:
        with self._lock:
            package["包状态"] = "已替换"
            package["失效时间"] = _now()
            package["失效原因"] = reason

    def audit(self) -> list[dict[str, Any]]:
        """巡检：处置包不生效、丢失、错位或重复发放时登记缺陷。"""
        found: list[dict[str, Any]] = []
        with self._lock:
            active_by_batch: dict[str, list[str]] = {}
            for package in self._packages:
                if package["包状态"] == "生效":
                    active_by_batch.setdefault(package["校验批次"], []).append(package["处置编号"])
                # 错位：处置包没有绑定规则版本或没有明细
                if not package.get("规则版本") or not package.get("明细"):
                    found.append(self.add_defect(
                        "处置包错位",
                        f"处置包 {package['处置编号']} 缺少规则版本或异常明细，无法作为下游依据",
                        batch=package.get("校验批次"), package_no=package.get("处置编号"),
                        code=ERR_PACKAGE_MISSING))
            for batch, nos in active_by_batch.items():
                if len(set(nos)) > 1:
                    found.append(self.add_defect(
                        "处置包重复发放",
                        f"批次 {batch} 同时存在 {len(set(nos))} 份生效处置包：{'、'.join(sorted(set(nos)))}",
                        batch=batch, package_no="、".join(sorted(set(nos))),
                        code=ERR_PACKAGE_BROKEN))
        return found


# 进程级单例仓库：路由层、质量报告与种子数据共用同一份状态。
repo = DisposalRepository()
