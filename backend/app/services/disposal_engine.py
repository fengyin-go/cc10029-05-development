"""校验异常处置引擎：按校验规则计算空值/格式/重复异常，并生成处置包。"""
from __future__ import annotations

import copy
import re
import threading
from typing import Any

from app.services.disposal import (
    EVIDENCE_TYPES,
    ERR_NO_BATCH,
    ERR_NO_RULE,
    ERR_NO_SAMPLE,
    ERR_PACKAGE_BROKEN,
    ERR_RULE_INVALID,
    FORMAT_PATTERNS,
    LEVELS,
    normalize_format,
    normalize_level,
    repo,
    _is_blank,
    _now,
)
from app.store import store

MODULE = "validate"
SEED_BATCH = "BATCH-DEMO-01"
_seed_lock = threading.Lock()
_seeded = False


def _fail(message: str, code: str, *, batch: str | None = None,
          package_no: str | None = None, defect_type: str = "处置包报错") -> dict[str, Any]:
    """处置包报错统一落缺陷清单，再按既有动作契约返回。"""
    repo.add_defect(defect_type, message, batch=batch, package_no=package_no, code=code)
    return {"ok": False, "code": code, "message": message, "entry": None}


def _normalize_rules(raw_rules: list[dict[str, Any]], fallback_version: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for raw in raw_rules:
        rule = dict(raw)
        code = str(rule.get("规则编号") or "").strip()
        field = str(rule.get("校验字段") or "").strip()
        version = str(rule.get("规则版本") or fallback_version or "").strip()
        if not code or not field or not version:
            # 缺少规则依据的规则不参与计算，交给上层判定。
            continue
        level = normalize_level(rule.get("阻断级别"))
        if level is None:
            continue
        dup = str(rule.get("重复判定") or "").strip()
        null_hold = str(rule.get("空值阈值") or "").strip()
        rules.append({
            "规则编号": code,
            "校验字段": field,
            "规则版本": version,
            "期望格式": normalize_format(rule.get("期望格式")),
            "空值阈值": null_hold,
            "空值校验": null_hold not in ("不校验", "停用", ""),
            "重复判定": dup,
            "重复校验": dup in ("启用", "是", "Y", "true", "True", "1"),
            "异常策略": str(rule.get("异常策略") or level),
            "阻断级别": level,
        })
    return rules


def _load_store_rules(fallback_version: str) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for row in store.rows(MODULE):
        raw.append({
            "规则编号": row.get("规则编号"),
            "校验字段": row.get("校验字段"),
            "期望格式": row.get("期望格式"),
            "空值阈值": row.get("空值阈值"),
            "重复判定": row.get("重复判定"),
            "异常策略": row.get("异常策略"),
            "阻断级别": row.get("阻断级别"),
            "规则版本": row.get("规则版本") or fallback_version,
        })
    return _normalize_rules(raw, fallback_version)


def _format_matches(value: str, fmt: str) -> bool:
    if fmt in ("文本", "字符串", "任意", ""):
        return True
    pattern = FORMAT_PATTERNS.get(fmt)
    if pattern is None and fmt.startswith(("^", "/")):
        pattern = fmt.rstrip("/").lstrip("/")
    if pattern is None:
        return True  # 未登记的格式口径不做误判，规则配置问题走缺陷而非误杀样本
    try:
        return re.match(pattern, value) is not None
    except re.error:
        return True


class DisposalService:
    """处置包生成、追加证据、冲突替换与下游读取的统一入口。"""

    # ---------- 对外读取 ----------
    def list_packages(self, batch: str | None = None) -> list[dict[str, Any]]:
        packages = repo.all_packages()
        if batch:
            packages = [p for p in packages if p["校验批次"] == batch]
        packages.sort(key=lambda p: (p["校验批次"], p["创建时间"]), reverse=True)
        return packages

    def list_batches(self) -> list[str]:
        return repo.list_batches()

    def get_package(self, package_no: str) -> dict[str, Any] | None:
        return repo.find_by_no(package_no)

    def get_active_package(self, batch: str) -> dict[str, Any] | None:
        return repo.active_for_batch(batch)

    def list_defects(self, batch: str | None = None) -> list[dict[str, Any]]:
        return repo.list_defects(batch)

    def resolve_defect(self, defect_id: int) -> dict[str, Any] | None:
        return repo.resolve_defect(defect_id)

    def audit(self) -> list[dict[str, Any]]:
        return repo.audit()

    # ---------- 核心提交 ----------
    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch = str(payload.get("校验批次") or "").strip()
        if not batch:
            return _fail("缺少校验批次，无法生成校验异常数据处置包", ERR_NO_BATCH,
                         defect_type="处置包报错")

        version = str(payload.get("规则版本") or "").strip()
        raw_samples = payload.get("样本") or []
        if not isinstance(raw_samples, list) or not raw_samples:
            return _fail("缺少待校验样本，处置包没有可归档的异常证据", ERR_NO_SAMPLE,
                         batch=batch, defect_type="处置包报错")

        # 规则依据：优先用提交载荷里的规则，缺省回退到采集校验模块已登记的规则。
        raw_rules = payload.get("规则")
        try:
            if isinstance(raw_rules, list) and raw_rules:
                rules = _normalize_rules(raw_rules, version)
            else:
                rules = _load_store_rules(version)
        except Exception as exc:  # 防御：规则解析异常不能留下半包
            return _fail(f"校验规则解析失败：{exc}", ERR_PACKAGE_BROKEN, batch=batch,
                         defect_type="处置包残留")

        if not version:
            version = rules[0]["规则版本"] if rules else ""
        if not rules:
            return _fail("缺少规则依据：未提供有效校验规则或规则版本，样本不能进入待处置范围",
                         ERR_NO_RULE, batch=batch, defect_type="规则依据缺失")
        if any(r["阻断级别"] not in LEVELS for r in rules):
            return _fail("校验规则阻断级别非法，仅允许 阻断/告警/提示", ERR_RULE_INVALID,
                         batch=batch, defect_type="规则依据缺失")

        # 样本规整：{样本编号, 字段: {...}}，没有规则覆盖的样本不进入待处置范围。
        covered_fields = {r["校验字段"] for r in rules}
        samples: list[dict[str, Any]] = []
        excluded = 0
        for index, item in enumerate(raw_samples):
            if not isinstance(item, dict):
                excluded += 1
                continue
            sample_no = str(item.get("样本编号") or f"S{index + 1:04d}")
            fields = item.get("字段") if isinstance(item.get("字段"), dict) else {
                k: v for k, v in item.items() if k != "样本编号"
            }
            if not (set(fields) & covered_fields):
                excluded += 1
                repo.add_defect(
                    "规则依据缺失",
                    f"样本 {sample_no} 的字段均无校验规则覆盖，已排除出待处置范围",
                    batch=batch, code=ERR_NO_RULE)
                continue
            samples.append({"样本编号": sample_no, "字段": fields})
        if not samples:
            return _fail("全部样本缺少规则依据，不能生成校验异常数据处置包", ERR_NO_RULE,
                         batch=batch, defect_type="规则依据缺失")

        try:
            findings = self._evaluate(samples, rules)
        except Exception as exc:
            return _fail(f"异常样本处置结果计算失败：{exc}", ERR_PACKAGE_BROKEN, batch=batch,
                         defect_type="处置包残留")

        # 互斥提交：并发/连续提交同一批次时，锁内只允许一个生效版本。
        with repo.lock():
            active = repo.active_for_batch(batch)
            if active is not None and active["规则版本"] == version:
                package = self._append_evidence(active, findings, rules)
                return {"ok": True, "code": "0",
                        "message": "重复校验已追加新的异常证据，沿用原处置编号",
                        "entry": package, "appended": True}

            package = self._build_package(batch, version, rules, samples, findings)
            if active is not None:
                conflicts = self._conflicts(active, package)
                reason = "新规则版本产生不同阻断结论，旧处置包停止生效" if conflicts \
                    else "规则版本更新，旧处置包归档保留"
                repo.mark_superseded(active, reason)
                package["上一版本处置编号"] = active["处置编号"]
                if conflicts:
                    repo.add_defect(
                        "阻断结论冲突",
                        f"批次 {batch} 样本 {('、'.join(sorted(set(conflicts))))} 在新旧规则下阻断结论不一致，"
                        f"下游仅读取 {package['处置编号']}（{version}）",
                        batch=batch, package_no=package["处置编号"], code="E4009")
            repo.save_package(package)

        return {"ok": True, "code": "0",
                "message": "校验异常数据处置包已生成并与规则版本同时保存",
                "entry": package, "appended": False}

    # ---------- 规则计算 ----------
    def _evaluate(self, samples: list[dict[str, Any]],
                  rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        rules_by_field: dict[str, list[dict[str, Any]]] = {}
        for rule in rules:
            rules_by_field.setdefault(rule["校验字段"], []).append(rule)

        # 重复值预统计：只统计启用了重复校验的字段。
        dup_rules = {r["校验字段"]: r for r in rules if r["重复校验"]}
        value_owners: dict[str, dict[str, list[str]]] = {f: {} for f in dup_rules}
        for sample in samples:
            for field in dup_rules:
                raw = sample["字段"].get(field)
                if not _is_blank(raw):
                    value_owners[field].setdefault(str(raw), []).append(sample["样本编号"])

        for sample in samples:
            sample_no = sample["样本编号"]
            for field, field_rules in rules_by_field.items():
                if field not in sample["字段"]:
                    continue
                raw = sample["字段"].get(field)
                rule = field_rules[0]
                if _is_blank(raw):
                    if rule["空值校验"]:
                        findings.append(self._finding(sample_no, field, "空值", rule, ""))
                    continue
                text = str(raw)
                if not _format_matches(text, rule["期望格式"]):
                    findings.append(self._finding(sample_no, field, "格式", rule, text))
            for field, rule in dup_rules.items():
                raw = sample["字段"].get(field)
                if _is_blank(raw):
                    continue
                owners = value_owners[field].get(str(raw), [])
                if len(owners) > 1:
                    findings.append(self._finding(sample_no, field, "重复", rule, str(raw),
                                                  extra={"重复样本": "、".join(owners)}))
        return findings

    @staticmethod
    def _finding(sample_no: str, field: str, evidence_type: str, rule: dict[str, Any],
                 actual: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        finding = {
            "样本编号": sample_no,
            "失败字段": field,
            "异常类型": evidence_type,
            "阻断级别": rule["阻断级别"],
            "规则编号": rule["规则编号"],
            "规则版本": rule["规则版本"],
            "实际值": actual,
        }
        if extra:
            finding.update(extra)
        return finding

    # ---------- 组装与追加 ----------
    def _build_package(self, batch: str, version: str, rules: list[dict[str, Any]],
                       samples: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
        package_no = repo.next_package_no()
        detail: list[dict[str, Any]] = []
        for finding in findings:
            evidence_no = repo.next_evidence_no()
            detail.append({
                "证据编号": evidence_no,
                "样本编号": finding["样本编号"],
                "失败字段": finding["失败字段"],
                "异常类型": finding["异常类型"],
                "阻断级别": finding["阻断级别"],
                "规则编号": finding["规则编号"],
                "规则版本": finding["规则版本"],
                "处置状态": "待处置",
                "实际值": finding.get("实际值", ""),
                "重复样本": finding.get("重复样本", ""),
                "证据列表": [{"校验时间": _now(), "实际值": finding.get("实际值", ""),
                            "规则版本": version}],
            })
        return self._assemble(package_no, batch, version, rules, samples, detail, 1)

    def _append_evidence(self, package: dict[str, Any], findings: list[dict[str, Any]],
                         rules: list[dict[str, Any]]) -> dict[str, Any]:
        index = {
            (d["样本编号"], d["失败字段"], d["异常类型"]): d for d in package["明细"]
        }
        added = 0
        for finding in findings:
            key = (finding["样本编号"], finding["失败字段"], finding["异常类型"])
            stamp = {"校验时间": _now(), "实际值": finding.get("实际值", ""),
                     "规则版本": finding["规则版本"]}
            if key in index:
                # 同一异常重复出现：只追加证据，不新发处置/证据编号。
                index[key]["证据列表"].append(stamp)
                index[key]["实际值"] = finding.get("实际值", index[key]["实际值"])
            else:
                evidence = {
                    "证据编号": repo.next_evidence_no(),
                    "样本编号": finding["样本编号"],
                    "失败字段": finding["失败字段"],
                    "异常类型": finding["异常类型"],
                    "阻断级别": finding["阻断级别"],
                    "规则编号": finding["规则编号"],
                    "规则版本": finding["规则版本"],
                    "处置状态": "待处置",
                    "实际值": finding.get("实际值", ""),
                    "重复样本": finding.get("重复样本", ""),
                    "证据列表": [stamp],
                }
                package["明细"].append(evidence)
                index[key] = evidence
                added += 1
        package["提交次数"] += 1
        package["最近更新时间"] = _now()
        package["规则快照"] = copy.deepcopy(rules)
        self._refresh_stats(package)
        package["本次新增证据数"] = added
        return package

    def _assemble(self, package_no: str, batch: str, version: str,
                  rules: list[dict[str, Any]], samples: list[dict[str, Any]],
                  detail: list[dict[str, Any]], submit_count: int) -> dict[str, Any]:
        package: dict[str, Any] = {
            "处置编号": package_no,
            "校验批次": batch,
            "规则版本": version,
            "包状态": "生效",
            "规则快照": copy.deepcopy(rules),
            "明细": detail,
            "样本总数": len(samples),
            "提交次数": submit_count,
            "创建时间": _now(),
            "最近更新时间": _now(),
            "本次新增证据数": len(detail),
        }
        self._refresh_stats(package)
        return package

    @staticmethod
    def _refresh_stats(package: dict[str, Any]) -> None:
        detail = package["明细"]
        abnormal_samples = {d["样本编号"] for d in detail}
        level_stats = {level: 0 for level in LEVELS}
        type_stats = {etype: 0 for etype in EVIDENCE_TYPES}
        for d in detail:
            level_stats[d["阻断级别"]] = level_stats.get(d["阻断级别"], 0) + 1
            type_stats[d["异常类型"]] = type_stats.get(d["异常类型"], 0) + 1
        package["异常样本数"] = len(abnormal_samples)
        package["异常证据数"] = len(detail)
        package["阻断级别统计"] = level_stats
        package["异常类型统计"] = type_stats

    @staticmethod
    def _conflicts(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
        old_idx = {(d["样本编号"], d["失败字段"], d["异常类型"]): d["阻断级别"]
                   for d in old["明细"]}
        conflicts: list[str] = []
        for d in new["明细"]:
            key = (d["样本编号"], d["失败字段"], d["异常类型"])
            if key in old_idx and old_idx[key] != d["阻断级别"]:
                conflicts.append(d["样本编号"])
        return conflicts


service = DisposalService()


# ---------- 演示种子：服务启动后保证页面有一份可查看的完整处置包 ----------
def ensure_seed_package() -> None:
    global _seeded
    with _seed_lock:
        if _seeded or repo.active_for_batch(SEED_BATCH) is not None:
            _seeded = True
            return
        rules = _load_store_rules("RV-DEMO-1")
        if not rules:
            _seeded = True
            return
        samples = [
            {"样本编号": "S1001", "字段": {"商品标题": "机械键盘", "价格": "199.00", "商品链接": "https://demo.example.com/p/1001"}},
            {"样本编号": "S1002", "字段": {"商品标题": "", "价格": "abc", "商品链接": "https://demo.example.com/p/1002"}},
            {"样本编号": "S1003", "字段": {"商品标题": "机械键盘", "价格": "89.90", "商品链接": "not-a-url"}},
            {"样本编号": "S1004", "字段": {"商品标题": "无线鼠标", "价格": "59.90", "商品链接": "https://demo.example.com/p/1004"}},
        ]
        service.submit({"校验批次": SEED_BATCH, "规则版本": "RV-DEMO-1",
                        "规则": rules, "样本": samples})
        _seeded = True
