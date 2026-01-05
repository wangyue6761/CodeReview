"""自动化测试脚本，从 test_prs.json 读取测试用例并批量运行代码审查。

工作流程：
1. 读取 test_prs.json (1-1638行)
2. 解析测试用例（仓库名、PR号、base/head分支）
3. 根据命令行参数过滤用例
4. 对每个用例调用代码审查系统
5. 将结果保存为带仓库和PR标识的文件
"""

import asyncio
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 注意：不要在模块导入时引入 main/run_review。
# 这样在仅做用例过滤（甚至过滤后为 0）时，不会触发昂贵的依赖导入或副作用。


def extract_pr_number(prlink: str) -> Optional[int]:
    """从 prlink 提取PR号。
    
    Args:
        prlink: PR链接，格式如 "https://github.com/ai-code-review-evaluation/sentry-greptile/pull/1"
    
    Returns:
        PR号，如果解析失败返回 None
    """
    # 匹配格式: .../pull/{number}
    pattern = r"/pull/(\d+)"
    match = re.search(pattern, prlink)
    
    if match:
        return int(match.group(1))
    
    return None


def parse_cases_range(cases_str: str) -> Tuple[int, int]:
    """解析case范围字符串，如 "1-10"。
    
    Args:
        cases_str: 范围字符串，格式如 "1-10" 或 "1"
    
    Returns:
        (start, end) 元组，end为-1表示全部
    """
    if not cases_str:
        return (1, -1)  # 默认全部
    
    # 支持格式: "1-10" 或 "10"
    if "-" in cases_str:
        parts = cases_str.split("-", 1)
        start = int(parts[0])
        end = int(parts[1])
        return (start, end)
    else:
        # 单个数字，表示前N个
        num = int(cases_str)
        return (1, num)


def load_test_cases(test_file: Path) -> Dict:
    """加载测试用例JSON文件。
    
    Args:
        test_file: 测试文件路径（应该是提取后的 test_cases.json）
    
    Returns:
        解析后的JSON字典
    """
    with open(test_file, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_all_cases(test_data: Dict) -> List[Dict]:
    """收集所有测试用例，按顺序排列。
    
    Args:
        test_data: 从test_prs.json加载的数据
    
    Returns:
        用例列表，每个用例包含：repo_group, case_name, case_data
    """
    all_cases = []
    
    for repo_group, cases in test_data.items():
        for case_name, case_data in cases.items():
            all_cases.append({
                "repo_group": repo_group,
                "case_name": case_name,
                "case_data": case_data
            })
    
    return all_cases


def filter_cases(
    all_cases: List[Dict],
    repos: Optional[List[str]] = None,
    cases_range: Optional[Tuple[int, int]] = None
) -> List[Dict]:
    """根据参数过滤用例。
    
    Args:
        all_cases: 所有用例列表
        repos: 要测试的仓库分组列表（None表示全部）
        cases_range: case范围 (start, end)，end为-1表示全部
    
    Returns:
        过滤后的用例列表
    """
    # 先按仓库过滤，再对过滤后的列表应用 cases_range。
    # 这样 `--repos cal.com --cases 1-1` 表示 “cal.com 的第 1 个用例”，
    # 而不是 “全局第 1 个用例且 repo_group=cal.com”（后者通常会得到 0 个）。
    filtered = []

    for case in all_cases:
        if repos and case["repo_group"] not in repos:
            continue
        filtered.append(case)

    if not cases_range:
        return filtered

    start, end = cases_range
    start = max(start, 1)
    if end == -1:
        return filtered[start - 1 :]
    return filtered[start - 1 : end]


async def run_single_test(
    case: Dict,
    datasets_dir: Path,
    output_dir: Path,
    quiet: bool = False
) -> Tuple[bool, str]:
    """运行单个测试用例。
    
    注意: output_dir 参数保留用于向后兼容，但不再使用。
    结果文件由 main.py 自动保存到 log 目录。
    
    Args:
        case: 测试用例字典
        datasets_dir: 数据集目录
        output_dir: 输出目录（已弃用，保留用于向后兼容）
        quiet: 是否静默模式
    
    Returns:
        (success, message) 元组
    """
    case_data = case["case_data"]
    prlink = case_data.get("prlink", "")
    case_name = case["case_name"]
    
    # 从case_data中获取仓库名（格式：{repo_group}-greptile）
    repo_name = case_data.get("repo_name")
    if not repo_name:
        return (False, f"case_data中缺少repo_name字段")
    
    # 从prlink提取PR号
    pr_number = extract_pr_number(prlink)
    if not pr_number:
        return (False, f"无法从prlink提取PR号: {prlink}")
    
    # 构建仓库路径
    repo_path = datasets_dir / repo_name
    if not repo_path.exists():
        return (False, f"仓库目录不存在: {repo_path}")
    
    # 获取base和head分支
    base_branch = case_data.get("base_branch")
    head_branch = case_data.get("head_branch")
    
    if not base_branch or not head_branch:
        return (False, f"base_branch或head_branch为null: base={base_branch}, head={head_branch}")
    
    # 运行审查
    # 注意: output_file 参数会被 main.py 忽略，因为 main.py 会自动生成输出位置
    # 但函数签名要求此参数，所以传入一个占位符路径
    placeholder_output = Path("/dev/null")
    
    try:
        # 延迟导入：只有真正要跑用例时才加载主审查入口
        from main import run_review

        if not quiet:
            print(f"\n{'='*80}")
            print(f"测试用例: {case_name}")
            print(f"仓库: {repo_name}, PR: {pr_number}")
            print(f"分支: {base_branch} -> {head_branch}")
            print(f"{'='*80}")
        
        exit_code = await run_review(
            repo_path=repo_path,
            base_branch=base_branch,
            head_branch=head_branch,
            output_file=placeholder_output,
            quiet=quiet
        )
        
        if exit_code != 0:
            return (False, f"审查失败，退出码: {exit_code}")
        
        # 结果文件已由 main.py 自动保存到 log 目录
        return (True, f"成功: 结果已保存到 log 目录")
        
    except Exception as e:
        return (False, f"执行异常: {str(e)}")


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数。
    
    Returns:
        解析后的参数命名空间
    """
    parser = argparse.ArgumentParser(
        description="自动化测试脚本 - 批量运行代码审查",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 测试所有用例
  python test/run_automated_tests.py --datasets-dir ./datasets
  
  # 测试前10个用例
  python test/run_automated_tests.py --datasets-dir ./datasets --cases 1-10
  
  # 测试指定仓库的前5个用例
  python test/run_automated_tests.py --datasets-dir ./datasets --repos sentry,grafana --cases 1-5
        """
    )
    
    parser.add_argument(
        "--datasets-dir",
        type=str,
        required=True,
        help="被测仓库数据集目录（必需）"
    )
    
    parser.add_argument(
        "--repos",
        type=str,
        default=None,
        help="要测试的仓库列表，逗号分隔（默认：全部）"
    )
    
    parser.add_argument(
        "--cases",
        type=str,
        default=None,
        help="要测试的 case 范围（在 repos 过滤后再应用），如 '1-10'；单个数字如 '10' 表示前 10 个（默认：全部）"
    )
    
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="输出目录（已弃用，结果文件现在自动保存到 log 目录）"
    )
    
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式（减少输出）"
    )
    
    return parser.parse_args()


