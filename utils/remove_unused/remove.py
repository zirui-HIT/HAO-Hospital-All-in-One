# -*- coding: utf-8 -*-
"""
Two-phase pruning with dependency closures:
- Examinations: LabTestingExaminationRef creates undirected keep-together edges.
- Symptoms: CollapseSymptomRef creates one-way keep edges (A keeps B; transitive).
- Surgeries: If a USED surgery has Complication/SymptomRef=B, then keep symptom B.

Algorithm
1) kept_symptoms := symptoms referenced by diagnoses
2) Iterate until fixed point:
   a) kept_symptoms := closure via CollapseSymptomRef (transitive)
   b) used_exams  := exams referenced by diagnoses ∪ by kept_symptoms
      used_treats := treats referenced by diagnoses ∪ by kept_symptoms
   c) used_exams  := closure via LabTestingExaminationRef equivalence (undirected)
   d) kept_symptoms := kept_symptoms ∪ { all complication symptom refs of USED surgeries }
3) Delete:
   - Symptoms not in kept_symptoms
   - Examinations not in used_exams
   - Treatments (incl. surgeries) not in used_treats

Preserves comments; default dry-run; use --apply to write; --backup to create .bak.

Usage:
  python prune_unused_med_content_v3.py --root /path/to/xmls                # dry-run
  python prune_unused_med_content_v3.py --root /path/to/xmls --apply --backup
"""

from __future__ import annotations
import argparse
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple
from lxml import etree

# --------- Tags & XPaths ---------
EXAM_TAG = "GameDBExamination"
SYMPTOM_TAG = "GameDBSymptom"
TREATMENT_TAG = "GameDBTreatment"
DIAGNOSIS_TAG = "GameDBMedicalCondition"
ID_ATTR = "ID"

# From diagnoses:
X_SYMPTOMS_IN_DIAG = ".//Symptoms//GameDBSymptomRef/text()"
X_EXAMS_IN_DIAG = ".//Examinations//ExaminationRef/text()"
X_TREATS_IN_DIAG = ".//Treatments//TreatmentRef/text()"

# From symptoms:
X_EXAMS_IN_SYMPTOM = ".//Examinations//ExaminationRef/text()"
X_TREATS_IN_SYMPTOM = ".//Treatments//TreatmentRef/text()"
X_COLLAPSE_IN_SYMPT = ".//CollapseSymptomRef/text()"

# From examinations:
X_LAB_PEER_IN_EXAM = ".//LabTestingExaminationRef/text()"

# From treatments:
X_TREATMENT_TYPE = ".//TreatmentType/text()"
X_COMPLICATION_SYMPT = ".//Complication//SymptomRef/text()"   # robust path


@dataclass
class DefLoc:
    file: Path
    tree: etree._ElementTree
    elem: etree._Element


class Index:
    def __init__(self) -> None:
        self.diagnoses: Dict[str, List[DefLoc]] = defaultdict(list)
        self.symptoms:  Dict[str, List[DefLoc]] = defaultdict(list)
        self.exams:     Dict[str, List[DefLoc]] = defaultdict(list)
        self.treats:    Dict[str, List[DefLoc]] = defaultdict(list)
        self.trees_by_file: Dict[Path, etree._ElementTree] = {}

    def add(self, tag: str, id_: str, file: Path, tree: etree._ElementTree, elem: etree._Element):
        m = None
        if tag == DIAGNOSIS_TAG:
            m = self.diagnoses
        elif tag == SYMPTOM_TAG:
            m = self.symptoms
        elif tag == EXAM_TAG:
            m = self.exams
        elif tag == TREATMENT_TAG:
            m = self.treats
        if m is not None and id_:
            m[id_].append(DefLoc(file, tree, elem))


def parse_xml(file: Path) -> etree._ElementTree | None:
    try:
        parser = etree.XMLParser(
            remove_blank_text=False,
            strip_cdata=False,
            resolve_entities=False,
            remove_comments=False,  # preserve comments
            ns_clean=True,
            huge_tree=True,
            recover=True,
        )
        return etree.parse(str(file), parser)
    except Exception as e:
        print(f"[WARN] Failed to parse {file}: {e}")
        return None


def build_index(root: Path) -> Index:
    idx = Index()
    for file in sorted(root.rglob("*.xml"), key=lambda p: str(p).lower()):
        tree = parse_xml(file)
        if tree is None:
            continue
        idx.trees_by_file[file] = tree
        for tag in (DIAGNOSIS_TAG, SYMPTOM_TAG, EXAM_TAG, TREATMENT_TAG):
            for elem in tree.findall(f".//{tag}"):
                id_ = (elem.get(ID_ATTR) or "").strip()
                if id_:
                    idx.add(tag, id_, file, tree, elem)
    return idx

