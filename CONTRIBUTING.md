# 贡献指南

感谢你考虑为 FapiaoVault 贡献代码！

## 项目原则（先读这个）

本项目有三条不可妥协的原则，任何 PR 都不得破坏：

1. **纯本地**：核心功能不联网、不上传任何数据。不允许引入任何形式的网络请求。
2. **确定性**：不引入 OCR / 大模型 / 概率性识别。解析结果必须可复现、可验证。
3. **零硬依赖**：`pip install fapiao-vault` 后核心功能必须可用。pypdf、openpyxl
   只能作为可选依赖（extras），缺失时功能优雅降级，绝不能崩。

CI 中的 `core-only` 任务会在**不装任何 extras** 的环境下跑全部测试，
如果你的改动引入了硬依赖，它会直接失败。

## 开发环境

```bash
git clone https://github.com/dfhhvc/fapiao-vault.git
cd fapiao-vault
pip install -e ".[pdf,excel]"
```

## 跑测试

```bash
python -m unittest discover -s tests -v
```

测试是零依赖的（不需要 pypdf / openpyxl），任何环境下都应全绿。

## 跑 Lint

```bash
pip install ruff
ruff check .
ruff format --check .
```

配置在 `pyproject.toml` 的 `[tool.ruff]` 段。提交前请确保两项都通过。

## 提交规范

- 一个 PR 只做一件事（修一个 bug 或加一个功能）
- commit message 用中文或英文均可，但请说清楚"为什么"
- 涉及解析规则（`fields.py`）的改动，必须附带能覆盖该版式的测试样例

## 新增发票版式支持

如果你要支持一种新的发票版式：

1. 在 `tools/make_fixtures.py` 里加一个**脱敏后的合成样例**（不得使用真实发票数据）
2. 在 `tests/` 里加对应解析测试
3. 确认全部测试通过后再提交

**注意**：严禁把真实发票（哪怕是你自己的）提交进仓库。样例必须手工合成。

## 报告问题

提 issue 前请先搜索已有 issue。报 bug 时请附上：

- 操作系统与 Python 版本
- 完整的命令行调用与输出
- 触发问题的发票文件**脱敏样例**（票号、金额、公司名请打码或替换）
