"""质量指标数据集：读取校验异常数据处置包生成质量指标。

口径保证：异常样本数量与阻断级别直接取自处置包明细，不另做估算，
因此质量报告里的数字必须与处置包明细一致。
"""
from __future__ import annotations

from typing import Any

from app.services.disposal import _now
from app.services.disposal_engine import service as disposal_service


class QualityDatasetService:
    def build_for_package(self, package: dict[str, Any]) -> dict[str, Any]:
        detail = package.get("明细", [])
        abnormal_samples = {d["样本编号"] for d in detail}
        level_stats = dict(package.get("阻断级别统计") or {})
        type_stats = dict(package.get("异常类型统计") or {})
        sample_total = int(package.get("样本总数") or 0)
        abnormal_count = len(abnormal_samples)
        # 阻断率/达标率都以处置包样本数为分母，口径与处置包完全一致。
        block_count = int(level_stats.get("阻断", 0))
        block_rate = round(block_count / sample_total, 4) if sample_total else 0.0
        pass_rate = round((sample_total - abnormal_count) / sample_total, 4) if sample_total else 0.0
        return {
            "数据集编号": f"QMD-{package['处置编号']}",
            "来源处置编号": package["处置编号"],
            "校验批次": package["校验批次"],
            "规则版本": package["规则版本"],
            "包状态": package["包状态"],
            "样本数量": sample_total,
            "异常样本数量": abnormal_count,
            "异常证据数": int(package.get("异常证据数") or len(detail)),
            "阻断级别统计": level_stats,
            "异常类型统计": type_stats,
            "阻断率": block_rate,
            "达标率": pass_rate,
            "明细口径": "异常样本数量=明细去重样本数；阻断级别统计=明细按阻断级别计数",
            "生成时间": _now(),
        }

    def list_datasets(self, batch: str | None = None) -> list[dict[str, Any]]:
        datasets = [self.build_for_package(p)
                    for p in disposal_service.list_packages(batch)]
        # 下游只允许读到一份绑定规则版本的完整生效处置包：已替换包不进入同步清单。
        return [d for d in datasets if d["包状态"] == "生效"]

    def active_dataset(self, batch: str) -> dict[str, Any] | None:
        package = disposal_service.get_active_package(batch)
        if package is None:
            return None
        return self.build_for_package(package)

    def consistency_check(self) -> list[dict[str, Any]]:
        """核对质量指标与处置包明细是否一致，错位即返回差异项（供缺陷清单使用）。"""
        drifts: list[dict[str, Any]] = []
        for package in disposal_service.list_packages():
            dataset = self.build_for_package(package)
            detail = package.get("明细", [])
            expected_samples = len({d["样本编号"] for d in detail})
            expected_block = sum(1 for d in detail if d["阻断级别"] == "阻断")
            if dataset["异常样本数量"] != expected_samples:
                drifts.append({"处置编号": package["处置编号"], "校验批次": package["校验批次"],
                               "问题": f"异常样本数量不一致：指标 {dataset['异常样本数量']} / 明细 {expected_samples}"})
            if dataset["阻断级别统计"].get("阻断", 0) != expected_block:
                drifts.append({"处置编号": package["处置编号"], "校验批次": package["校验批次"],
                               "问题": "阻断级别统计与处置包明细不一致"})
        return drifts


dataset_service = QualityDatasetService()
