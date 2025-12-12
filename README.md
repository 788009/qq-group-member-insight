# QQ Group Member Insight

*互联网真小*

主要提供以下三个功能：

- 查询共同群聊数量 >= 2 的用户
  <details>
    <summary>点击查看示例图片</summary>

    ![example-tab1-phone.jpg](https://github.com/788009/qq-group-member-insight/blob/main/images/example-tab1-phone.jpg?raw=true)

    ![example-tab1-PC.jpg](https://github.com/788009/qq-group-member-insight/blob/main/images/example-tab1-PC.png?raw=true)
    
  </details>
- 查询指定群聊成员还在哪些群聊
  <details>
    <summary>点击查看示例图片</summary>

    ![example-tab2-PC.jpg](https://github.com/788009/qq-group-member-insight/blob/main/images/example-tab2-PC.png?raw=true)

  </details>
- 查询指定的若干群聊有哪些共同成员
  <details>
    <summary>点击查看示例图片</summary>

    ![example-tab3-PC.jpg](https://github.com/788009/qq-group-member-insight/blob/main/images/example-tab3-PC.png?raw=true)

  </details>

本工具除了在前端获取 Tailwind CSS 模板，无任何联网功能，**所有操作均在本地完成**。

## 使用方法

若不是 Android 或没有 Root，[跳转通用方案](#%E9%80%9A%E7%94%A8%E6%96%B9%E6%A1%88)。

### Android (需要 Root)

此为最推荐的方法，运行服务后，可以一键获取数据，程序会自动获取并处理 QQ 私有目录下的数据库（不会修改 QQ 私有目录下的任何数据）。

<details>
  <summary>点击查看示例图片</summary>
  
  ![import-tsu.jpg](https://github.com/788009/qq-group-member-insight/blob/main/images/import-tsu.jpg?raw=true)

</details>

以下所有步骤均在 [Termux](https://github.com/termux/termux-app) 中操作。

#### 1. 安装环境

由于在 Termux 中编译 `pydantic-core` (`fastapi` 的依赖) 非常耗时，作者提供预编译的 `.whl`。

若你的系统为 `aarch64`，且 Python 版本为 `3.12`，可以使用以下命令快速安装：

```bash
pkg update
pkg install tsu python3 sqlcipher -y
curl -LO https://github.com/788009/pydantic-core-termux-whl/releases/download/v1.0/pydantic_core-2.41.5-cp312-cp312-linux_aarch64.whl
pip3 install pydantic_core-2.41.5-cp312-cp312-linux_aarch64.whl
pip3 install sqlalchemy pysqlcipher3 uvicorn fastapi python-multipart
```

若你的环境不满足上述要求，或者你不信任作者发布的 `.whl`，你也可以使用以下命令安装，但耗时非常长：

```bash
pkg update
pkg install tsu python3 rust binutils-is-llvm sqlcipher -y
pip3 install sqlalchemy pysqlcipher3 uvicorn fastapi python-multipart
```

#### 2. 运行服务

```bash
tsu
curl -LO https://github.com/788009/qq-group-member-insight/releases/download/v1.0.0/qq-group-member-insight-v1.0.0.zip
unzip qq-group-member-insight-v1.0.0.zip
cd qq-group-member-insight-v1.0.0
python3 app.py
```

默认使用 `8003` 端口，你也可以使用 `--port` 参数指定端口：

```bash
python3 app.py --port <port>
```

#### 3. 功能使用

打开浏览器，访问 `localhost:8003`（或者你指定的端口），点击“去管理数据”，选择目标 QQ 号，一键提取。

### 通用方案

本工具在任何平台都可以使用，但需要自行获取 QQ 目录下的 `group_info.db`，且可能需要自行解密，**难度不一定比 Root 低**。

获取数据库与解密参考：[QQDecrypt](https://docs.aaqwq.top/decrypt/description.html)

#### 环境要求

要求以下 Python 库：
- `fastapi`
- `uvicorn`
- `pysqlcipher3` (若需要使用本工具解密数据库)
  - 为了解决 Windows 难以安装 `pysqlcipher3` 的问题，若无法导入 `pysqlcipher3`，本工具会自动尝试通过命令行调用 `sqlcipher`，你可以在[这里](https://github.com/QQBackup/sqlcipher-github-actions/releases/tag/latest)下载 `sqlcipher` 的可执行文件，然后手动将所在目录加入 Path。

#### 功能使用

环境安装完成后，在 [releases](https://github.com/788009/qq-group-member-insight/releases) 中下载 `.zip` 文件，解压后运行 `app.py`。

若要自定义端口，可以在命令行执行 `python3 app.py --port <port>`。

运行服务后，在浏览器中访问 `localhost:8003`（或者你指定的端口），点击“去管理数据”。

目前支持两类导入数据的方案：

1. 通过数据库及必要信息

   <details>
    <summary>点击查看示例图片</summary>

    ![import-db.jpg](https://github.com/788009/qq-group-member-insight/blob/main/images/import-db.png?raw=true)

  </details>

2. 通过 JSON

   <details>
    <summary>点击查看示例图片</summary>

    ![import-json.jpg](https://github.com/788009/qq-group-member-insight/blob/main/images/import-json.png?raw=true)

  </details>

## 致谢

- [QQDecrypt](https://docs.aaqwq.top/decrypt/description.html): 获取数据库与解密方法参考。

### 许可证

MIT License