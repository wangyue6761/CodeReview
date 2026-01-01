"""提取测试用例信息到新的JSON文件。

从 test_prs.json 中提取必要信息（仓库分组、case名称、prlink、base_branch、head_branch）
并保存到新的JSON文件中。
"""

import json
from pathlib import Path
from typing import Dict, List


def extract_test_cases(input_file: Path, output_file: Path) -> None:
    """从原始JSON文件中提取测试用例信息。
    
    Args:
        input_file: 输入的 test_prs.json 文件路径
        output_file: 输出的新JSON文件路径
    """
    print(f"📖 读取文件: {input_file}")
    
    # 读取完整的JSON文件
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    print(f"✅ 文件读取成功")
    
    # 提取必要信息
    extracted_data = {}
    total_cases = 0
    
    for repo_group, cases in data.items():
        extracted_data[repo_group] = {}
        
        # 仓库名格式：{repo_group}-greptile
        repo_name = f"{repo_group}-greptile"
        
        for case_name, case_data in cases.items():
            # 提取必要字段
            extracted_case = {
                "repo_name": repo_name,
                "prlink": case_data.get("prlink", ""),
                "base_branch": case_data.get("base_branch"),
                "head_branch": case_data.get("head_branch"),
            }
            
            # 只保留有效的case（有prlink和分支信息）
            if extracted_case["prlink"] and extracted_case["base_branch"] and extracted_case["head_branch"]:
                extracted_data[repo_group][case_name] = extracted_case
                total_cases += 1
    
    print(f"📊 提取完成: {len(extracted_data)} 个仓库分组, {total_cases} 个有效用例")
    
    # 保存到新文件
    print(f"💾 保存到: {output_file}")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(extracted_data, f, indent=2, ensure_ascii=False)
    
    print(f"✅ 保存成功!")
    
    # 显示统计信息
    print("\n📈 统计信息:")
    for repo_group, cases in extracted_data.items():
        print(f"  - {repo_group}: {len(cases)} 个用例")


if __name__ == "__main__":
    # 获取脚本所在目录
    script_dir = Path(__file__).parent
    
    input_file = script_dir / "test_prs.json"
    output_file = script_dir / "test_cases.json"
    
    if not input_file.exists():
        print(f"❌ 输入文件不存在: {input_file}")
        exit(1)
    
    extract_test_cases(input_file, output_file)
    print(f"\n✨ 完成! 新文件: {output_file}")