# --------- Helpers to normalize text sets ---------


def _texts(nodes: List[str]) -> Set[str]:
    return {s.strip() for s in nodes if s and s.strip()}

# --------- Precompute dependency maps (union across multi-definitions) ---------


def precompute_maps(idx: Index):
    # Diagnoses direct refs (union)
    diag_sym_refs: Set[str] = set()
    diag_exam_refs: Set[str] = set()
    diag_treat_refs: Set[str] = set()
    for _, locs in idx.diagnoses.items():
        for loc in locs:
            e = loc.elem
            diag_sym_refs.update(_texts(e.xpath(X_SYMPTOMS_IN_DIAG)))
            diag_exam_refs.update(_texts(e.xpath(X_EXAMS_IN_DIAG)))
            diag_treat_refs.update(_texts(e.xpath(X_TREATS_IN_DIAG)))

    # Symptoms -> exams/treats; Collapse map
    symptom_to_exams: Dict[str, Set[str]] = defaultdict(set)
    symptom_to_treats: Dict[str, Set[str]] = defaultdict(set)
    symptom_collapse: Dict[str, Set[str]] = defaultdict(set)  # A -> {B...}
    for sid, locs in idx.symptoms.items():
        for loc in locs:
            e = loc.elem
            symptom_to_exams[sid].update(_texts(e.xpath(X_EXAMS_IN_SYMPTOM)))
            symptom_to_treats[sid].update(_texts(e.xpath(X_TREATS_IN_SYMPTOM)))
            symptom_collapse[sid].update(_texts(e.xpath(X_COLLAPSE_IN_SYMPT)))

    # Exams undirected adjacency via LabTestingExaminationRef
    exam_adj: Dict[str, Set[str]] = defaultdict(set)
    for eid, locs in idx.exams.items():
        for loc in locs:
            e = loc.elem
            peers = _texts(e.xpath(X_LAB_PEER_IN_EXAM))
            for p in peers:
                exam_adj[eid].add(p)
                exam_adj[p].add(eid)  # undirected equivalence

    # Treatments info
    treat_is_surgery: Set[str] = set()
    treat_complication_syms: Dict[str, Set[str]] = defaultdict(set)
    for tid, locs in idx.treats.items():
        is_surg = False
        comp_syms: Set[str] = set()
        for loc in locs:
            e = loc.elem
            # Type (case-insensitive; treat missing as not surgery)
            for tt in _texts(e.xpath(X_TREATMENT_TYPE)):
                if tt.upper() == "SURGERY":
                    is_surg = True
                    break
            # Complication symptom refs
            comp_syms.update(_texts(e.xpath(X_COMPLICATION_SYMPT)))
        if is_surg:
            treat_is_surgery.add(tid)
        if comp_syms:
            treat_complication_syms[tid].update(comp_syms)

    return (diag_sym_refs, diag_exam_refs, diag_treat_refs,
            symptom_to_exams, symptom_to_treats, symptom_collapse,
            exam_adj, treat_is_surgery, treat_complication_syms)

# --------- Closures ---------


def closure_collapse_symptoms(seed: Set[str], collapse_map: Dict[str, Set[str]]) -> Set[str]:
    """Directed closure: if A kept, keep B for all B in collapse[A]. Transitive."""
    kept = set(seed)
    q = deque(seed)
    while q:
        a = q.popleft()
        for b in collapse_map.get(a, ()):
            if b not in kept:
                kept.add(b)
                q.append(b)
    return kept


def closure_equiv_exams(seed: Set[str], adj: Dict[str, Set[str]]) -> Set[str]:
    """Undirected closure via LabTestingExaminationRef equivalence."""
    kept = set(seed)
    q = deque(seed)
    while q:
        a = q.popleft()
        for b in adj.get(a, ()):
            if b not in kept:
                kept.add(b)
                q.append(b)
    return kept

# --------- Delete utils ---------


def delete_ids(id_list: List[str], bucket: Dict[str, List[DefLoc]]):
    for id_ in id_list:
        locs = bucket.get(id_, [])
        for loc in locs:
            parent = loc.elem.getparent()
            if parent is not None:
                # If you prefer to hoist inner comments, enable:
                # for node in list(loc.elem):
                #     if isinstance(node, etree._Comment):
                #         parent.insert(parent.index(loc.elem), node)
                parent.remove(loc.elem)
        bucket.pop(id_, None)


