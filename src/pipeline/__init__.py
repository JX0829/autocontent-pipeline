"""src/pipeline —— 声明式流水线编排层（T1.x）

分层（见 spec_enhancement.md §4.1）：
    contracts.py  校验层：产物存在性 + 结构契约 + 模型名/预算违例扫描
    runner.py     编排层：按序执行、状态流转、断点续跑
"""
