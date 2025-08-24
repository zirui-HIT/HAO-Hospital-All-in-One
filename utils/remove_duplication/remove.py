# -*- coding: utf-8 -*-
"""
Dedupe IDs across Project Hospital-style XML packs and keep ONLY the last occurrence.

目标
- 递归扫描 --root 下所有 .xml
- 针对以下四类节点（同上）：只保留“最后一次出现”的 ID 定义
    - <GameDBMedicalCondition   ID="...">  # 疾病
    - <GameDBSymptom            ID="...">  # 症状
    - <GameDBExamination        ID="...">  # 检测
    - <GameDBTreatment          ID="...">  # 治疗（含手术）
- 写回原文件，尽量保持注释与空白（不改变未触及的节点）

说明
- “最后一次出现”根据全局扫描顺序确定：
  先按文件路径排序，再按同一文件中的文档顺序；该顺序中“最后”的定义被保留。
- 如需改为“文件修改时间优先”，修改 sorted_xmls 的排序逻辑即可。

安全
- 默认 dry-run；加 --apply 才写回；建议加 --backup 生成 .bak。
- 如果同一个 ID 出现在多个文件，将删除前面文件中的重复定义，只保留最后那个文件中的定义。
"""

from __future__ import annotations
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple
from lxml import etree
from collections import defaultdict

# ===== 配置：要去重的标签类型（同上）=====
TAG_WHITELIST = {
    "GameDBMedicalCondition",  # 疾病
    "GameDBSymptom",           # 症状
    "GameDBExamination",       # 检测
    "GameDBTreatment",         # 治疗/手术
}
ID_ATTR = "ID"


@dataclass
class DefLoc:
    file: Path
    tree: etree._ElementTree
    elem: etree._Element
    tag: str
    index_in_file: int  # 文档顺序（用于调试）


def parse_xml(file: Path) -> etree._ElementTree | None:
    try:
        parser = etree.XMLParser(
            remove_blank_text=False,
            strip_cdata=False,
            resolve_entities=False,
            remove_comments=False,  # 保留注释
            ns_clean=True,
            huge_tree=True,
            recover=True,
        )
        return etree.parse(str(file), parser)
    except Exception as e:
        print(f"[WARN] Failed to parse {file}: {e}")
        return None


def enumerate_defs_in_file(file: Path, tree: etree._ElementTree) -> List[DefLoc]:
    """按文档顺序列举 file 中的目标元素定义。"""
    root = tree.getroot()
    seq: List[DefLoc] = []
    idx = 0
    # 以文档顺序遍历所有元素，再筛选白名单标签和有 ID 的
    for elem in root.iter():
        tag = etree.QName(elem.tag).localname if isinstance(
            elem.tag, str) else None
        if not tag or tag not in TAG_WHITELIST:
            continue
        id_ = (elem.get(ID_ATTR) or "").strip()
        if not id_:
            continue
        seq.append(DefLoc(file=file, tree=tree,
                   elem=elem, tag=tag, index_in_file=idx))
        idx += 1
    return seq


def build_occurrence_index(root: Path) -> Tuple[Dict[Tuple[str, str], List[DefLoc]], Dict[Path, etree._ElementTree]]:
    """返回 (occ_map, trees_by_file)：
       occ_map[(tag, id)] -> [DefLoc, DefLoc, ...]  （按全局“文件路径排序 + 文档顺序”）
    """
    trees_by_file: Dict[Path, etree._ElementTree] = {}
    occ_map: Dict[Tuple[str, str], List[DefLoc]] = defaultdict(list)

    # 1) 确定全局文件顺序：按路径排序（确保确定性）
    xml_files = sorted(root.rglob("*.xml"), key=lambda p: str(p).lower())

    for f in xml_files:
        tree = parse_xml(f)
        if tree is None:
            continue
        trees_by_file[f] = tree
        defs = enumerate_defs_in_file(f, tree)
        for d in defs:
            id_ = (d.elem.get(ID_ATTR) or "").strip()
            if not id_:
                continue
            occ_map[(d.tag, id_)].append(d)

    return occ_map, trees_by_file


def dedupe_keep_last(occ_map: Dict[Tuple[str, str], List[DefLoc]], apply_changes: bool) -> Dict[str, int]:
    """
    对每个 (tag, id) 的出现列表，只保留最后一个，其余删除。
    返回统计信息。
    """
    removed = 0
    kept = 0

    for (tag, id_), locs in occ_map.items():
        if len(locs) <= 1:
            kept += len(locs)
            continue

        # 需要删除的：除最后一个以外全部
        to_remove = locs[:-1]
        to_keep = locs[-1]
        kept += 1

        print(
            f"[DUP] {tag}:{id_}  -> keep {to_keep.file.name}#{to_keep.index_in_file}, remove {len(to_remove)} earlier")
        if apply_changes:
            for loc in to_remove:
                parent = loc.elem.getparent()
                if parent is None:
                    continue
                # 如需“上提保留”被删节点内部的注释，可开启以下逻辑：
                # for node in list(loc.elem):
                #     if isinstance(node, etree._Comment):
                #         parent.insert(parent.index(loc.elem), node)  # HOIST_COMMENTS
                parent.remove(loc.elem)
                removed += 1

    return {"removed": removed, "kept": kept}


def write_back(trees_by_file: Dict[Path, etree._ElementTree], backup: bool):
    for file, tree in trees_by_file.items():
        if backup:
            try:
                file.with_suffix(
                    file.suffix + ".bak").write_bytes(file.read_bytes())
            except Exception as e:
                print(f"[WARN] Backup failed for {file}: {e}")
        try:
            tree.write(
                str(file),
                encoding="utf-8",
                xml_declaration=True,
                pretty_print=True,   # 如想最小化差异可改为 False
                standalone=False,
            )
        except Exception as e:
            print(f"[WARN] Failed to write {file}: {e}")


def main():
    ap = argparse.ArgumentParser(
        description="Deduplicate IDs and keep only the last occurrence (by file path + document order).")
    ap.add_argument("--root", required=True, type=Path,
                    help="Root folder to scan XML files recursively.")
    ap.add_argument("--apply", action="store_true",
                    help="Apply changes (otherwise dry-run).")
    ap.add_argument("--backup", action="store_true",
                    help="Create .bak backup before writing.")
    args = ap.parse_args()

    root: Path = args.root
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    print(f"[INFO] Scanning XMLs under: {root.resolve()}")
    occ_map, trees_by_file = build_occurrence_index(root)

    total_keys = len(occ_map)
    total_occurrences = sum(len(v) for v in occ_map.values())
    dup_keys = sum(1 for v in occ_map.values() if len(v) > 1)
    dup_occurrences = sum(len(v) - 1 for v in occ_map.values() if len(v) > 1)
    print(
        f"[INFO] Collected {total_occurrences} occurrences of {total_keys} unique (tag,id) keys.")
    print(
        f"[INFO] Duplicated keys: {dup_keys}, duplicated occurrences to remove: {dup_occurrences}")

    stats = dedupe_keep_last(occ_map, apply_changes=args.apply)

    if args.apply:
        write_back(trees_by_file, backup=args.backup)
        print(
            f"\n[DONE] Removed {stats['removed']} duplicate definition(s). Kept {stats['kept']} definition(s).")
    else:
        print(
            "\n[DRY-RUN] No files were modified. Use --apply to write changes (add --backup for safety).")


if __name__ == "__main__":
    main()
