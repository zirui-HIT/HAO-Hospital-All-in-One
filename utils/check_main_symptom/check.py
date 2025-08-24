import os
import glob
import argparse
import xml.etree.ElementTree as ET

from typing import Dict, List, Set, Tuple

# 定义数据结构类型提示，增强代码可读性
SymptomDB = Dict[str, Dict[str, bool]]
DiseaseDB = Dict[str, List[str]]


def _parse_and_update_db(file_list: List[str], symptoms_db: SymptomDB, reference_mode: bool = False):
    """
    一个内部辅助函数，用于解析XML文件列表并更新症状数据库。
    """
    mode_str = "参考" if reference_mode else "常规"
    for file_path in file_list:
        try:
            tree = ET.parse(file_path)
            xml_root = tree.getroot()
            for symptom_node in xml_root.findall('.//GameDBSymptom'):
                symptom_id = symptom_node.get('ID')
                if not symptom_id:
                    continue

                is_main_elem = symptom_node.find('IsMainSymptom')
                is_main = is_main_elem is not None and is_main_elem.text.lower() == 'true'

                # 如果症状已存在并且是参考模式，打印覆盖信息
                if symptom_id in symptoms_db and reference_mode:
                    print(
                        f"  - ({mode_str}模式) 发现重复症状'{symptom_id}'，将使用 '{file_path}' 中的定义进行覆盖。")

                symptoms_db[symptom_id] = {'is_main': is_main}
        except ET.ParseError as e:
            print(f"警告: 解析文件 '{file_path}' 时出错: {e}。已跳过此文件。")


# MODIFIED: 函数签名和文档已更新以接受路径列表
def load_symptoms(symptoms_paths: List[str], reference_path: str = None) -> SymptomDB:
    """
    递归加载一个或多个指定路径下的所有症状XML文件，并根据reference_path处理优先级。
    会分别独立地遍历symptoms_paths中的每个路径和reference_path。
    """
    symptoms_db: SymptomDB = {}
    print("--- 开始加载症状文件... ---")

    # MODIFIED: 从多个路径递归收集所有.xml文件
    other_files = []
    print(f"--- 正在从常规路径收集文件...")
    if symptoms_paths:
        for path in symptoms_paths:
            if os.path.isdir(path):
                print(f"  - 正在扫描路径: '{path}'")
                # 使用 extend 将当前路径下的所有文件添加到列表中
                other_files.extend([os.path.abspath(os.path.join(root, file))
                                   for root, _, files in os.walk(path)
                                   for file in files if file.endswith('.xml')])
            else:
                print(f"警告: 提供的症状路径 '{path}' 不是一个有效的目录，已跳过。")

    # 从参考路径收集文件（逻辑保持不变）
    reference_files = []
    if reference_path:
        if os.path.isdir(reference_path):
            print(f"--- 正在从参考路径 '{reference_path}' 收集文件...")
            reference_files = [os.path.abspath(os.path.join(root, file))
                               for root, _, files in os.walk(reference_path)
                               for file in files if file.endswith('.xml')]
        else:
            print(f"警告: 提供的参考路径 '{reference_path}' 不是一个有效的目录，将忽略此参数。")

    # 使用集合运算处理路径重叠的情况，确保每个文件只被处理一次
    other_files_set = set(other_files)
    reference_files_set = set(reference_files)

    # 从“其他文件”中移除所有在“参考文件”中也存在的文件
    unique_other_files = list(other_files_set - reference_files_set)

    # 最终的参考文件列表
    final_reference_files = list(reference_files_set)

    # 1. 首先加载所有唯一的常规文件
    if unique_other_files:
        print("\n--- 正在处理常规症状文件... ---")
        _parse_and_update_db(unique_other_files, symptoms_db)

    # 2. 然后加载参考文件，重复的定义将会覆盖
    if final_reference_files:
        print("\n--- 正在处理参考症状文件 (如有重复将进行覆盖)... ---")
        _parse_and_update_db(final_reference_files,
                             symptoms_db, reference_mode=True)

    print(f"\n--- 加载完成，共找到 {len(symptoms_db)} 个独立症状。 ---\n")
    return symptoms_db


