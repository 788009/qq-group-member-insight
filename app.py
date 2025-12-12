import uvicorn
import os
import shutil
import hashlib
import time
import json
import sqlite3 # 标准库，用于普通非加密操作
import subprocess
import sys
import gc
from typing import List, Optional, Dict
from fastapi import FastAPI, HTTPException, Body, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, ForeignKey, Integer, func, desc, distinct, select
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.pool import StaticPool

# 尝试导入 pysqlcipher3
try:
    from pysqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    sqlcipher = None

def md5(input_string: str) -> str:
    """
    功能：计算输入字符串的 MD5 散列值 (32位，小写)。
    输入：input_string (str) - 待散列的字符串。
    输出：str - MD5 散列值。
    """
    # 1. 创建 MD5 散列对象
    md5_hasher = hashlib.md5()
    
    # 2. 将字符串以 UTF-8 编码方式更新到散列对象
    data_to_hash = input_string.encode('utf-8')
    md5_hasher.update(data_to_hash)
    
    # 3. 返回 32 位小写的十六进制摘要
    result_hex = md5_hasher.hexdigest()
    
    return result_hex

# ================= 配置与路径 =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# QQ 数据根目录 (Termux 环境)
QQ_ROOT_DIR = "/data/data/com.tencent.mobileqq"
QQ_DB_DIR = os.path.join(QQ_ROOT_DIR, "databases/nt_db")
QQ_UID_DIR = os.path.join(QQ_ROOT_DIR, "files/uid")

# ================= 数据库模型 (Analysis DB) =================
Base = declarative_base()

class Group(Base):
    __tablename__ = 'groups'
    group_id = Column(String, primary_key=True)
    group_name = Column(String)
    members = relationship("Member", back_populates="group")

class Member(Base):
    __tablename__ = 'members'
    group_id = Column(String, ForeignKey('groups.group_id'), primary_key=True)
    user_id = Column(String, primary_key=True)
    user_name = Column(String)
    user_group_name = Column(String)
    group = relationship("Group", back_populates="members")

# ================= 工具类：动态数据库管理 =================
class DBManager:
    """管理多用户的数据库连接"""
    def __init__(self):
        self.engines = {}

    def get_session(self, qq_id: str):
        """获取指定 QQ 用户的数据库会话"""
        db_path = os.path.join(DATA_DIR, qq_id, "group_data.db")
        if not os.path.exists(db_path):
            raise HTTPException(status_code=404, detail=f"用户 {qq_id} 的数据库不存在")
        
        if qq_id not in self.engines:
            # 使用 check_same_thread=False 允许在 FastAPI 异步中使用
            engine = create_engine(
                f"sqlite:///{db_path}", 
                connect_args={"check_same_thread": False},
                poolclass=StaticPool 
            )
            self.engines[qq_id] = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        return self.engines[qq_id]()

    def close_engine(self, qq_id: str):
        """[新增功能] 关闭指定用户的数据库引擎连接"""
        if qq_id in self.engines:
            # 获取绑定的 engine 并释放连接池资源
            engine = self.engines[qq_id].kw['bind']
            engine.dispose()
            del self.engines[qq_id]

    def init_user_db(self, qq_id: str):
        """初始化一个新的用户数据库结构"""
        user_dir = os.path.join(DATA_DIR, qq_id)
        os.makedirs(user_dir, exist_ok=True)
        db_path = os.path.join(user_dir, "group_data.db")
        
        # 确保初始化前释放旧连接（如果有）
        self.close_engine(qq_id)

        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        return db_path

db_manager = DBManager()

# ================= 核心业务逻辑：解密与清洗 =================

