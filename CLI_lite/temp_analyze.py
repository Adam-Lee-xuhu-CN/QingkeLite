import pandas as pd
import sys

# 读取Excel文件
file_path = r'D:\项目类\CLI_lite应用\test_data\test_employees.xlsx'

try:
    df = pd.read_excel(file_path)
    
    print('=== 基本信息 ===')
    print(f'总行数: {len(df)}')
    print(f'总列数: {len(df.columns)}')
    print()
    
    print('=== 列名 ===')
    for i, col in enumerate(df.columns):
        print(f'{i+1}. {col}')
    print()
    
    print('=== 数据类型 ===')
    for col in df.columns:
        print(f'{col}: {df[col].dtype}')
    print()
    
    print('=== 前5行数据 ===')
    print(df.head().to_string())
    print()
    
    print('=== 数据摘要统计 ===')
    print(df.describe(include='all').to_string())
    print()
    
    print('=== 缺失值统计 ===')
    missing = df.isnull().sum()
    for col in df.columns:
        print(f'{col}: {missing[col]}')
        
except Exception as e:
    print(f'错误: {e}')