def parse_diseases(disease_file: str) -> DiseaseDB:
    """
    解析指定的疾病XML文件。
    （此函数无需修改）
    """
    diseases_db: DiseaseDB = {}
    print(f"--- 正在解析疾病文件 '{disease_file}'... ---")

    if not os.path.isfile(disease_file):
        print(f"错误: 疾病文件 '{disease_file}' 不存在。")
        return diseases_db

    try:
        tree = ET.parse(disease_file)
        root = tree.getroot()
        for disease_node in root.findall('.//GameDBMedicalCondition'):
            disease_id = disease_node.get('ID')
            if not disease_id:
                continue

            symptom_refs = [
                ref.text for ref in disease_node.findall('.//Symptoms/GameDBSymptomRules/GameDBSymptomRef')
                if ref.text
            ]
            diseases_db[disease_id] = symptom_refs

    except ET.ParseError as e:
        print(f"致命错误: 解析疾病文件 '{disease_file}' 时出错: {e}。无法继续。")
        return {}

    print(f"--- 解析完成，共找到 {len(diseases_db)} 个疾病。 ---\n")
    return diseases_db


def validate_diagnoses(diseases: DiseaseDB, symptoms: SymptomDB) -> List[str]:
    """
    根据规则验证疾病的主症状配置。
    （此函数无需修改）
    """
    errors: List[str] = []
    used_main_symptoms: Dict[str, str] = {}  # 存储已用主症状及其所属疾病ID

    for disease_id, associated_symptoms in diseases.items():
        main_symptoms_found = []
        for symptom_id in associated_symptoms:
            if symptom_id not in symptoms:
                errors.append(
                    f"数据完整性错误: 疾病 '{disease_id}' 引用了未定义的症状 '{symptom_id}'。")
                continue

            if symptoms[symptom_id].get('is_main', False):
                main_symptoms_found.append(symptom_id)

        # 条件一：检查每个疾病的主症状数量
        if len(main_symptoms_found) == 0:
            errors.append(
                f"规则1错误: 疾病 '{disease_id}' 没有找到任何主要症状 (<IsMainSymptom>为true)。")
        elif len(main_symptoms_found) > 1:
            errors.append(
                f"规则1错误: 疾病 '{disease_id}' 有 {len(main_symptoms_found)} 个主要症状: {', '.join(main_symptoms_found)}。应有且仅有一个。")
        else:
            # 条件二：检查主症状是否在不同疾病间重复
            main_symptom = main_symptoms_found[0]
            if main_symptom in used_main_symptoms:
                original_disease = used_main_symptoms[main_symptom]
                errors.append(
                    f"规则2错误: 主要症状 '{main_symptom}' 在疾病 '{disease_id}' 和 '{original_disease}' 之间重复使用。")
            else:
                used_main_symptoms[main_symptom] = disease_id

    return errors


def main():
    """
    主函数，用于设置命令行参数解析并执行整个验证流程。
    """
    parser = argparse.ArgumentParser(
        description="验证疾病文件中的主症状配置是否符合规范，并根据参考路径处理优先级。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    # MODIFIED: argparse参数现在接受一个或多个路径
    parser.add_argument("--disease_path", type=str,
                        required=True, help="疾病文件夹路径")
    parser.add_argument("--symptoms_paths", nargs='+', type=str, required=True,
                        help="一个或多个症状文件夹路径，用空格分隔。")
    parser.add_argument("--reference_path", type=str,
                        help="参考文件夹路径（可选，具有更高优先级）。")
    args = parser.parse_args()

    # 1. 加载所有症状，并应用优先级规则
    # MODIFIED: 传递路径列表 args.symptoms_paths
    symptoms_database = load_symptoms(args.symptoms_paths, args.reference_path)
    if not symptoms_database:
        print("未能加载任何症状，检查终止。")
        return

    # 加载args.disease_path下的所有.xml文件
    disease_files = glob.glob(os.path.join(args.disease_path, "*.xml"))
    if not disease_files:
        print(f"未能在 '{args.disease_path}' 路径下找到任何疾病文件，检查终止。")
        return

    # 2. 解析疾病文件
    diseases_database = {}
    for file in disease_files:
        file_diseases = parse_diseases(file)
        diseases_database.update(file_diseases)

    if not diseases_database:
        print("未能解析疾病文件，检查终止。")
        return

    # 3. 执行验证
    print("--- 开始验证主症状配置... ---")
    validation_errors = validate_diagnoses(
        diseases_database, symptoms_database)
    print("--- 验证完成。 ---\n")

    # 4. 报告结果
    if not validation_errors:
        print("✅ 检查通过！所有疾病的主症状配置均符合规范。")
    else:
        print(f"❌ 发现 {len(validation_errors)} 个配置问题：")
        for i, error in enumerate(validation_errors, 1):
            print(f"{i}. {error}")


if __name__ == "__main__":
    main()
