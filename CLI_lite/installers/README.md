# 离线安装包目录

本目录用于存放Python和Node.js的离线安装包，支持无网络环境下自动安装。

## 文件命名规则

安装程序会按照以下顺序查找安装包：

### 1. 精确匹配（推荐）
文件名必须与以下名称完全一致：
- **Python**: `python-3.12.4-amd64.exe`
- **Node.js**: `node-v20.15.0-x64.msi`

### 2. 模糊匹配
如果精确匹配失败，会查找以组件名开头的安装文件：
- **Python**: 以`python`开头，以`.exe`结尾的文件
- **Node.js**: 以`node`开头，以`.msi`结尾的文件

## 下载地址

### Python 3.12.4
- 官方下载: https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe
- 国内镜像: https://mirrors.tuna.tsinghua.edu.cn/python/ftp/python/3.12.4/python-3.12.4-amd64.exe

### Node.js 20.15.0
- 官方下载: https://nodejs.org/dist/v20.15.0/node-v20.15.0-x64.msi
- 国内镜像: https://npmmirror.com/mirrors/node/v20.15.0/node-v20.15.0-x64.msi

## 使用说明

1. 下载对应的安装包到本目录
2. 启动青稞·lite应用程序
3. 程序会自动检测并使用本地安装包
4. 如果本地安装包不存在，程序会提示需要联网下载

## 文件大小参考

- Python 3.12.4: ~25MB
- Node.js 20.15.0: ~30MB

## 注意事项

1. 请确保下载的是64位版本（amd64/x64）
2. 安装包文件名区分大小写
3. 如果同时存在多个匹配文件，会优先使用精确匹配的文件
4. 本目录支持放置多个版本的安装包，但只会使用匹配的版本