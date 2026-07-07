"""
环境检测与自动安装模块（离线闭环版）
优先从本地 installers/ 目录读取安装包，无网络环境下也能完成安装
"""

import os
import sys
import shutil
import subprocess
import threading
import time
import urllib.request
import tempfile
import json
from pathlib import Path


class EnvComponent:
    """环境组件定义"""
    def __init__(self, name, display_name, check_cmd, version_cmd, 
                 download_url, install_args, local_filename, min_version=None):
        self.name = name
        self.display_name = display_name
        self.check_cmd = check_cmd
        self.version_cmd = version_cmd
        self.download_url = download_url
        self.install_args = install_args
        self.local_filename = local_filename  # 本地安装包文件名
        self.min_version = min_version


# 环境组件定义
COMPONENTS = {
    "python": EnvComponent(
        name="python",
        display_name="Python",
        check_cmd=["python", "--version"],
        version_cmd=["python", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
        download_url="https://www.python.org/ftp/python/{version}/python-{version}-amd64.exe",
        install_args=["/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_pip=1"],
        local_filename="python-3.12.4-amd64.exe",
        min_version="3.9"
    ),
    "node": EnvComponent(
        name="node",
        display_name="Node.js",
        check_cmd=["node", "--version"],
        version_cmd=["node", "--version"],
        download_url="https://nodejs.org/dist/{version}/node-{version}-x64.msi",
        install_args=["/quiet", "INSTALLDIR=C:\\Program Files\\nodejs"],
        local_filename="node-v20.15.0-x64.msi",
        min_version="18.0"
    )
}

# 已知的最新稳定版本
KNOWN_VERSIONS = {
    "python": "3.12.4",
    "node": "20.15.0"
}


def _get_installers_dir():
    """获取本地安装包目录（exe同级目录下的 installers/）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller打包模式：exe所在目录
        base_dir = os.path.dirname(sys.executable)
    else:
        # 开发模式：项目根目录
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    return os.path.join(base_dir, "installers")


def _find_local_installer(component_name):
    """查找本地安装包"""
    comp = COMPONENTS.get(component_name)
    if not comp:
        return None
    
    installers_dir = _get_installers_dir()
    
    # 1. 精确匹配文件名
    exact_path = os.path.join(installers_dir, comp.local_filename)
    if os.path.isfile(exact_path):
        return exact_path
    
    # 2. 模糊匹配（同组件名开头的文件）
    if os.path.isdir(installers_dir):
        prefix = component_name.lower()
        for f in os.listdir(installers_dir):
            if f.lower().startswith(prefix) and (f.endswith('.exe') or f.endswith('.msi')):
                return os.path.join(installers_dir, f)
    
    return None


def list_local_installers():
    """列出本地可用的安装包"""
    installers_dir = _get_installers_dir()
    result = {}
    
    if not os.path.isdir(installers_dir):
        return result
    
    for name, comp in COMPONENTS.items():
        path = _find_local_installer(name)
        if path:
            size_mb = os.path.getsize(path) / (1024 * 1024)
            result[name] = {
                "path": path,
                "filename": os.path.basename(path),
                "size_mb": round(size_mb, 1)
            }
    
    return result


class EnvChecker:
    """环境检测器"""
    
    def __init__(self):
        self.results = {}
    
    def check_component(self, component_name):
        """检测单个组件是否安装"""
        comp = COMPONENTS.get(component_name)
        if not comp:
            return {"installed": False, "version": None, "error": "未知组件"}
        
        try:
            result = subprocess.run(
                comp.check_cmd,
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            if result.returncode == 0:
                version_output = (result.stdout + result.stderr).strip()
                version = self._extract_version(version_output, component_name)
                return {"installed": True, "version": version, "error": None}
            else:
                return {"installed": False, "version": None, "error": result.stderr.strip()}
        except FileNotFoundError:
            return {"installed": False, "version": None, "error": f"未找到 {comp.display_name}"}
        except subprocess.TimeoutExpired:
            return {"installed": False, "version": None, "error": "检测超时"}
        except Exception as e:
            return {"installed": False, "version": None, "error": str(e)}
    
    def check_all(self):
        """检测所有组件"""
        results = {}
        for name in COMPONENTS:
            results[name] = self.check_component(name)
        self.results = results
        return results
    
    def _extract_version(self, output, component_name):
        """从命令输出中提取版本号"""
        import re
        match = re.search(r'(\d+\.\d+\.\d+)', output)
        if match:
            return match.group(1)
        match = re.search(r'v(\d+\.\d+\.\d+)', output)
        if match:
            return match.group(1)
        return output.strip()
    
    def is_all_ready(self):
        """检查所有组件是否就绪"""
        if not self.results:
            self.check_all()
        return all(r["installed"] for r in self.results.values())
    
    def get_missing(self):
        """获取缺失的组件列表"""
        if not self.results:
            self.check_all()
        return [name for name, r in self.results.items() if not r["installed"]]


class EnvInstaller:
    """环境自动安装器（离线优先）"""
    
    def __init__(self, progress_callback=None, log_callback=None):
        self.progress_callback = progress_callback or (lambda *a: None)
        self.log_callback = log_callback or (lambda *a: None)
        self._cancel = False
        self.temp_dir = tempfile.mkdtemp(prefix="qingke_env_")
        self._local_installers = list_local_installers()
    
    def cancel(self):
        """取消安装"""
        self._cancel = True
    
    def has_local_installer(self, component_name):
        """检查是否有本地安装包"""
        return component_name in self._local_installers
    
    def get_install_info(self):
        """获取安装信息（用于UI显示）"""
        info = {}
        for name, comp in COMPONENTS.items():
            if name in self._local_installers:
                local = self._local_installers[name]
                info[name] = {
                    "source": "local",
                    "path": local["path"],
                    "size_mb": local["size_mb"],
                    "desc": f"本地安装包: {local['filename']} ({local['size_mb']}MB)"
                }
            else:
                version = KNOWN_VERSIONS.get(name, "latest")
                info[name] = {
                    "source": "network",
                    "path": None,
                    "size_mb": 0,
                    "desc": f"需要联网下载: {comp.display_name} {version}"
                }
        return info
    
    def install_component(self, component_name):
        """安装单个组件（离线优先）"""
        comp = COMPONENTS.get(component_name)
        if not comp:
            self.log_callback(f"错误：未知组件 {component_name}")
            return False
        
        self.log_callback(f"开始安装 {comp.display_name}...")
        self.progress_callback(component_name, 0, f"准备安装 {comp.display_name}")
        
        # 1. 查找安装包（优先本地）
        filepath = _find_local_installer(component_name)
        
        if filepath:
            # 本地安装包
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            self.log_callback(f"找到本地安装包: {os.path.basename(filepath)} ({size_mb:.1f}MB)")
            self.progress_callback(component_name, 30, f"使用本地安装包 ({size_mb:.1f}MB)")
        else:
            # 需要下载
            version = KNOWN_VERSIONS.get(component_name, "latest")
            url = comp.download_url.format(version=version)
            filename = comp.local_filename
            filepath = os.path.join(self.temp_dir, filename)
            
            self.log_callback(f"未找到本地安装包，需要联网下载 {comp.display_name} {version}")
            self.progress_callback(component_name, 10, f"正在下载 {comp.display_name} {version}")
            
            try:
                success = self._download_file(url, filepath, component_name)
                if not success:
                    self.log_callback(f"下载失败，请将安装包放入 {_get_installers_dir()} 目录")
                    self.log_callback(f"文件名: {comp.local_filename}")
                    return False
            except Exception as e:
                self.log_callback(f"下载失败：{e}")
                self.log_callback(f"离线安装方法：将 {comp.local_filename} 放入 {_get_installers_dir()} 目录")
                return False
        
        if self._cancel:
            return False
        
        # 2. 执行安装
        self.log_callback(f"正在安装 {comp.display_name}（静默安装，请稍候）...")
        self.progress_callback(component_name, 50, f"正在安装 {comp.display_name}...")
        
        try:
            success = self._run_installer(filepath, comp, component_name)
            if not success:
                return False
        except Exception as e:
            self.log_callback(f"安装失败：{e}")
            self.progress_callback(component_name, 50, f"安装失败：{e}")
            return False
        
        if self._cancel:
            return False
        
        # 3. 验证安装
        self.progress_callback(component_name, 85, f"验证 {comp.display_name} 安装...")
        self.log_callback(f"验证 {comp.display_name} 安装...")
        time.sleep(2)
        
        # 刷新环境变量
        self._refresh_path()
        
        checker = EnvChecker()
        result = checker.check_component(component_name)
        
        if result["installed"]:
            self.log_callback(f"{comp.display_name} 安装成功！版本：{result['version']}")
            self.progress_callback(component_name, 100, f"{comp.display_name} 安装成功！版本：{result['version']}")
            return True
        else:
            self.log_callback(f"{comp.display_name} 安装完成，但验证失败。可能需要重启应用。")
            self.progress_callback(component_name, 100, f"{comp.display_name} 安装完成，可能需要重启")
            return True
    
    def _download_file(self, url, filepath, component_name):
        """下载文件，带进度回调"""
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "QingkeLite-EnvSetup/1.0"
            })
            response = urllib.request.urlopen(req, timeout=30)
            total_size = int(response.headers.get("Content-Length", 0))
            
            downloaded = 0
            block_size = 8192
            
            with open(filepath, 'wb') as f:
                while True:
                    if self._cancel:
                        response.close()
                        return False
                    
                    chunk = response.read(block_size)
                    if not chunk:
                        break
                    
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size > 0:
                        percent = 10 + int(40 * downloaded / total_size)
                        size_mb = downloaded / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        self.progress_callback(
                            component_name, percent,
                            f"下载中：{size_mb:.1f}MB / {total_mb:.1f}MB"
                        )
                        if downloaded % (1024 * 1024) < block_size:  # 每1MB记录一次
                            self.log_callback(f"下载进度：{size_mb:.1f}MB / {total_mb:.1f}MB")
            
            self.log_callback(f"下载完成：{os.path.basename(filepath)}")
            return True
            
        except urllib.error.HTTPError as e:
            self.log_callback(f"下载失败：HTTP {e.code} - {e.reason}")
            return self._download_fallback(url, filepath, component_name)
        except Exception as e:
            self.log_callback(f"下载异常：{e}")
            return self._download_fallback(url, filepath, component_name)
    
    def _download_fallback(self, url, filepath, component_name):
        """备用下载方式：使用PowerShell"""
        self.log_callback("尝试备用下载方式（PowerShell）...")
        self.progress_callback(component_name, 15, "使用备用下载方式...")
        
        try:
            cmd = f'Invoke-WebRequest -Uri "{url}" -OutFile "{filepath}" -UseBasicParsing'
            result = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True, text=True, timeout=600,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            if result.returncode == 0 and os.path.exists(filepath):
                self.log_callback("备用下载完成")
                return True
            else:
                self.log_callback(f"备用下载失败：{result.stderr}")
                return False
        except Exception as e:
            self.log_callback(f"备用下载异常：{e}")
            return False
    
    def _run_installer(self, filepath, comp, component_name):
        """执行安装程序"""
        try:
            if filepath.endswith('.msi'):
                cmd = ["msiexec", "/i", filepath] + comp.install_args
            else:
                cmd = [filepath] + comp.install_args
            
            self.log_callback(f"执行安装：{os.path.basename(filepath)}")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            # 等待安装完成，期间更新进度
            elapsed = 0
            while process.poll() is None:
                if self._cancel:
                    process.terminate()
                    return False
                
                time.sleep(2)
                elapsed += 2
                percent = min(50 + int(35 * elapsed / 180), 84)
                self.progress_callback(component_name, percent, f"安装中...（{elapsed}秒）")
                
                if elapsed % 15 == 0:
                    self.log_callback(f"安装进行中...（{elapsed}秒）")
            
            returncode = process.returncode
            if returncode == 0:
                self.log_callback("安装程序执行完成")
                return True
            elif returncode in (1641, 3010):
                self.log_callback("安装完成，可能需要重启系统")
                return True
            else:
                self.log_callback(f"安装程序返回代码：{returncode}（可能已成功）")
                return True
                
        except Exception as e:
            self.log_callback(f"执行安装程序异常：{e}")
            return False
    
    def _refresh_path(self):
        """刷新环境变量PATH（Windows）"""
        if sys.platform != 'win32':
            return
        
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
            )
            try:
                system_path, _ = winreg.QueryValueEx(key, "Path")
            finally:
                winreg.CloseKey(key)
            
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
            try:
                user_path, _ = winreg.QueryValueEx(key, "Path")
            finally:
                winreg.CloseKey(key)
            
            new_path = system_path + ";" + user_path
            os.environ["PATH"] = new_path
            
            self.log_callback("已刷新系统环境变量")
        except Exception as e:
            self.log_callback(f"刷新环境变量失败：{e}")
    
    def cleanup(self):
        """清理临时文件"""
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass


def check_and_report():
    """快速检测并返回报告"""
    checker = EnvChecker()
    results = checker.check_all()
    
    report = []
    for name, result in results.items():
        comp = COMPONENTS[name]
        if result["installed"]:
            report.append(f"✓ {comp.display_name} {result['version']}")
        else:
            # 检查是否有本地安装包
            local = _find_local_installer(name)
            if local:
                report.append(f"○ {comp.display_name} 未安装（本地有安装包）")
            else:
                report.append(f"✗ {comp.display_name} 未安装（需要联网下载）")
    
    return {
        "all_ready": checker.is_all_ready(),
        "missing": checker.get_missing(),
        "results": results,
        "report": "\n".join(report),
        "local_installers": list_local_installers()
    }