async def main():
    """主函数。"""
    args = parse_arguments()
    
    print("🚀 自动化测试脚本")
    print("=" * 80)
    
    # 解析参数
    datasets_dir = Path(args.datasets_dir).resolve()
    
    # 输出目录：保留用于向后兼容，但不再使用（结果文件现在自动保存到 log 目录）
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        # 默认输出到 test/results 目录（已弃用）
        test_dir = Path(__file__).parent
        output_dir = test_dir / "results"
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    repos = None
    if args.repos:
        repos = [r.strip() for r in args.repos.split(",")]
    
    cases_range = None
    if args.cases:
        cases_range = parse_cases_range(args.cases)
    
    # 加载测试用例（优先使用提取后的 test_cases.json）
    test_dir = Path(__file__).parent
    test_file = test_dir / "test_cases.json"
    
    # 如果 test_cases.json 不存在，尝试使用原始的 test_prs.json
    if not test_file.exists():
        test_file = test_dir / "test_prs.json"
        print(f"⚠️  test_cases.json 不存在，使用原始文件 test_prs.json")
        print(f"   提示: 运行 python test/extract_test_cases.py 生成 test_cases.json")
    
    if not test_file.exists():
        print(f"❌ 测试文件不存在: {test_file}")
        return 1
    
    print(f"📁 数据集目录: {datasets_dir}")
    print(f"📄 测试文件: {test_file}")
    print(f"💡 注意: 结果文件将自动保存到 log 目录")
    
    print("\n📖 加载测试用例...")
    try:
        test_data = load_test_cases(test_file)
        all_cases = collect_all_cases(test_data)
        filtered_cases = filter_cases(all_cases, repos=repos, cases_range=cases_range)
        
        print(f"✅ 加载完成: 总共 {len(all_cases)} 个用例，过滤后 {len(filtered_cases)} 个用例")
    except Exception as e:
        print(f"❌ 加载测试用例失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    if not filtered_cases:
        print("⚠️  没有符合条件的测试用例")
        return 0
    
    # 运行测试（顺序执行，不并行）
    print(f"\n🧪 开始运行 {len(filtered_cases)} 个测试用例（顺序执行）...")
    
    results = {
        "success": [],
        "failed": [],
        "skipped": []
    }
    
    # 顺序执行每个测试用例（不并行）
    for i, case in enumerate(filtered_cases, 1):
        if not args.quiet:
            print(f"\n[{i}/{len(filtered_cases)}] ", end="", flush=True)
        
        success, message = await run_single_test(
            case=case,
            datasets_dir=datasets_dir,
            output_dir=output_dir,
            quiet=args.quiet
        )
        
        if success:
            results["success"].append((case["case_name"], message))
            if not args.quiet:
                print(f"✅ {message}")
        else:
            results["failed"].append((case["case_name"], message))
            if not args.quiet:
                print(f"❌ {message}")
    
    # 生成测试报告
    print("\n" + "=" * 80)
    print("📊 测试报告")
    print("=" * 80)
    print(f"✅ 成功: {len(results['success'])}")
    print(f"❌ 失败: {len(results['failed'])}")
    print(f"⏭️  跳过: {len(results['skipped'])}")
    
    if results["failed"]:
        print("\n失败的用例:")
        for case_name, message in results["failed"]:
            print(f"  - {case_name}: {message}")
    
    print(f"\n💾 结果文件保存在: log 目录（由 main.py 自动生成）")
    
    return 0 if len(results["failed"]) == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
