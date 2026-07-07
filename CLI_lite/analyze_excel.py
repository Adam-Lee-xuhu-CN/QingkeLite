# -*- coding: utf-8 -*-
import pandas as pd
import sys

# 设置标准输出编码为utf-8
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

file_path = r'D:\项目类\CLI_lite应用\test_data\test_employees.xlsx'

try:
    df = pd.read_excel(file_path)
    
    print('# Excel文件数据分析报告')
    print()
    print('## 基本信息')
    print(f'- **文件路径**: {file_path}')
    print(f'- **总行数**: {len(df)}')
    print(f'- **总列数**: {len(df.columns)}')
    print()
    
    print('## 列名列表')
    for i, col in enumerate(df.columns):
        print(f'{i+1}. {col}')
    print()
    
    print('## 数据类型')
    for col in df.columns:
        print(f'- **{col}**: {df[col].dtype}')
    print()
    
    print('## 数据预览（前3行）')
    print(df.head(3).to_markdown(index=False))
    print()
    
    print('## 数值列统计摘要')
    numeric_cols = df.select_dtypes(include=[int, float]).columns
    if len(numeric_cols) > 0:
        stats = df[numeric_cols].describe()
        print(stats.to_markdown())
    else:
        print('无数值列')
    print()
    
    print('## 缺失值统计')
    missing = df.isnull().sum()
    for col in df.columns:
        print(f'- **{col}**: {missing[col]}')
    print()
    
    print('## 数据质量评估')
    total_missing = missing.sum()
    if total_missing == 0:
        print('- ✅ 数据完整，无缺失值')
    else:
        print(f'- ⚠️ 存在 {total_missing} 个缺失值')
    
    # 检查重复行
    duplicates = df.duplicated().sum()
    if duplicates == 0:
        print('- ✅ 无重复行')
    else:
        print(f'- ⚠️ 存在 {duplicates} 个重复行')
    
    print()
    print('---')
    print('*报告生成时间: 2026-07-02*')
    
except Exception as e:
    print(f'错误: {e}')
