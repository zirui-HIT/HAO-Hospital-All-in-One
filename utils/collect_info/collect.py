# -*- coding: utf-8 -*-
"""
Extract:
1) All Diagnoses/Symptoms/Examinations/Treatments as {"ID": "中文名"} -> names_zh.json
2) Disease -> Department as {"疾病中文名": "科室中文名(若多项以 / 连接)"} -> disease_department_zh.json

Usage:
  python extract_names_and_depts.py --root /path/to/xmls1 /path/to/xmls2
  # or specify outputs:
  python extract_names_and_depts.py --root /path/to/xmls --out1 names_zh.json --out2 disease_department_zh.json
"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Dict, List, Tuple, Set
import xml.etree.ElementTree as ET

# ---- Target tags ----
TAG_DIAGNOSIS = "GameDBMedicalCondition"
TAG_SYMPTOM = "GameDBSymptom"
TAG_EXAM = "GameDBExamination"
TAG_TREATMENT = "GameDBTreatment"
TAG_DEPARTMENT = "GameDBDepartment"

TARGET_TAGS = {TAG_DIAGNOSIS, TAG_SYMPTOM, TAG_EXAM, TAG_TREATMENT}

# Preferred LocID fields in descending priority
LOC_FIELD_PRIORITY = [
    "NameLocID",
    "AbbreviationLocID",
    "ShortNameLocID",
    "TitleLocID",
    "DisplayNameLocID",
]


def localname(tag: str) -> str:
    """Strip XML namespace from tag name."""
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_xml(path: Path):
    try:
        return ET.parse(str(path))
    except Exception:
        return None


def collect_zh_map(root_dirs: List[Path]) -> Dict[str, str]:
    """
    Collect zh-Hans string table: LocID -> Text (non-empty) from multiple root directories.
    Compatible with:
      <GameDBStringTable>
        <LanguageCode>zh-Hans</LanguageCode>
        <LocalizedStrings>
          <GameDBLocalizedString><LocID>..</LocID><Text>..</Text></GameDBLocalizedString>
        </LocalizedStrings>
      </GameDBStringTable>
    """
    zh: Dict[str, str] = {}
    for root_dir in root_dirs:
        for f in sorted(root_dir.rglob("*.xml"), key=lambda p: str(p).lower()):
            tree = parse_xml(f)
            if tree is None:
                continue
            root = tree.getroot()
            for tbl in root.findall(".//GameDBStringTable"):
                code = (tbl.findtext(".//LanguageCode") or "").strip()
                if code != "zh-Hans":
                    continue
                for item in tbl.findall(".//GameDBLocalizedString"):
                    loc = (item.findtext("./LocID") or "").strip()
                    txt = (item.findtext("./Text") or "").strip()
                    if loc and txt:
                        zh[loc] = txt
    return zh


def gather_loc_fields(elem: ET.Element) -> List[Tuple[str, str]]:
    """
    From an entity element, collect all child fields whose tag ends with 'LocID'.
    Return list of (field_name, locid_value).
    """
    pairs: List[Tuple[str, str]] = []
    for node in elem.iter():
        t = localname(node.tag)
        if t.endswith("LocID"):
            v = (node.text or "").strip()
            if v:
                pairs.append((t, v))
    return pairs


def pick_best_name(entity_id: str, loc_fields: List[Tuple[str, str]], zh_map: Dict[str, str]) -> str:
    """
    Choose the best Chinese display text for an entity:
      1) try prioritized LocID fields
      2) fallback: ID, ID_DESC, ID_DESCRIPTION
      3) else try any LocID present on the entity
      4) else return ""
    """

    return zh_map[entity_id] if entity_id in zh_map else ""

    # 1) prioritized fields
    by_field: Dict[str, str] = {}
    for fname, loc in loc_fields:
        if loc in zh_map and zh_map[loc]:
            by_field.setdefault(fname, zh_map[loc])

    for want in LOC_FIELD_PRIORITY:
        if want in by_field:
            return by_field[want]

    # 2) fallback to ID-based keys
    for key in (entity_id, f"{entity_id}_DESC", f"{entity_id}_DESCRIPTION"):
        if key in zh_map and zh_map[key]:
            return zh_map[key]

    # 3) any available LocID on the entity
    for _, loc in loc_fields:
        if loc in zh_map and zh_map[loc]:
            return zh_map[loc]

    # 4) not found
    return ""


def extract_entities_and_departments(root_dirs: List[Path], zh_map: Dict[str, str]):
    """
    Returns:
      names_by_cat: {
        "diagnoses": {id: zh_name},
        "symptoms": {id: zh_name},
        "examinations": {id: zh_name},
        "treatments": {id: zh_name},
      }
      disease_to_dept_ids: {diagnosis_id: set(department_ids)}
      dept_id_to_name: {dept_id: zh_name}
    """
    names_by_cat = {
        "diagnoses": {},
        "symptoms": {},
        "examinations": {},
        "treatments": {},
    }
    disease_to_dept_ids: Dict[str, Set[str]] = {}
    dept_id_to_name: Dict[str, str] = {}

    # 1) scan all files from all root directories for entities & departments
    for root_dir in root_dirs:
        for f in sorted(root_dir.rglob("*.xml"), key=lambda p: str(p).lower()):
            if "utils" in str(f):
                continue

            tree = parse_xml(f)
            if tree is None:
                continue
            root = tree.getroot()

            # Departments
            for elem in root.findall(f".//{TAG_DEPARTMENT}"):
                dep_id = (elem.get("ID") or "").strip()
                if not dep_id:
                    continue
                loc_fields = gather_loc_fields(elem)
                dept_id_to_name[dep_id] = pick_best_name(
                    dep_id, loc_fields, zh_map)

            # Entities & disease->department refs
            for elem in root.iter():
                tag = localname(elem.tag)
                if tag not in TARGET_TAGS:
                    continue
                ent_id = (elem.get("ID") or "").strip()
                if not ent_id:
                    continue

                # Name resolution
                loc_fields = gather_loc_fields(elem)
                zh_name = pick_best_name(ent_id, loc_fields, zh_map)
                if tag == TAG_DIAGNOSIS:
                    names_by_cat["diagnoses"][ent_id] = zh_name
                elif tag == TAG_SYMPTOM:
                    names_by_cat["symptoms"][ent_id] = zh_name
                elif tag == TAG_EXAM:
                    names_by_cat["examinations"][ent_id] = zh_name
                elif tag == TAG_TREATMENT:
                    names_by_cat["treatments"][ent_id] = zh_name

                # Disease -> Department IDs (collect any descendant *DepartmentRef)
                if tag == TAG_DIAGNOSIS:
                    dep_ids: Set[str] = disease_to_dept_ids.get(ent_id, set())
                    for node in elem.iter():
                        t2 = localname(node.tag)
                        if t2.endswith("DepartmentRef"):
                            v = (node.text or "").strip()
                            if v:
                                dep_ids.add(v)
                    disease_to_dept_ids[ent_id] = dep_ids

    return names_by_cat, disease_to_dept_ids, dept_id_to_name


def build_disease_to_dept_zh(names_by_cat, disease_to_dept_ids, dept_id_to_name, zh_map):
    """
    Map '疾病中文名' -> '科室中文名(若多项以 / 连接)'.
    If disease name missing, use disease ID as key text.
    If department name missing, try fallback via zh_map with dep_id or dep_id_DESC/_DESCRIPTION.
    """
    result: Dict[str, str] = {}
    for dis_id, dep_ids in disease_to_dept_ids.items():
        disease_name = names_by_cat["diagnoses"].get(dis_id, "") or dis_id
        dept_names: List[str] = []
        for dep_id in sorted(dep_ids):
            name = dept_id_to_name.get(dep_id, "")
            if not name:
                # fallback via string table
                for key in (dep_id, f"{dep_id}_DESC", f"{dep_id}_DESCRIPTION"):
                    if key in zh_map and zh_map[key]:
                        name = zh_map[key]
                        break
            if name:
                dept_names.append(name)
        # Deduplicate and join
        joined = " / ".join(dict.fromkeys(dept_names)) if dept_names else ""
        result[disease_name] = joined
    return result


def main():
    ap = argparse.ArgumentParser(
        description="Extract names and disease->department mappings (zh-Hans) from one or more directories.")
    ap.add_argument("--root", required=True, type=Path, nargs='+',
                    help="Root folder(s) to scan XML files recursively.")
    ap.add_argument("--out1", type=Path, default=Path("names_zh.json"),
                    help="Output JSON for entities' names.")
    ap.add_argument("--out2", type=Path, default=Path("disease_department_zh.json"),
                    help="Output JSON for disease->department.")
    args = ap.parse_args()

    roots = args.root
    for root in roots:
        if not root.exists():
            raise SystemExit(f"Root not found: {root}")

    print(f"[INFO] Scanning XMLs under: {[str(r.resolve()) for r in roots]}")
    zh_map = collect_zh_map(roots)
    print(f"[INFO] zh-Hans entries: {len(zh_map)}")

    names_by_cat, disease_to_dept_ids, dept_id_to_name = extract_entities_and_departments(
        roots, zh_map)

    # Write names_zh.json (all four categories)
    args.out1.parent.mkdir(parents=True, exist_ok=True)
    names_by_cat: Dict[str, Dict[str, str]]
    names_by_cat = {k: dict(sorted(v.items(), key=lambda item: item[1])) for k, v in names_by_cat.items()}
    with args.out1.open("w", encoding="utf-8") as f:
        json.dump(names_by_cat, f, ensure_ascii=False, indent=4)
    print(f"[DONE] Wrote entity names -> {args.out1}")

    # Build and write disease -> department (zh)
    disease_to_dept_zh = build_disease_to_dept_zh(
        names_by_cat, disease_to_dept_ids, dept_id_to_name, zh_map)
    disease_to_dept_zh = dict(
        sorted(disease_to_dept_zh.items(), key=lambda item: (item[0])))
    args.out2.parent.mkdir(parents=True, exist_ok=True)
    with args.out2.open("w", encoding="utf-8") as f:
        json.dump(disease_to_dept_zh, f, ensure_ascii=False, indent=4)
    print(f"[DONE] Wrote disease->department -> {args.out2}")

    # 检查names_by_cat中，value相同的key有哪些，并打印
    def check_same_suffix(keys: List[str]) -> bool:
        suffix = [k.split('_')[0] for k in keys]
        # 如果suffix中有重复的str，则返回true，否则返回false
        return len(suffix) > len(set(suffix))

    reverse_map: Dict[str, List[str]] = defaultdict(list)
    for category, names in names_by_cat.items():
        for key, value in names.items():
            reverse_map[value].append(key)
    with open('./utils/collect_info/duplication.log', 'w', encoding='utf-8') as f:
        for value, keys in reverse_map.items():
            if len(keys) > 1 and check_same_suffix(keys):
                f.write(
                    f"[WARNING] Duplicate value found: {value} -> {keys}\n")


if __name__ == "__main__":
    main()