class Pipeline:
    @staticmethod
    def get_key(nt_uid: str, db_file_path: str) -> str:
        """根据 nt_uid 和 DB 文件头计算解密 Key"""
        with open(db_file_path, "rb") as f:
            f.seek(0)
            header_chunk = f.read(54)
            seed_bytes = header_chunk[-8:]
            seed_str = str(seed_bytes)[2:-1] 

        uid_hash = md5(nt_uid)
        final_key = md5(uid_hash + seed_str)
        return final_key

    @staticmethod
    def remove_header(source_path: str, dest_path: str):
        """去除前 1024 字节"""
        with open(source_path, "rb") as f:
            data = f.read()[1024:]
        with open(dest_path, "wb") as f:
            f.write(data)

    @staticmethod
    def _find_sqlcipher_binary():
        """寻找可用的 sqlcipher 命令行工具"""
        candidates = ["sqlcipher", "sqlcipher-x64", "sqlcipher-x86"]
        if sys.platform == "win32":
            candidates = [c + ".exe" for c in candidates]
        
        for cmd in candidates:
            try:
                # 尝试运行 version 或者 help
                # sqlcipher 交互式 shell 退出需要 .quit
                # 这里我们通过 stdin 发送 .quit 来测试是否可运行
                subprocess.run([cmd], input=".quit\n", text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                return cmd
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        return None

    @staticmethod
    def decrypt_db(encrypted_path: str, decrypted_path: str, key: str):
        """
        解密数据库
        策略 1: 尝试使用 pysqlcipher3 (Python 库)
        策略 2: 尝试使用 sqlcipher 命令行工具
        """
        # --- 策略 1: Python 库 ---
        if sqlcipher is not None:
            print("[Info] 使用 pysqlcipher3 进行解密")
            if os.path.exists(decrypted_path):
                os.remove(decrypted_path)

            conn = sqlcipher.connect(decrypted_path)
            conn.close()
            conn = None
            try:
                conn = sqlcipher.connect(encrypted_path)
                cursor = conn.cursor()
                cursor.execute(f"PRAGMA key = '{key}';")
                cursor.execute("PRAGMA cipher_page_size = 4096;")
                cursor.execute("PRAGMA kdf_iter = 4000;")
                cursor.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1;")
                cursor.execute("PRAGMA cipher_default_kdf_algorithm = PBKDF2_HMAC_SHA512;")
                cursor.execute("PRAGMA cipher = 'aes-256-cbc';")
                
                cursor.execute("BEGIN;")
                cursor.execute(f"ATTACH DATABASE '{decrypted_path}' AS plaintext KEY '';")
                cursor.execute("SELECT sqlcipher_export('plaintext');")
                cursor.execute("DETACH DATABASE plaintext;")
                conn.commit()
                return # 成功
            except Exception as e:
                print(f"[Warn] pysqlcipher3 解密失败，尝试命令行 fallback: {e}")
                # 如果 Python 库解密失败，继续尝试命令行
            finally:
                if conn: conn.close()
        
        # --- 策略 2: 命令行工具 ---
        cmd_tool = Pipeline._find_sqlcipher_binary()
        if cmd_tool:
            print(f"[Info] 使用命令行工具 {cmd_tool} 进行解密")
            if os.path.exists(decrypted_path):
                os.remove(decrypted_path)
            
            # 构造 SQL 脚本 (注意路径转义和换行)
            # Windows 路径在 SQL 语句中可能需要处理，但 sqlcipher 通常接受正斜杠
            safe_decrypted_path = decrypted_path.replace("\\", "/")
            
            sql_script = f"""
                PRAGMA key = '{key}';
                PRAGMA cipher_page_size = 4096;
                PRAGMA kdf_iter = 4000;
                PRAGMA cipher_hmac_algorithm = HMAC_SHA1;
                PRAGMA cipher_default_kdf_algorithm = PBKDF2_HMAC_SHA512;
                PRAGMA cipher = 'aes-256-cbc';
                ATTACH DATABASE '{safe_decrypted_path}' AS plaintext KEY '';
                SELECT sqlcipher_export('plaintext');
                DETACH DATABASE plaintext;
            """
            
            try:
                # 调用命令行
                subprocess.run(
                    [cmd_tool, encrypted_path],
                    input=sql_script,
                    text=True,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                return # 成功
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr if e.stderr else str(e)
                raise Exception(f"命令行 sqlcipher 执行出错: {err_msg}")
        
        # --- 所有策略失败 ---
        error_detail = (
            "无法解密数据库。后端未检测到 pysqlcipher3 库，也未在 PATH 中找到 sqlcipher/sqlcipher-x64/sqlcipher-x86 可执行文件。\n"
            "请参照项目 GitHub 页面配置环境，或者访问 https://docs.aaqwq.top/decrypt/decode_db.html "
            "手动获取 group_info.decrypted.db 后使用「阶段3」导入。"
        )
        raise Exception(error_detail)

    @staticmethod
    def clean_data_to_analysis_db(source_decrypted_db: str, target_qq_id: str):
        """将解密后的 group_info 数据清洗并导入 Analysis DB"""
        target_db_path = db_manager.init_user_db(target_qq_id)
        
        src_conn = sqlite3.connect(source_decrypted_db)
        src_cursor = src_conn.cursor()
        
        tgt_engine = create_engine(f"sqlite:///{target_db_path}")
        Session = sessionmaker(bind=tgt_engine)
        session = Session()

        try:
            session.query(Member).delete()
            session.query(Group).delete()
            
            # --- 迁移群组 ---
            src_cursor.execute('SELECT * FROM group_list')
            group_rows = src_cursor.fetchall()
            for row in group_rows:
                if len(row) > 5:
                    session.merge(Group(group_id=str(row[0]), group_name=str(row[5])))
            
            # --- 迁移成员 ---
            src_cursor.execute("SELECT * FROM group_member3")
            member_rows = src_cursor.fetchall()
            
            batch = []
            BATCH_SIZE = 5000
            
            for row in member_rows:
                if len(row) <= 5 or row[2] is None or row[5] is None:
                    continue
                    
                g_nick = row[0]
                u_name = row[1]
                g_id = str(row[2])
                u_id = str(row[5])
                
                # [新增功能] 数据清洗：排除用户自己
                if u_id == target_qq_id:
                    continue

                final_nick = g_nick if g_nick else u_name
                
                batch.append(Member(
                    group_id=g_id,
                    user_id=u_id,
                    user_name=u_name,
                    user_group_name=final_nick
                ))
                
                if len(batch) >= BATCH_SIZE:
                    session.bulk_save_objects(batch)
                    batch = []
            
            if batch:
                session.bulk_save_objects(batch)
                
            session.commit()
        except Exception as e:
            session.rollback()
            raise Exception(f"数据清洗失败: {str(e)}")
        finally:
            src_conn.close()
            session.close()

# ================= FastAPI 应用 =================
app = FastAPI(title="QQ Group Analyzer Pro")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Pydantic Schemas
class CreateUserTermux(BaseModel):
    qq_id: str
    nt_uid: str
    cleanup: bool = False

class CreateUserManual(BaseModel):
    qq_id: str
    nt_uid: Optional[str] = None
    key: Optional[str] = None
    cleanup: bool = False
    step: str # "raw", "cleaned", "decrypted"

class JSONImport(BaseModel):
    qq_id: str
    data: Dict[str, dict] # JSON 结构

class IntersectionRequest(BaseModel):
    group_ids: List[str]

# ----- 页面路由 -----
@app.get("/")
async def read_index():
    return FileResponse('index.html')

# ----- 用户管理 API -----

@app.get("/api/system/uids")
def scan_termux_uids():
    """Termux 专用：扫描本地 UID 映射"""
    if not os.path.exists(QQ_UID_DIR):
        return {"available": False, "uids": []}
    
    uids = []
    try:
        for filename in os.listdir(QQ_UID_DIR):
            if "###" in filename:
                parts = filename.split("###")
                if len(parts) == 2:
                    uids.append({"qq": parts[0], "nt_uid": parts[1]})
    except PermissionError:
        return {"available": False, "error": "没有权限读取 /data 目录，请确保使用 tsu 运行"}
    return {"available": True, "uids": uids}

@app.get("/api/users")
def list_users():
    """列出所有已处理的用户"""
    users = []
    if os.path.exists(DATA_DIR):
        for d in os.listdir(DATA_DIR):
            if os.path.isdir(os.path.join(DATA_DIR, d)) and os.path.exists(os.path.join(DATA_DIR, d, "group_data.db")):
                users.append(d)
    return users

@app.post("/api/users/{qq_id}/create_folder")
def create_user_folder(qq_id: str):
    """[新增功能] 创建用户数据文件夹"""
    user_dir = os.path.join(DATA_DIR, qq_id)
    try:
        os.makedirs(user_dir, exist_ok=True)
        return {"status": "success", "detail": f"文件夹已创建: ./data/{qq_id}/"}
    except Exception as e:
        raise HTTPException(500, f"创建文件夹失败: {str(e)}")

@app.delete("/api/users/{qq_id}")
def delete_user(qq_id: str):
    # [修复问题 1] 显式关闭数据库连接并进行垃圾回收，防止文件被占用
    db_manager.close_engine(qq_id)
    gc.collect() 
    
    path = os.path.join(DATA_DIR, qq_id)
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
        return {"status": "deleted"}
    except Exception as e:
        # 如果依然失败，可能是操作系统延迟，尝试稍微等待一下或者直接报错
        raise HTTPException(500, f"删除失败(文件可能被占用): {str(e)}")

@app.post("/api/users/termux_process")
def process_termux(payload: CreateUserTermux):
    """方法 1: Termux 一键全自动"""
    qq_id = payload.qq_id
    nt_uid = payload.nt_uid
    
    user_dir = os.path.join(DATA_DIR, qq_id)
    os.makedirs(user_dir, exist_ok=True)
    
    src_db = os.path.join(QQ_DB_DIR, f"nt_qq_{md5(md5(nt_uid) + 'nt_kernel')}", "group_info.db")
    local_raw = os.path.join(user_dir, "group_info.db")
    local_clean_header = os.path.join(user_dir, "group_info.cleaned.db")
    local_decrypted = os.path.join(user_dir, "group_info.decrypted.db")

    try:
        if not os.path.exists(src_db):
            raise HTTPException(404, f"找不到源文件 {src_db}，请检查是否已登录该 QQ")
        shutil.copy2(src_db, local_raw)
        
        key = Pipeline.get_key(nt_uid, local_raw)
        Pipeline.remove_header(local_raw, local_clean_header)
        Pipeline.decrypt_db(local_clean_header, local_decrypted, key)
        Pipeline.clean_data_to_analysis_db(local_decrypted, qq_id)
        
        if payload.cleanup:
            for f in [local_raw, local_clean_header, local_decrypted]:
                if os.path.exists(f): os.remove(f)
                
        return {"status": "success", "qq_id": qq_id}
        
    except Exception as e:
        # traceback.print_exc() # 可选 debug
        # 将 Pipeline 中 raise 的 Exception detail 传递给前端
        error_msg = str(e)
        if "无法解密数据库" in error_msg:
             raise HTTPException(500, detail=error_msg)
        raise HTTPException(500, f"处理失败: {error_msg}")

@app.post("/api/users/manual_process")
def process_manual(payload: CreateUserManual):
    """方法 2: 手动文件处理"""
    qq_id = payload.qq_id
    user_dir = os.path.join(DATA_DIR, qq_id)
    os.makedirs(user_dir, exist_ok=True) # 确保目录存在
    
    local_raw = os.path.join(user_dir, "group_info.db")
    local_clean_header = os.path.join(user_dir, "group_info.cleaned.db")
    local_decrypted = os.path.join(user_dir, "group_info.decrypted.db")
    
    try:
        if payload.step == "raw":
            if not os.path.exists(local_raw):
                raise HTTPException(400, "未找到 group_info.db，请确认文件已放置")
            if not payload.nt_uid and not payload.key:
                raise HTTPException(400, "处理原始文件需要提供 nt_uid 或 key")
            
            key = payload.key
            if not key:
                key = Pipeline.get_key(payload.nt_uid, local_raw)
            
            Pipeline.remove_header(local_raw, local_clean_header)
            Pipeline.decrypt_db(local_clean_header, local_decrypted, key)
            Pipeline.clean_data_to_analysis_db(local_decrypted, qq_id)
            
        elif payload.step == "cleaned":
            if not os.path.exists(local_clean_header):
                raise HTTPException(400, "未找到 group_info.cleaned.db")
            if not payload.key:
                raise HTTPException(400, "处理去头文件必须提供 Key")
            
            Pipeline.decrypt_db(local_clean_header, local_decrypted, payload.key)
            Pipeline.clean_data_to_analysis_db(local_decrypted, qq_id)
            
        elif payload.step == "decrypted":
            if not os.path.exists(local_decrypted):
                raise HTTPException(400, "未找到 group_info.decrypted.db")
            Pipeline.clean_data_to_analysis_db(local_decrypted, qq_id)
        
        if payload.cleanup and payload.step != "decrypted":
            # 如果是从已解密开始，通常不删已解密文件，除非用户特意要求
            # 这里逻辑简单处理：如果是 raw/cleaned 开始，清理中间文件
            for f in [local_raw, local_clean_header]: 
                if os.path.exists(f): os.remove(f)
            if payload.step != "decrypted" and os.path.exists(local_decrypted):
                os.remove(local_decrypted)

        return {"status": "success", "qq_id": qq_id}

    except Exception as e:
        error_msg = str(e)
        if "无法解密数据库" in error_msg:
             raise HTTPException(500, detail=error_msg)
        raise HTTPException(500, f"处理失败: {error_msg}")

@app.post("/api/users/json_import")
async def process_json(file: UploadFile = File(...), qq_id: str = Body(...)):
    """方法 3: JSON 导入"""
    try:
        content = await file.read()
        data = json.loads(content)
        
        db_path = db_manager.init_user_db(qq_id)
        engine = create_engine(f"sqlite:///{db_path}")
        Session = sessionmaker(bind=engine)
        session = Session()
        
        session.query(Member).delete()
        session.query(Group).delete()
        
        for gid, g_info in data.items():
            g_name = g_info.get("group_name", "Unknown")
            session.merge(Group(group_id=str(gid), group_name=g_name))
            
            members = g_info.get("members", {})
            for uid, u_info in members.items():
                if str(uid) == str(qq_id): continue # [新增功能] JSON 导入时也排除自己

                session.merge(Member(
                    group_id=str(gid),
                    user_id=str(uid),
                    user_name=u_info.get("user_name", ""),
                    user_group_name=u_info.get("user_group_name", "")
                ))
        
        session.commit()
        session.close()
        return {"status": "success", "qq_id": qq_id}
    except Exception as e:
        raise HTTPException(500, f"JSON 导入失败: {str(e)}")

# ----- 数据分析 API (复用逻辑) -----

@app.get("/api/analysis/{qq_id}/search_groups")
def search_groups(qq_id: str, q: str = ""):
    session = db_manager.get_session(qq_id)
    try:
        query = session.query(Group)
        if q:
            query = query.filter(Group.group_name.like(f"%{q}%"))
        return [{"group_id": g.group_id, "group_name": g.group_name} for g in query.limit(20).all()]
    finally:
        session.close()

@app.get("/api/analysis/{qq_id}/frequent_users")
def get_frequent_users(qq_id: str, min_groups: int = 2):
    session = db_manager.get_session(qq_id)
    try:
        stmt = (
            session.query(
                Member.user_id,
                Member.user_name,
                func.count(Member.group_id).label("count")
            )
            .group_by(Member.user_id)
            .having(func.count(Member.group_id) >= min_groups)
            .order_by(desc("count"))
        )
        return [{"user_id": r.user_id, "user_name": r.user_name, "group_count": r.count} for r in stmt.all()]
    finally:
        session.close()

@app.get("/api/analysis/{qq_id}/group_overlap")
def analyze_group_overlap(qq_id: str, group_id: str):
    session = db_manager.get_session(qq_id)
    try:
        target_group_members = session.query(Member.user_id).filter(Member.group_id == group_id).subquery()
        stmt = (
            session.query(
                Member.user_id,
                Member.user_name,
                func.count(Member.group_id).label("count")
            )
            .filter(Member.user_id.in_(select(target_group_members)))
            .group_by(Member.user_id)
            .having(func.count(Member.group_id) > 1)
            .order_by(desc("count"))
        )
        return [{"user_id": r.user_id, "user_name": r.user_name, "group_count": r.count} for r in stmt.all()]
    finally:
        session.close()

@app.post("/api/analysis/{qq_id}/intersection")
def analyze_intersection(qq_id: str, payload: IntersectionRequest):
    if not payload.group_ids: return []
    session = db_manager.get_session(qq_id)
    try:
        target_len = len(payload.group_ids)
        stmt = (
            session.query(Member.user_id, func.max(Member.user_name).label("user_name"))
            .filter(Member.group_id.in_(payload.group_ids))
            .group_by(Member.user_id)
            .having(func.count(distinct(Member.group_id)) == target_len)
        )
        return [{"user_id": r.user_id, "user_name": r.user_name} for r in stmt.all()]
    finally:
        session.close()

@app.get("/api/analysis/{qq_id}/user_groups/{target_user_id}")
def get_user_group_details(qq_id: str, target_user_id: str):
    session = db_manager.get_session(qq_id)
    try:
        results = session.query(Group.group_name, Member.user_group_name)\
            .join(Member, Member.group_id == Group.group_id)\
            .filter(Member.user_id == target_user_id)\
            .all()
        return [{"group_name": row.group_name, "nickname": row.user_group_name} for row in results]
    finally:
        session.close()

if __name__ == "__main__":
    print("启动 WebUI: http://127.0.0.1:8003")
    uvicorn.run(app, host="0.0.0.0", port=8003)