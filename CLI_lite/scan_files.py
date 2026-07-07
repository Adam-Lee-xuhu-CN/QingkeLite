import os
import sys
import time

# 要扫描的扩展名
extensions = {'.doc', '.docx', '.ppt', '.pptx', '.pdf', '.txt', '.md'}

# 扫描的根目录
root_dir = 'D:\\'

# 结果文件
output_file = os.path.join('data', 'output', 'scan_results.txt')

def scan_files():
    results = []
    count_by_ext = {ext: 0 for ext in extensions}
    
    start_time = time.time()
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # 检查超时，比如300秒
        if time.time() - start_time > 300:
            print("扫描超时，已停止。")
            break
        
        for filename in filenames:
            _, ext = os.path.splitext(filename)
            ext = ext.lower()
            if ext in extensions:
                file_path = os.path.join(dirpath, filename)
                results.append(file_path)
                count_by_ext[ext] += 1
    
    # 写入结果文件
    with open(output_file, 'w', encoding='utf-8') as f:
        for path in results:
            f.write(path + '\n')
    
    # 打印统计
    print("扫描完成。")
    print("文件数量统计：")
    for ext, count in count_by_ext.items():
        print(f"{ext}: {count}")
    print(f"总文件数: {len(results)}")
    print(f"结果已保存到: {output_file}")
    
    return results, count_by_ext

if __name__ == '__main__':
    scan_files()