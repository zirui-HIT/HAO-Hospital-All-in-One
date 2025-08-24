# -*- coding: utf-8 -*-
"""
Split a symptom XML into two files by <IsMainSymptom> true/false.
- 保留所有注释：
  * 症状元素内部的注释会随元素一并保留
  * 症状前的“相邻注释/空白”会被一起搬到对应输出文件
- 输出文件：<原名>_main.xml 与 <原名>_other.xml
"""

from __future__ import annotations
import argparse
from pathlib import Path
from typing import List
from copy import deepcopy
from lxml import etree

SYMPTOM_TAG = "GameDBSymptom"

def parse_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(
        remove_blank_text=False,
        strip_cdata=False,
        resolve_entities=False,
        remove_comments=False,  # 关键：保留注释
        ns_clean=True,
        huge_tree=True,
        recover=True,
    )
    return etree.parse(str(path), parser)

def is_true_text(s: str | None) -> bool:
    if not s:
        return False
    t = s.strip().lower()
    return t in {"true", "1", "yes"}

def is_main_symptom(sym_elem: etree._Element) -> bool:
    # <IsMainSymptom> 可能不存在，视为 False
    node = sym_elem.find(".//IsMainSymptom")
    return is_true_text(node.text if node is not None else None)

def make_empty_root_like(src_root: etree._Element) -> etree._Element:
    # 复制根标签与属性（不复制子节点），尽量保持结构风格
    new_root = etree.Element(src_root.tag, attrib=dict(src_root.attrib))
    # 也可复制根级别的 text（通常为空）
    new_root.text = src_root.text
    return new_root

def split_file(xml_path: Path, outdir: Path) -> tuple[Path, Path, int, int]:
    tree = parse_xml(xml_path)
    root = tree.getroot()

    # 目标根：与原始根同名与同属性
    root_main = make_empty_root_like(root)
    root_other = make_empty_root_like(root)

    # 我们在“根的直接子级序列”上运作，以便能把症状前的注释也一并带走
    # 若症状不在根直系，可按需要改为更深层容器；此处假设文件主要是症状清单
    pending: List[etree._Element] = []  # 累积“症状前”的注释/空白节点
    count_main = 0
    count_other = 0

    for node in list(root):  # 只看根的直接子节点顺序
        if isinstance(node.tag, str):
            tag_local = etree.QName(node.tag).localname
        else:
            tag_local = None

        if tag_local == SYMPTOM_TAG:
            target_parent = root_main if is_main_symptom(node) else root_other

            # 先把 pending 的注释/空白拷贝过去
            for p in pending:
                target_parent.append(deepcopy(p))
            pending.clear()

            # 再拷贝症状本体
            target_parent.append(deepcopy(node))
            if target_parent is root_main:
                count_main += 1
            else:
                count_other += 1

        else:
            # 非症状节点：如果是注释或纯空白文本的“占位节点”，缓存在 pending，
            # 以便附着到下一个症状前；否则忽略（比如其它类型元素）
            if isinstance(node, etree._Comment):
                pending.append(deepcopy(node))
            else:
                # 对于无标签节点（极少见）或其它元素，尽量只把纯空白的 text/tail 作为注释对待
                # 通常 XML 结构里这些会是换行/缩进，deepcopy 也不会有副作用
                if (getattr(node, "text", None) or "").strip() == "" and \
                   (getattr(node, "tail", None) or "").strip() == "":
                    pending.append(deepcopy(node))
                # 若你需要把根级的其它元素（比如 <Comments> 容器）也复制到两个文件，可在此添加逻辑

    # 末尾若仍有悬挂注释，把它们附在两个文件末尾，避免丢失
    for p in pending:
        root_main.append(deepcopy(p))
        root_other.append(deepcopy(p))

    # 写出
    outdir.mkdir(parents=True, exist_ok=True)
    stem = xml_path.stem
    out_main = outdir / f"{stem}_main.xml"
    out_other = outdir / f"{stem}_other.xml"

    etree.ElementTree(root_main).write(
        str(out_main),
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
        standalone=False,
    )
    etree.ElementTree(root_other).write(
        str(out_other),
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
        standalone=False,
    )
    return out_main, out_other, count_main, count_other

def main():
    ap = argparse.ArgumentParser(description="Split a symptom XML into two files by <IsMainSymptom>.")
    ap.add_argument("--xml", required=True, type=Path, help="Path to the symptom XML file.")
    ap.add_argument("--outdir", required=True, type=Path, help="Output folder.")
    args = ap.parse_args()

    out_main, out_other, n_main, n_other = split_file(args.xml, args.outdir)
    print(f"[DONE] Main symptoms   : {n_main} -> {out_main}")
    print(f"[DONE] Other symptoms  : {n_other} -> {out_other}")

if __name__ == "__main__":
    main()
