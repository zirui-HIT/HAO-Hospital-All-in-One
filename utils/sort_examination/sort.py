#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
按照 AND/OR 语义修正分类：
- RequiredEquipmentList: AND（全部必需）
- RequiredRoomTags: OR（任一满足即可）

分类规则（对每个房间 tag 独立判类，最后取候选中的“最低 rank”）：
a office_no_equipment
b office_simple_equipment
c observation
d lab（仅当 room tag 以 'lab' 结尾）
e imaging（room/workspace/unit 的前缀/后缀，或命中 IMAGING_KEYS）
注意：不再使用 LabTestingExaminationRef 作为实验室判定条件。

打分（按疾病去重）：
Score(exam) = Σ_{d ∈ 覆盖到的疾病}  freq(代表症状_s*_d)
其中 freq(s) = 症状 s 出现在多少个疾病中。
每个疾病仅选一个“代表症状”（在该疾病中且能被该检测检出的候选里，取 freq 最大的那一个），
从而自然实现“同一疾病多症状 → 只贡献 1 次”的去重。
"""

import os
import glob
import json
import argparse
import itertools
import xml.etree.ElementTree as ET
from collections import defaultdict, Counter

# ---------- 配置 ----------
TIE_ONLY_WITHIN_CATEGORY = True
EQUIP_TAGS_IGNORED = {"sit_exam", "clean_hands"}   # 不计为设备
OBS_ROOM_KEYS = {"observation", "trauma", "ward", "hdu", "icu"}
IMAGING_KEYS = {
    "xray", "radiology", "ct", "mri", "usg", "ultrasound",
    "echo", "angiography", "endoscopy", "cardio", "neuro", "orthoped", "urology", "dermatology"
}
OFFICE_NO_EQUIP_TAG = "examinations_no_equipment"
OFFICE_BASIC_EQUIP_TAG = "examinations_basic_equipment"

# 用于“排序/打分”的固定先后：a→b→c→d→e
CATEGORY_ORDER = {
    "office_no_equipment": 0,     # a
    "office_simple_equipment": 1,  # b
    "observation": 2,             # c
    "lab": 3,                     # d
    "imaging": 4,                 # e
}
PRIORITY_FLOOR = 25


def iter_xml_files(root_dir: str):
    for p in glob.glob(os.path.join(root_dir, "**", "*.xml"), recursive=True):
        yield p


def tags_under(elem, path):
    cur = elem
    for name in path:
        if cur is None:
            return []
        cur = cur.find(name)
    if cur is None:
        return []
    return [t.text.strip().lower() for t in cur.findall("Tag") if t.text]


def equipment_tags(elem):
    proc = elem.find("Procedure")
    if proc is None:
        return []
    req = proc.find("RequiredEquipmentList")
    if req is None:
        return []
    out = []
    for relem in req.findall("RequiredEquipment"):
        t = relem.find("Tag")
        if t is not None and t.text:
            out.append(t.text.strip().lower())
    return out


def _is_lab_room_tag(tag: str) -> bool:
    # “只要以 lab 结尾就是实验室”
    return tag.endswith("lab")


def _is_imaging_room_tag(tag: str) -> bool:
    # “以 room/workspace/unit 开头或结尾 一律算专科单元/影像”
    if tag.startswith(("room", "workspace", "unit")) or tag.endswith(("room", "workspace", "unit")):
        return True
    return any(k in tag for k in IMAGING_KEYS)


def _has_effective_equipment(exam_elem):
    equips = set(equipment_tags(exam_elem))
    return any(t not in EQUIP_TAGS_IGNORED for t in equips)

# 修改：将房间 tag -> 类别 的逻辑，支持识别 office，并根据设备确定 no_equipment / simple_equipment


def _room_tag_to_category(tag: str, has_effective_equips: bool) -> str | None:
    tag_l = tag.lower()

    # 0) 显式办公室标准标签（必须优先判断并用上）
    if tag_l == OFFICE_NO_EQUIP_TAG:
        return "office_no_equipment"
    if tag_l == OFFICE_BASIC_EQUIP_TAG:
        return "office_simple_equipment"

    # 1) 实验室：只要 tag 以 'lab' 结尾
    if tag_l.endswith("lab"):
        return "lab"

    # 2) 办公室：凡包含 'office'（如 *_office / emergency_doctors_office）
    if "office" in tag_l:
        return "office_simple_equipment" if has_effective_equips else "office_no_equipment"

    # 3) 观察/病房
    if any(k in tag_l for k in OBS_ROOM_KEYS):  # observation / trauma / ward / hdu / icu
        return "observation"

    # 4) 影像/专科：以 room/workspace/unit 开头或结尾，或命中 IMAGING_KEYS
    if tag_l.startswith(("room", "workspace", "unit")) or tag_l.endswith(("room", "workspace", "unit")):
        return "imaging"
    if any(k in tag_l for k in IMAGING_KEYS):
        return "imaging"

    # 5) 其它未知房间 tag：不直接给类别，由上层兜底为 office_*
    return None


# 修改：分类函数——加入 LabTestingExaminationRef 的“强制实验室”与新的 office 识别


def classify_exam(exam_elem):
    """
    AND/OR 语义下的分类：
    - RequiredRoomTags: OR（逐 tag 判类，汇总候选后取 a→b→c→d→e 中 rank 最小）
    - RequiredEquipmentList: AND（有任一有效设备则不能归为无设备）
    - 强制规则：若存在 <LabTestingExaminationRef> 则直接归为 'lab'
    """
    # --- 强制：存在 LabTestingExaminationRef → 实验室 ---
    lab_ref = exam_elem.find("LabTestingExaminationRef")
    if lab_ref is not None and (lab_ref.text or "").strip() != "":
        return "lab", CATEGORY_ORDER["lab"]

    room_tags = set(tags_under(exam_elem, ["Procedure", "RequiredRoomTags"]))
    equips = set(equipment_tags(exam_elem))
    has_effective_equips = any(t not in EQUIP_TAGS_IGNORED for t in equips)

    # 房间 OR：汇总所有候选类别
    candidates = set()
    for rt in room_tags:
        cat = _room_tag_to_category(rt, has_effective_equips)
        if cat:
            candidates.add(cat)

    # AND/OR 一致性：若候选包含 office_no_equipment 但实际有设备，升级为 simple_equipment
    if "office_no_equipment" in candidates and has_effective_equips:
        candidates.discard("office_no_equipment")
        candidates.add("office_simple_equipment")

    # 兜底：没有识别出任何房间类别，则按设备落到办公室
    if not candidates:
        candidates.add(
            "office_simple_equipment" if has_effective_equips else "office_no_equipment")

    # 取 a→b→c→d→e 中 rank 最小者
    chosen = min(candidates, key=lambda c: CATEGORY_ORDER[c])
    return chosen, CATEGORY_ORDER[chosen]


def parse_all(root_dir):
    examinations = defaultdict(list)    # ex_id -> [(file_path, element)]
    symptoms_refs = {}                  # sym_id -> set(exam_id)
    used_symptoms = set()               # 被疾病实际使用到的症状
    disease_to_syms = defaultdict(set)  # disease_id -> {sym...}
    sym_to_diseases = defaultdict(set)  # sym_id -> {disease_id...}
    file_trees = {}

    for path in iter_xml_files(root_dir):
        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except ET.ParseError:
            continue
        file_trees[path] = (tree, root)

        for elem in root.iter():
            tag = elem.tag.strip()
            if tag == "GameDBExamination":
                ex_id = elem.get("ID", "").strip()
                if ex_id:
                    examinations[ex_id].append((path, elem))

            elif tag == "GameDBSymptom":
                s_id = elem.get("ID", "").strip()
                if not s_id:
                    continue
                exs = set()
                exs_node = elem.find("Examinations")
                if exs_node is not None:
                    for r in exs_node.findall("ExaminationRef"):
                        if r.text and r.text.strip():
                            exs.add(r.text.strip())
                symptoms_refs[s_id] = exs

            elif tag == "GameDBMedicalCondition":
                d_id = elem.get("ID", "").strip()
                syms = elem.find("Symptoms")
                if d_id and syms is not None:
                    for rule in syms.findall("GameDBSymptomRules"):
                        sref = rule.find("GameDBSymptomRef")
                        if sref is not None and sref.text and sref.text.strip():
                            s_id = sref.text.strip()
                            used_symptoms.add(s_id)
                            disease_to_syms[d_id].add(s_id)
                            sym_to_diseases[s_id].add(d_id)

    return examinations, symptoms_refs, used_symptoms, disease_to_syms, sym_to_diseases, file_trees


def build_scores(examinations, symptoms_refs, used_symptoms, disease_to_syms, sym_to_diseases):
    """
    计算每个 exam 的：
      - 最优分类（若同一 ID 多次出现，取“类别 rank 最小”的那个定义）
      - score（按疾病去重；Σ 每个疾病代表症状的跨病出现次数）
      - disease_count（覆盖到的去重疾病数）
    """
    # exam -> set(被疾病实际使用且引用此 exam 的症状)
    exam_to_syms = defaultdict(set)
    for s_id in used_symptoms:
        for ex_id in symptoms_refs.get(s_id, set()):
            exam_to_syms[ex_id].add(s_id)

    symptom_global_freq = {s: len(dset) for s, dset in sym_to_diseases.items()}

    exam_meta = {}
    for ex_id, placements in examinations.items():
        # -------- ① 多定义取最高优先级（rank 最小）的分类 ----------
        best_cat, best_rank = None, float("inf")
        for _, elem in placements:
            cat_i, rank_i = classify_exam(elem)
            if rank_i < best_rank:
                best_cat, best_rank = cat_i, rank_i

        # -------- ② 计算分数（按疾病去重） ----------
        syms_for_exam = exam_to_syms.get(ex_id, set())
        if not syms_for_exam:
            exam_meta[ex_id] = {
                "category": best_cat,
                "cat_rank": best_rank,
                "score": 0,
                "disease_count": 0
            }
            continue

        covered_diseases = set()
        sum_freq = 0
        for d_id, d_syms in disease_to_syms.items():
            candidates = syms_for_exam.intersection(d_syms)
            if not candidates:
                continue
            # 取该疾病下与此检测相关的候选中“跨病出现次数”最大的症状作为代表
            rep = max(candidates, key=lambda s: symptom_global_freq.get(s, 0))
            sum_freq += symptom_global_freq.get(rep, 0)
            covered_diseases.add(d_id)

        exam_meta[ex_id] = {
            "category": best_cat,
            "cat_rank": best_rank,
            "score": sum_freq,                      # Σ_d freq(s*_d)
            "disease_count": len(covered_diseases)  # 疾病级去重
        }
    return exam_meta


def compute_priorities(exam_meta):
    """排序键：(cat_rank ASC, score DESC, ex_id ASC)。同类内同分并列。"""
    items = [(ex_id, m["cat_rank"], m["score"])
             for ex_id, m in exam_meta.items()]
    items.sort(key=lambda x: (x[1], -x[2], x[0]))

    key_fn = (lambda x: (x[1], x[2])) if TIE_ONLY_WITHIN_CATEGORY else (
        lambda x: (x[2],))
    groups = [list(g) for _, g in itertools.groupby(items, key=key_fn)]
    highest = PRIORITY_FLOOR + (len(groups) - 1)

    ex_priority = {}
    for idx, grp in enumerate(groups):
        score = highest - idx
        for ex_id, _, _ in grp:
            ex_priority[ex_id] = score
    return ex_priority, items


def ensure_priority_node(exam_elem):
    proc = exam_elem.find("Procedure")
    if proc is None:
        proc = ET.SubElement(exam_elem, "Procedure")
    pr = proc.find("Priority")
    if pr is None:
        pr = ET.SubElement(proc, "Priority")
    return pr


def write_back(examinations, ex_priority, file_trees):
    for ex_id, placements in examinations.items():
        if ex_id not in ex_priority:
            continue
        val = str(ex_priority[ex_id])
        for path, elem in placements:
            pr = ensure_priority_node(elem)
            pr.text = val

    touched = 0
    for path, (tree, _) in file_trees.items():
        try:
            # if os.path.exists(path):
            #     bak = path + ".bak"
            #     if not os.path.exists(bak):
            #         with open(path, "rb") as fsrc, open(bak, "wb") as fdst:
            #             fdst.write(fsrc.read())
            tree.write(path, encoding="utf-8", xml_declaration=False)
            touched += 1
        except Exception as e:
            print(f"[ERROR] 写回失败 {path}: {e}")
    print(f"写回完成，共处理 {touched} 个 XML 文件。")


def main():
    args = argparse.ArgumentParser(
        description="Process examination XML files.")
    args.add_argument("--input", type=str, required=True,
                      help="Input directory")
    args.add_argument("--output", type=str, required=True,
                      help="Output directory")
    args.add_argument("--examination_map", type=str, required=True,
                      help="Examination map JSON file")
    args = args.parse_args()

    root_dir = args.input
    exams, sym_refs, used_syms, disease_to_syms, sym_to_diseases, file_trees = parse_all(
        root_dir)
    print(
        f"解析：检测 {len(exams)}，症状定义 {len(sym_refs)}，疾病使用症状 {len(used_syms)}，文件 {len(file_trees)}")

    exam_meta = build_scores(exams, sym_refs, used_syms,
                             disease_to_syms, sym_to_diseases)
    ex_priority, ordered = compute_priorities(exam_meta)

    # 输出 JSON（含 score 与 disease_count）
    dump_json = args.output
    os.makedirs(os.path.dirname(dump_json), exist_ok=True)
    result = [{
        "examination_id": ex_id,
        "priority": ex_priority[ex_id],
        "category": exam_meta[ex_id]["category"],
        "score": exam_meta[ex_id]["score"],
        "disease_count": exam_meta[ex_id]["disease_count"],
    } for ex_id in ex_priority.keys()]
    result.sort(key=lambda x: (x["priority"],
                x["examination_id"]), reverse=True)
    with open(args.examination_map, 'r', encoding='utf-8') as f:
        exam_map = json.load(f)
        for item in result:
            item["name"] = exam_map.get(
                item["examination_id"], item["examination_id"])
    with open(dump_json, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=4)

    # 如需写回 XML，取消下一行注释
    write_back(exams, ex_priority, file_trees)

    # 预览 Top-10
    print("\nTop-10 预览：rank\tPriority\texam_id\tcategory\tscore\tdiseases")
    for i, (ex_id, cat_rank, _) in enumerate(ordered[:10], 1):
        cat = next(k for k, v in CATEGORY_ORDER.items() if v == cat_rank)
        print(
            f"{i}\t{ex_priority[ex_id]}\t{ex_id}\t{cat}\t{exam_meta[ex_id]['score']}\t{exam_meta[ex_id]['disease_count']}")
    print(f"\nJSON 已写入：{dump_json}\n完成。")


if __name__ == "__main__":
    main()
