"""
青稞·lite 打包脚本
用法: python 青稞lite/build.py
"""
import os
import sys
import shutil
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP_DIR = os.path.join(BASE_DIR, '青稞lite')
DIST_DIR = os.path.join(DESKTOP_DIR, 'dist')
BUILD_DIR = os.path.join(DESKTOP_DIR, 'build')
SPEC_FILE = os.path.join(DESKTOP_DIR, 'build.spec')


def check_dependencies():
    """检查打包依赖是否安装"""
    print("[1/5] 检查依赖...")
    missing = []
    for pkg in ['PyQt5', 'PyQt5.QtWebEngineWidgets', 'flask', 'yaml', 'requests', 'pyinstaller']:
        try:
            __import__(pkg.replace('-', '_').split('.')[0])
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"  缺少依赖: {', '.join(missing)}")
        print(f"  正在安装...")
        subprocess.check_call([
            sys.executable, '-m', 'pip', 'install',
            '-r', os.path.join(DESKTOP_DIR, 'requirements.txt')
        ])
    else:
        print("  依赖检查通过")


def clean_build():
    """清理旧的构建产物"""
    print("[2/5] 清理旧构建...")
    for d in [DIST_DIR, BUILD_DIR]:
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"  已删除: {d}")


def run_pyinstaller():
    """执行 PyInstaller 打包"""
    print("[3/5] 执行 PyInstaller 打包...")
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--clean',
        '--noconfirm',
        '--distpath', DIST_DIR,
        '--workpath', BUILD_DIR,
        SPEC_FILE
    ]
    print(f"  命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=BASE_DIR)
    if result.returncode != 0:
        print("  打包失败!")
        sys.exit(1)
    print("  打包完成")


def copy_runtime_data():
    """复制运行时数据文件到 dist 目录（单文件模式：配置在exe同级）"""
    print("[4/5] 复制运行时数据...")
    dist_app = DIST_DIR  # 单文件模式：exe直接在dist目录下

    # 复制配置文件到exe同级目录（用户可修改）
    config_src = os.path.join(BASE_DIR, 'CLI_lite', 'config')
    config_dst = os.path.join(dist_app, 'config')
    if os.path.exists(config_src):
        if os.path.exists(config_dst):
            shutil.rmtree(config_dst)
        shutil.copytree(config_src, config_dst)
        print(f"  已复制: config/")

    # 复制数据目录（会话、日志等运行时生成的）
    data_dirs = ['data']
    for d in data_dirs:
        src = os.path.join(BASE_DIR, 'CLI_lite', d)
        dst = os.path.join(dist_app, d)
        if os.path.exists(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            print(f"  已复制: {d}/")

    # 复制 dag 目录
    dag_dir = os.path.join(BASE_DIR, 'CLI_lite', 'dag')
    if os.path.exists(dag_dir):
        dst = os.path.join(dist_app, 'dag')
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(dag_dir, dst)
        print(f"  已复制: dag/")

    # 复制 skill 目录（扩展技能库）
    skill_dir = os.path.join(BASE_DIR, 'CLI_lite', 'skill')
    if os.path.exists(skill_dir):
        dst = os.path.join(dist_app, 'skill')
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(skill_dir, dst)
        print(f"  已复制: skill/")

    # 复制 installers 目录（离线安装包）
    installers_src = os.path.join(BASE_DIR, 'CLI_lite', 'installers')
    installers_dst = os.path.join(dist_app, 'installers')
    if os.path.exists(installers_src):
        if os.path.exists(installers_dst):
            shutil.rmtree(installers_dst)
        shutil.copytree(installers_src, installers_dst)
        print(f"  已复制: installers/")
        # 统计安装包数量和大小
        total_size = 0
        count = 0
        for f in os.listdir(installers_dst):
            fp = os.path.join(installers_dst, f)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)
                count += 1
        print(f"  安装包数量: {count}, 总大小: {total_size/(1024*1024):.1f} MB")
    else:
        print(f"  警告: 未找到installers目录，离线安装功能将不可用")
        print(f"  请将安装包放置在 {installers_src} 目录下")


def print_summary():
    """输出打包结果"""
    print("[5/5] 打包结果:")
    dist_app = DIST_DIR  # 单文件模式：exe直接在dist目录下
    exe_path = os.path.join(dist_app, 'QingkeLite.exe')

    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"  可执行文件: {exe_path}")
        print(f"  文件大小: {size_mb:.1f} MB")
        print(f"  输出目录: {dist_app}")
        print()
        print("  运行方式:")
        print(f"    直接双击 {exe_path}")
        print(f"    或命令行: \"{exe_path}\"")
        print()
        print("  注意: 配置文件和数据目录在exe同级目录下")
    else:
        print("  打包失败，未找到可执行文件")
        sys.exit(1)


def main():
    print("=" * 50)
    print("  青稞·lite Desktop 打包工具")
    print("=" * 50)
    print()

    check_dependencies()
    clean_build()
    run_pyinstaller()
    copy_runtime_data()
    print_summary()

    print()
    print("打包完成! 产物在 青稞lite/dist/ 目录下")
    print("  双击 QingkeLite.exe 即可运行")


if __name__ == '__main__':
    main()
