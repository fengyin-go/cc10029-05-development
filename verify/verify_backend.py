"""校验异常数据处置包后端验收脚本（标准库实现，python -I 运行）。

覆盖：
1. 种子处置包生成 + 批次/明细/规则版本绑定
2. 空值、格式、重复三类异常按规则计算
3. 缺少规则依据的样本不进入待处置范围并落缺陷
4. 同批次同规则版本重复提交：只追加证据、沿用处置编号
5. 同批次连续/并发提交：只保留一个生效版本
6. 新旧规则阻断结论冲突：旧包标记已替换 + 冲突缺陷
7. 质量报告数据集与处置包明细口径一致
8. 处置包丢失/错位进入缺陷清单与巡检
"""
import json
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = "http://127.0.0.1:8000"
failures = []


def call(method, path, body=None, expect_status=200):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode())
        if exc.code != expect_status:
            raise
        return exc.code, payload


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        failures.append(name)


# ---------- 1. 种子数据 ----------
_, batches = call("GET", "/api/validate/disposal/batches")
check("种子批次可查看", "BATCH-DEMO-01" in batches["items"], str(batches))
_, pkgs = call("GET", "/api/validate/disposal/packages?batch=BATCH-DEMO-01")
seed_pkg = pkgs["items"][0]
check("种子包绑定规则版本", seed_pkg["规则版本"] == "RV-DEMO-1")
check("种子包含三类异常证据",
      set(seed_pkg["异常类型统计"]) >= {"空值", "格式", "重复"},
      str(seed_pkg["异常类型统计"]))
# S1002 空值+格式，S1003 重复+格式
check("种子包异常样本数与明细一致",
      seed_pkg["异常样本数"] == len({d["样本编号"] for d in seed_pkg["明细"]}),
      f"样本数={seed_pkg['异常样本数']} 明细={len(seed_pkg['明细'])}")

# ---------- 2/3. 规则计算 + 缺少规则依据 ----------
batch = "BATCH-VERIFY-01"
rules = [
    {"规则编号": "R1", "校验字段": "name", "期望格式": "文本", "空值阈值": "启用",
     "重复判定": "启用", "阻断级别": "阻断", "规则版本": "v1"},
    {"规则编号": "R2", "校验字段": "email", "期望格式": "邮箱", "空值阈值": "启用",
     "重复判定": "不校验", "阻断级别": "告警", "规则版本": "v1"},
]
samples = [
    {"样本编号": "A1", "字段": {"name": "alpha", "email": "a@x.com"}},
    {"样本编号": "A2", "字段": {"name": "", "email": "bad-mail"}},          # 空值+格式
    {"样本编号": "A3", "字段": {"name": "alpha", "email": "c@x.com"}},      # 重复
    {"样本编号": "A4", "字段": {"unknown_field": "x"}},                     # 无规则依据
]
_, r = call("POST", "/api/validate/disposal/packages",
            {"校验批次": batch, "规则版本": "v1", "规则": rules, "样本": samples})
check("首次提交成功", r["ok"], r.get("message", ""))
pkg1 = r["entry"]
check("处置编号已发放", pkg1["处置编号"].startswith("DSP-"))
check("无依据样本未入待处置", "A4" not in {d["样本编号"] for d in pkg1["明细"]})
check("空值异常被识别", any(d["样本编号"] == "A2" and d["异常类型"] == "空值" for d in pkg1["明细"]))
check("格式异常被识别", any(d["样本编号"] == "A2" and d["异常类型"] == "格式" for d in pkg1["明细"]))
check("重复异常被识别", any(d["样本编号"] == "A3" and d["异常类型"] == "重复" for d in pkg1["明细"]))
check("阻断级别来自规则",
      all(d["阻断级别"] in ("阻断", "告警") and d["规则版本"] == "v1" for d in pkg1["明细"]))
_, defects = call("GET", f"/api/validate/disposal/defects?batch={batch}")
check("无依据样本进入缺陷清单", any(d["缺陷类型"] == "规则依据缺失" for d in defects["items"]))

# 缺少规则依据的提交
_, r2 = call("POST", "/api/validate/disposal/packages",
             {"校验批次": "BATCH-NO-RULE", "规则版本": "v1", "规则": [],
              "样本": [{"样本编号": "X1", "字段": {"name": "x"}}]})
check("无规则依据拒绝生成", (not r2["ok"]) and r2["code"] == "E4002", r2.get("message", ""))
# 缺少批次
_, r3 = call("POST", "/api/validate/disposal/packages",
             {"规则版本": "v1", "规则": rules, "样本": samples})
check("缺批次报错", (not r3["ok"]) and r3["code"] == "E4001")

# ---------- 4. 重复提交只追加证据 ----------
_, r4 = call("POST", "/api/validate/disposal/packages",
             {"校验批次": batch, "规则版本": "v1", "规则": rules, "样本": [
                 {"样本编号": "A2", "字段": {"name": "", "email": "still-bad"}},
                 {"样本编号": "A5", "字段": {"name": "", "email": "e@x.com"}},
             ]})
