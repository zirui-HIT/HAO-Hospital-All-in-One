# -*- coding: utf-8 -*-
"""
Export IDs (Diagnoses/Symptoms/Examinations/Treatments) that LACK language translations.
仅判断“哪些实体ID没有中文翻译”，不会检查“哪些翻译没有对应ID”。

规则
1) 扫描四类实体：
   - <GameDBMedicalCondition ID="...">
   - <GameDBSymptom ID="...">
   - <GameDBExamination ID="...">
   - <GameDBTreatment ID="...">
2) 若实体有任意 *LocID 字段，要求这些 LocID 在 language 字符串表中均存在且 Text 非空；
3) 若实体没有 *LocID，则回退到 {ID} / {ID}_DESCRIPTION / {ID}_DESC 任一存在且非空即视为已翻译；
4) 输出未翻译的实体 ID -> JSON (List[str])。

注意
- 允许同一 ID 多处定义，最终只统计一次（最后一次扫描覆盖前面的元信息）。
"""

from __future__ import annotations

import os
import json
import argparse

from lxml import etree
from pathlib import Path
from typing import Dict, Set, List

TAG_DIAGNOSIS = "GameDBMedicalCondition"
TAG_SYMPTOM = "GameDBSymptom"
TAG_EXAM = "GameDBExamination"
TAG_TREATMENT = "GameDBTreatment"
TARGET_TAGS = {TAG_DIAGNOSIS, TAG_SYMPTOM, TAG_EXAM, TAG_TREATMENT}
ID_ATTR = "ID"


def parse_xml(file: Path) -> etree._ElementTree | None:
    try:
        parser = etree.XMLParser(
            remove_blank_text=False,
            strip_cdata=False,
            resolve_entities=False,
            remove_comments=False,
            ns_clean=True,
            huge_tree=True,
            recover=True,
        )
        return etree.parse(str(file), parser)
    except Exception as e:
        print(f"[WARN] Failed to parse {file}: {e}")
        return None


def is_locid_field(local_tag: str) -> bool:
    return local_tag.endswith("LocID")


def collect_entities(root_dirs: List[Path]) -> Dict[str, Set[str]]:
    """
    从多个根目录收集实体信息。
    返回: entities[id] = needed_locids（该实体中所有 *LocID 的取值；若为空则表示该实体无显式 LocID）
    若同一 ID 多次出现，以“最后一次扫描到”的为准（路径字典序 + 文档顺序）。
    """
    entities: Dict[str, Set[str]] = {}
    all_xml_files: Set[Path] = set()
    for root_dir in root_dirs:
        all_xml_files.update(root_dir.rglob("*.xml"))

    for file in sorted(all_xml_files, key=lambda p: str(p).lower()):
        # 如果当前file对应的路径包含"utils"，则跳过
        if 'utils' in str(file):
            continue

        tree = parse_xml(file)
        if tree is None:
            continue
        for elem in tree.getroot().iter():
            if not isinstance(elem.tag, str):
                continue
            tag = etree.QName(elem.tag).localname

            if tag not in TARGET_TAGS:
                continue
            ent_id = (elem.get(ID_ATTR) or "").strip()
            if not ent_id:
                continue
            needed: Set[str] = set()
            for child in elem.iter():
                if not isinstance(child.tag, str):
                    continue
                cname = etree.QName(child.tag).localname
                if is_locid_field(cname):
                    txt = (child.text or "").strip()
                    if txt:
                        needed.add(txt)
            entities[ent_id] = needed  # 覆盖为最后一次
    return entities


def collect_lang_table(root_dirs: List[Path], target_lang: str) -> Dict[str, str]:
    """
    从多个根目录收集 language 的 LocID->Text（仅保留 Text 非空）
    结构兼容：
    <GameDBStringTable>
      <LanguageCode>language</LanguageCode>
      <LocalizedStrings>
        <GameDBLocalizedString><LocID>..</LocID><Text>..</Text></GameDBLocalizedString>
      </LocalizedStrings>
    </GameDBStringTable>
    """
    lang_map: Dict[str, str] = {}
    all_xml_files: Set[Path] = set()
    for root_dir in root_dirs:
        all_xml_files.update(root_dir.rglob("*.xml"))

    for file in sorted(all_xml_files, key=lambda p: str(p).lower()):
        tree = parse_xml(file)
        if tree is None:
            continue
        root = tree.getroot()
        for tbl in root.findall(".//GameDBStringTable"):
            lang = (tbl.findtext(".//LanguageCode") or "").strip()
            if lang != target_lang:
                continue
            for item in tbl.findall(".//GameDBLocalizedString"):
                loc = (item.findtext("./LocID") or "").strip()
                txt = (item.findtext("./Text") or "").strip()
                if loc and txt:
                    lang_map[loc] = txt

    return lang_map


def main():
    ap = argparse.ArgumentParser(
        description="Export IDs without language translations (List[str] JSON).")
    ap.add_argument("--root", required=True, type=Path, nargs='+',
                    help="One or more root folders to scan XML files recursively.")
    ap.add_argument("--out", type=Path, required=True,
                    help="Output JSON path.")
    ap.add_argument("--lang", type=str, default="zh-Hans",
                    help="Output language code.")
    args = ap.parse_args()

    # 校验所有输入的 --root 路径都存在
    for root_path in args.root:
        if not root_path.exists():
            raise SystemExit(f"Root not found: {root_path}")

    print(f"[INFO] Scanning: {[str(p.resolve()) for p in args.root]}")

    entities = collect_entities(args.root)
    lang_map = collect_lang_table(args.root, args.lang)

    missing: List[str] = []
    for ent_id, needed_locids in entities.items():
        if needed_locids:
            # 有 *LocID：全部必须存在
            if not all(loc in lang_map for loc in needed_locids):
                missing.append(ent_id)
        else:
            # 无 *LocID：回退规则
            candidates = [ent_id, f"{ent_id}_DESCRIPTION", f"{ent_id}_DESC"]
            if not any(c in lang_map for c in candidates):
                missing.append(ent_id)

    # 去重排序并写出
    missing = sorted(set(missing))
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            json.dump(missing, f, ensure_ascii=False, indent=2)
        print(f"[DONE] Missing {args.lang} IDs: {len(missing)} -> {args.out}")
    except Exception as e:
        raise SystemExit(f"[ERROR] Failed to write output: {e}")


if __name__ == "__main__":
    main()
