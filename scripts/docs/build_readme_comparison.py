"""Render README charts from a small, auditable historical result extract.

The default reads committed evidence; it does not rerun the simulator. To rebuild
the extract, supply the original (Git-external) JSON with --extract-source.
Only rendering requires matplotlib. --verify-only uses the standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/20261115/online-allocation-comparison.json"
IMAGES = ROOT / "docs/images/readme"
SOURCE_PATH = "simulation_results/online_allocation_validation_20261013.json"
SOURCE_SHA = "0de319b268d8482f465601089d59edda2211ed96839b81d7bde66f1c40088ab3"
RULES = ("R0", "R1", "R2", "R3", "C1")
LABELS = {
    "R0": "첫 수용 순서", "R1": "예상 도착 우선", "R2": "자원 경쟁 고려",
    "R3": "공동 배정·제한 열거", "C1": "공동 배정·CP-SAT",
}
EXPECTED = {
    "R0": (108, 2278, 13525), "R1": (108, 2278, 13093),
    "R2": (84, 1847, 11384), "R3": (84, 1862, 11805),
    "C1": (41, 1275, 8895),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def aggregate(rows):
    totals = {}
    for rule in RULES:
        group = [row for row in rows if row["rule"] == rule]
        item = {"scenarios": len(group)}
        for key in ("patients", "unassigned", "not_arrived", "inquiries", "arrival_capped_sum_minutes"):
            item[key] = sum(row[key] for row in group)
        item["arrival_capped_mean_minutes"] = item["arrival_capped_sum_minutes"] / item["patients"]
        totals[rule] = item
    return totals


def extract(path):
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SOURCE_SHA, "Unexpected historical source SHA-256")
    source = json.loads(raw)
    rows = []
    for result in source["results"]:
        scenario = result["scenario"]
        expected_ids = {patient["id"] for patient in scenario["patients"]}
        for branch in result["branches"]:
            rule = branch["metrics"]["policy"]
            outcomes = branch["patient_outcomes"]
            end = branch["metrics"]["observation_end_t"]
            require(len(outcomes) == len(expected_ids), "Patient denominator changed")
            require({patient["id"] for patient in outcomes} == expected_ids, "Patient IDs changed")
            row = {
                "scenario": scenario["id"], "family": scenario["family"],
                "seed": scenario["seed"], "rule": rule, "patients": len(outcomes),
                "observation_end_t_minutes": end,
                "unassigned": sum(patient["first_commit_t"] is None for patient in outcomes),
                "not_arrived": sum(patient["final_arrive_t"] is None for patient in outcomes),
                "inquiries": sum(patient["inquiries"] for patient in outcomes),
                "arrival_capped_sum_minutes": sum(
                    (patient["final_arrive_t"] - patient["occur_t"])
                    if patient["final_arrive_t"] is not None
                    else max(0, end - patient["occur_t"])
                    for patient in outcomes
                ),
            }
            summary = branch["online_summary"]
            for key in ("patients", "unassigned", "not_arrived", "inquiries"):
                require(row[key] == summary[key], f"Summary mismatch: {scenario['id']} {rule} {key}")
            require(math.isclose(row["arrival_capped_sum_minutes"] / len(outcomes),
                                 summary["arrival_capped_mean_minutes"], abs_tol=1e-9),
                    f"Capped arrival mismatch: {scenario['id']} {rule}")
            rows.append(row)
    return {
        "schema_version": 1,
        "kind": "historical_synthetic_online_allocation_chart_extract",
        "extraction_date": "2026-11-15",
        "source": {"path": SOURCE_PATH, "sha256": SOURCE_SHA, "bytes": len(raw),
                   "tracked_in_git": False},
        "scope": {
            "scenarios_per_rule": 108, "patients_per_rule": 433,
            "families": source["families"], "seeds": source["seeds"],
            "common_inquiry_settings": {"k": 3, "wait_minutes": 2, "notify": True},
            "time_unit": "simulated minutes", "rules": LABELS,
        },
        "metric_definitions": {
            "unassigned": "Count of patient_outcomes with first_commit_t=null at observation end.",
            "inquiries": "Sum of patient_outcomes[].inquiries, including repeat inquiries.",
            "arrival_capped_sum_minutes": "Sum(final_arrive_t-occur_t) for arrivals; otherwise max(0, observation_end_t-occur_t).",
            "arrival_capped_mean_minutes": "arrival_capped_sum_minutes / all patients (433 per rule); patient-weighted, not the mean among arrivals or a family-equal mean.",
        },
        "limitations": [
            "Stored synthetic validation results, not a new experiment or clinical/field performance evidence.",
            "Same external environment and inquiry settings; allocations change subsequent capacity and received replies.",
            "C1 is the historical weighted joint-allocation research policy, not current server severity_first.",
            "R3 switches to a sequential fallback when its enumeration limit is exceeded.",
            "Nonarrivals contribute observation-end elapsed time; the mean is not an estimate of when they actually arrive.",
            "In scarce_long_detour, a patient arriving under both R1 and C1 arrives 47 simulated minutes later under C1.",
            "Solver computation time is measured separately, not added to the simulated timeline; current-batch optimality is not whole-episode optimality.",
        ],
        "scenario_metrics": rows,
        "totals": aggregate(rows),
    }


def verify(data):
    require(data["source"]["sha256"] == SOURCE_SHA, "Unexpected source identity")
    rows = data["scenario_metrics"]
    require(len(rows) == 540, "Expected 108 scenarios x 5 rules")
    keys = {(row["scenario"], row["rule"]) for row in rows}
    require(len(keys) == 540, "Duplicate scenario/rule rows")
    reference = {row["scenario"]: row["patients"] for row in rows if row["rule"] == "R0"}
    for rule in RULES:
        current = {row["scenario"]: row["patients"] for row in rows if row["rule"] == rule}
        require(current == reference, f"Different scenario cohort: {rule}")
    totals = aggregate(rows)
    require(totals == data["totals"], "Stored totals do not match scenario rows")
    for rule, total in totals.items():
        require(total["scenarios"] == 108 and total["patients"] == 433, "Unexpected denominator")
        require(tuple(total[key] for key in ("unassigned", "inquiries", "arrival_capped_sum_minutes"))
                == EXPECTED[rule], f"Historical totals changed: {rule}")
    earlier = json.loads((ROOT / "docs/evidence/20261105/resource-workload.json").read_text(encoding="utf-8"))
    prior_rows = earlier["allocation_scenarios"]
    prior = {(row["scenario"], row["policy"]): row for row in prior_rows}
    for row in rows:
        if row["rule"] in ("R1", "C1"):
            old = prior[(row["scenario"], row["rule"])]
            for key in ("patients", "unassigned", "not_arrived", "inquiries"):
                require(row[key] == old[key], f"Earlier evidence mismatch: {row['scenario']} {key}")
    return totals


def render(data, output_dir, font_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    if font_path is None:
        candidate = Path("C:/Windows/Fonts/malgun.ttf")
        require(candidate.exists(), "Supply a Korean font file with --font (for example Noto Sans CJK KR)")
        font_path = candidate
    require(font_path.is_file(), f"Font does not exist: {font_path}")
    font_manager.fontManager.addfont(str(font_path))
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=str(font_path)).get_name(),
                         "axes.unicode_minus": False, "svg.fonttype": "path",
                         "svg.hashsalt": "readme-online-allocation-20261115", "font.size": 12})
    colors = ["#3D8FD6", "#D98A2B", "#9C7CE3", "#2FA88A", "#E0607E"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 7.8), facecolor="white")
    fig.subplots_adjust(left=.055, right=.97, top=.71, bottom=.24, wspace=.25)
    fig.text(.055, .94, "같은 합성 환경에서 5개 배정 규칙 비교", fontsize=25, weight="bold", color="#14263d")
    fig.text(.055, .895, "108개 시나리오 · 규칙당 동일한 433명 · 저장된 검증 결과 재집계", fontsize=13, color="#536476")
    for index, rule in enumerate(RULES):
        x = .055 + index * .187
        fig.text(x, .825, rule, fontsize=14, weight="bold", color=colors[index])
        fig.text(x, .79, LABELS[rule], fontsize=11, color="#334155")

    metrics = (
        ("unassigned", "미배정 환자", "명 / 전체 433명", False),
        ("inquiries", "총 문의 수", "건 · 재문의 포함", False),
        ("arrival_capped_mean_minutes", "도착 제한 경과 평균", "분 · 전체 환자 가중평균", True),
    )
    for ax, (key, title, unit, decimal) in zip(axes, metrics):
        values = [data["totals"][rule][key] for rule in RULES]
        bars = ax.barh(RULES, values, height=.57, color=colors)
        ax.invert_yaxis()
        ax.set_xlim(0, max(values) * 1.25)
        ax.set_title(title, loc="left", fontsize=15, weight="bold", pad=17, color="#14263d")
        ax.set_xlabel(unit, fontsize=11, labelpad=10, color="#536476")
        ax.xaxis.grid(True, color="#e5eaf0", linewidth=.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="both", length=0, labelcolor="#536476", labelsize=11)
        for spine in ax.spines.values():
            spine.set_visible(False)
        for bar, value in zip(bars, values):
            label = f"{value:.2f}" if decimal else f"{value:,}"
            ax.text(value + max(values) * .025, bar.get_y() + bar.get_height() / 2,
                    label, va="center", fontsize=13, color="#14263d", weight="bold")
    fig.text(.055, .145, "시간 지표: 미도착 환자는 관찰 종료까지의 경과시간을 포함합니다. 실제 도착자만의 평균이 아닙니다.",
             fontsize=11, color="#334155")
    fig.text(.055, .105, "한계: C1은 연구 정책이며, 긴 우회 사례에서는 기존 환자 1명의 도착이 R1보다 47분 늦었습니다.",
             fontsize=11, color="#334155")
    fig.text(.055, .052, "출처: 2026-10-13 합성 검증 기록 · 문의 조건 공통(동시 3곳·추가 대기 2분) · 임상·현장 성과 아님",
             fontsize=10, color="#64748b")
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / "algorithm-comparison.png", dpi=180, facecolor="white")
    fig.savefig(output_dir / "algorithm-comparison.svg", facecolor="white", metadata={"Date": None})
    svg_path = output_dir / "algorithm-comparison.svg"
    svg_path.write_text("\n".join(line.rstrip() for line in svg_path.read_text(encoding="utf-8").splitlines()) + "\n",
                        encoding="utf-8")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract-source", type=Path, help="Original historical JSON; verifies pinned SHA and writes compact evidence")
    parser.add_argument("--verify-only", action="store_true", help="Validate stored rows, totals, and prior evidence without matplotlib")
    parser.add_argument("--font", type=Path, help="Korean font file for rendering; Windows defaults to Malgun Gothic")
    parser.add_argument("--output-dir", type=Path, default=IMAGES)
    args = parser.parse_args()
    data = extract(args.extract_source) if args.extract_source else json.loads(EVIDENCE.read_text(encoding="utf-8"))
    totals = verify(data)
    if args.extract_source:
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.verify_only:
        render(data, args.output_dir, args.font)
    print(json.dumps({"status": "pass", "stored_result_only": True, "totals": totals}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