def write_back(idx: Index, backup: bool):
    for file, tree in idx.trees_by_file.items():
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
                pretty_print=True,  # set False to minimize diffs
                standalone=False,
            )
        except Exception as e:
            print(f"[WARN] Failed to write {file}: {e}")


def main():
    ap = argparse.ArgumentParser(
        description="Two-phase pruning with LabTesting, CollapseSymptomRef, and surgery complications.")
    ap.add_argument("--root", required=True, type=Path,
                    help="Root folder with XMLs (recursive).")
    ap.add_argument("--apply", action="store_true",
                    help="Apply changes (otherwise dry-run).")
    ap.add_argument("--backup", action="store_true",
                    help="Create .bak backups before writing.")
    args = ap.parse_args()

    root = args.root
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    print(f"[INFO] Scanning XMLs under: {root.resolve()}")
    idx = build_index(root)
    print(
        f"[INFO] Indexed: diagnoses={len(idx.diagnoses)} symptoms={len(idx.symptoms)} exams={len(idx.exams)} treatments={len(idx.treats)}")

    (diag_sym_refs, diag_exam_refs, diag_treat_refs,
     symptom_to_exams, symptom_to_treats, symptom_collapse,
     exam_adj, treat_is_surgery, treat_complication_syms) = precompute_maps(idx)

    # ---- Fixed-point computation of kept/used sets ----
    # start from diseases' symptom refs
    kept_symptoms: Set[str] = set(diag_sym_refs)
    used_exams: Set[str] = set(diag_exam_refs)
    used_treats: Set[str] = set(diag_treat_refs)

    iter_no = 0
    while True:
        iter_no += 1
        prev_state = (len(kept_symptoms), len(used_exams), len(used_treats))

        # Collapse closure on symptoms
        kept_symptoms = closure_collapse_symptoms(
            kept_symptoms, symptom_collapse)

        # Add exams/treats referenced by kept symptoms
        for s in list(kept_symptoms):
            used_exams.update(symptom_to_exams.get(s, ()))
            used_treats.update(symptom_to_treats.get(s, ()))

        # Exams LabTesting equivalence closure
        used_exams = closure_equiv_exams(used_exams, exam_adj)

        # From USED surgeries, keep complication symptoms
        for t in list(used_treats):
            if t in treat_is_surgery:
                kept_symptoms.update(treat_complication_syms.get(t, ()))

        curr_state = (len(kept_symptoms), len(used_exams), len(used_treats))
        if curr_state == prev_state:
            break  # fixed point

    # ---- Decide deletions ----
    defined_symptoms = set(idx.symptoms.keys())
    defined_exams = set(idx.exams.keys())
    defined_treats = set(idx.treats.keys())

    unused_symptoms = sorted(defined_symptoms - kept_symptoms)
    unused_exams = sorted(defined_exams - used_exams)
    unused_treats = sorted(defined_treats - used_treats)

    # ---- Phase 1: delete symptoms ----
    print(f"\n[PHASE 1] Symptoms (after closures)")
    print(
        f"  keep: {len(defined_symptoms) - len(unused_symptoms)} / {len(defined_symptoms)}")
    print(f"  delete: {len(unused_symptoms)}")
    if args.apply and unused_symptoms:
        delete_ids(unused_symptoms, idx.symptoms)
        print(f"  Deleted {len(unused_symptoms)} symptom(s).")

    # ---- Phase 2: exams & treatments ----
    print(f"\n[PHASE 2] Examinations & Treatments")
    print(
        f"  exams used/def     : {len(used_exams)} / {len(defined_exams)}    -> delete {len(unused_exams)}")
    print(
        f"  treatments used/def: {len(used_treats)} / {len(defined_treats)} -> delete {len(unused_treats)}")
    if args.apply:
        if unused_exams:
            delete_ids(unused_exams, idx.exams)
            print(f"  Deleted {len(unused_exams)} examination(s).")
        if unused_treats:
            delete_ids(unused_treats, idx.treats)
            print(f"  Deleted {len(unused_treats)} treatment(s).")

    # ---- Write back ----
    if args.apply:
        write_back(idx, backup=args.backup)
        print("\n[DONE] Changes applied and files written.")
    else:
        print(
            "\n[DRY-RUN] No files were modified. Use --apply to write changes (add --backup for safety).")


if __name__ == "__main__":
    main()