check("重复提交返回追加标记", r4["ok"] and r4.get("appended") is True)
pkg2 = r4["entry"]
check("处置编号不变", pkg2["处置编号"] == pkg1["处置编号"])
check("没有复制整份包", pkg2["提交次数"] == 2)
a2_null = [d for d in pkg2["明细"] if d["样本编号"] == "A2" and d["异常类型"] == "空值"][0]
check("同异常只追加证据不新发编号", len(a2_null["证据列表"]) == 2)
check("新异常追加进同一包", any(d["样本编号"] == "A5" for d in pkg2["明细"]))

# ---------- 5. 连续/并发提交只保留一个生效版本 ----------
def submit():
    return call("POST", "/api/validate/disposal/packages",
                {"校验批次": batch, "规则版本": "v1", "规则": rules, "样本": samples})
with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(lambda _: submit(), range(2)))
check("并发两次提交均成功", all(x[1]["ok"] for x in results))
_, pkgs_now = call("GET", f"/api/validate/disposal/packages?batch={batch}")
active = [p for p in pkgs_now["items"] if p["包状态"] == "生效"]
check("并发提交只保留一个生效版本", len(active) == 1, f"生效包数={len(active)}")
check("并发沿用同一处置编号", active[0]["处置编号"] == pkg1["处置编号"])

# ---------- 6. 新旧规则阻断冲突 ----------
rules_v2 = [dict(r, 规则版本="v2", 阻断级别=("告警" if r["阻断级别"] == "阻断" else "提示"))
            for r in rules]
_, r6 = call("POST", "/api/validate/disposal/packages",
             {"校验批次": batch, "规则版本": "v2", "规则": rules_v2, "样本": samples})
check("新版本提交成功", r6["ok"])
pkg3 = r6["entry"]
check("新版本新处置编号", pkg3["处置编号"] != pkg1["处置编号"])
check("新包为唯一生效版本", pkg3["包状态"] == "生效")
_, pkgs_after = call("GET", f"/api/validate/disposal/packages?batch={batch}")
old = [p for p in pkgs_after["items"] if p["处置编号"] == pkg1["处置编号"]][0]
check("旧包标记已替换", old["包状态"] == "已替换")
active2 = [p for p in pkgs_after["items"] if p["包状态"] == "生效"]
check("批次下唯一生效包", len(active2) == 1 and active2[0]["处置编号"] == pkg3["处置编号"])
_, defects6 = call("GET", f"/api/validate/disposal/defects?batch={batch}")
check("阻断冲突进入缺陷清单", any(d["缺陷类型"] == "阻断结论冲突" for d in defects6["items"]))

# ---------- 7. 质量报告数据集 ----------
_, ds = call("GET", f"/api/quality/datasets/{batch}")
check("下游只读到一份绑定版本的包", ds["规则版本"] == "v2" and ds["来源处置编号"] == pkg3["处置编号"])
check("数据集异常样本数与明细一致",
      ds["异常样本数量"] == len({d["样本编号"] for d in pkg3["明细"]}),
      f"数据集={ds['异常样本数量']} 包={pkg3['异常样本数']}")
check("数据集阻断级别与明细一致",
      ds["阻断级别统计"] == pkg3["阻断级别统计"],
      f"{ds['阻断级别统计']} vs {pkg3['阻断级别统计']}")
_, all_ds = call("GET", "/api/quality/datasets")
check("数据集清单只含生效包", all(p["包状态"] == "生效" for p in all_ds["items"]))
_, sync = call("POST", "/api/quality/datasets/sync")
check("同步口径校验通过", sync["ok"], sync.get("message", ""))

# ---------- 8. 丢失/错位/巡检 ----------
status, missing = call("GET", "/api/validate/disposal/packages/DSP-NOT-EXIST", expect_status=404)
check("丢失包返回404错误码", status == 404 and "E4041" in missing["detail"])
_, defects8 = call("GET", "/api/validate/disposal/defects")
check("丢失事件进入缺陷清单", any(d["错误码"] == "E4041" for d in defects8["items"]))
_, audit = call("POST", "/api/validate/disposal/audit")
check("巡检可执行", audit["ok"])

# ---------- 既有契约保持 ----------
_, legacy = call("GET", "/api/validate")
check("既有列表契约保留", "items" in legacy and len(legacy["items"]) == 3)
_, run = call("POST", "/api/validate/run", {"values": {"样本范围": "3"}})
check("既有运行分析契约保留", len(run["items"]) == 3)
_, qlegacy = call("GET", "/api/quality")
check("质量报告既有契约保留", "items" in qlegacy)

print("\n==== 结果：%d 失败 ====" % len(failures))
if failures:
    print("\n".join(failures))
    sys.exit(1)
