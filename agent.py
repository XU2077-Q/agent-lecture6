# -*- coding: utf-8 -*-
"""
第六讲《组装自己的Agent》课后作业 —— 完整组装的私人 Agent
============================================================
按课件 P4~P19 八步组装：
  第一步 接入飞书     —— 自建应用 + 长连接收消息（有凭据时启用）
  第二步 接入大模型   —— OpenAI 兼容接口，地址/密钥/型号三项配置
  第三步 短期记忆     —— 每会话一份 messages，滑动窗口 20 条，落盘 memory/{chat_id}.json
  第四步 长期记忆     —— 每天追加 memory/日期.txt，只写不读
  第五步 读取记忆     —— run_cmd 工具 + Agent 循环（最多连续 10 轮工具调用）
  第六步 读写文件     —— read_file / edit_file 专用工具
  第七步 发送文件     —— send_file 工具（上传拿 file_key 再发文件消息）
  第八步 装上技能     —— AGENTS.md 工作规矩 + skills/ 技能索引 + workspace/ 工作区

运行方式：
  python agent.py            有 feishu_app_id.txt/feishu_app_secret.txt 时走飞书长连接
  python agent.py --cli      无飞书凭据时在终端里跑（同一条 handle() Agent 循环）
"""
import glob
import json
import os
import subprocess
import sys
import threading
import time

import requests

# Windows 终端中文输出保险
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------- 配置：大模型三项（环境变量 > 同目录文件 > 默认值） ----------
def _conf(env, fname, default=""):
    v = os.environ.get(env, "").strip()
    if v:
        return v
    if os.path.exists(fname):
        return open(fname, encoding="utf-8").read().strip()
    return default

LLM_BASE_URL = _conf("LLM_BASE_URL", "llm_base.txt", "https://api.deepseek.com/v1")
LLM_API_KEY  = _conf("LLM_API_KEY",  "llm_key.txt")
LLM_MODEL    = _conf("LLM_MODEL",    "llm_model.txt", "deepseek-chat")

# ---------- 配置：飞书（可选；没有就自动进入 CLI 模式） ----------
FEISHU_APP_ID     = _conf("FEISHU_APP_ID", "feishu_app_id.txt")
FEISHU_APP_SECRET = _conf("FEISHU_APP_SECRET", "feishu_app_secret.txt")
FEISHU_API = "https://open.feishu.cn/open-apis"

OS_NAME = "Windows" if os.name == "nt" else "Mac/Linux"
SHELL = ["cmd", "/c"] if os.name == "nt" else ["bash", "-c"]

CLI_MODE = "--cli" in sys.argv or not (FEISHU_APP_ID and FEISHU_APP_SECRET)
CURRENT_CHAT = None          # send_file 需要知道发给谁（配合锁，一次只处理一个任务）
CHAT_LOCK = threading.Lock()

os.makedirs("memory", exist_ok=True)
os.makedirs("workspace", exist_ok=True)


def log(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


# ========== 第一步：接入飞书（P4~P5） ==========
_token_cache = {"token": None, "expire": 0}

def get_tenant_token():
    """飞书自建应用身份凭证，带简单缓存"""
    if _token_cache["token"] and time.time() < _token_cache["expire"]:
        return _token_cache["token"]
    resp = requests.post(FEISHU_API + "/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10).json()
    _token_cache["token"] = resp["tenant_access_token"]
    _token_cache["expire"] = time.time() + resp.get("expire", 7200) - 300
    return _token_cache["token"]


def reply_text(chat_id, text):
    """回一条普通文本消息；CLI 模式直接打印"""
    if CLI_MODE:
        print("\nAgent: " + text + "\n", flush=True)
        return
    log("回复飞书: " + text[:100])
    requests.post(FEISHU_API + "/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": "Bearer " + get_tenant_token(),
                 "Content-Type": "application/json; charset=utf-8"},
        json={"receive_id": chat_id, "msg_type": "text",
              "content": json.dumps({"text": text}, ensure_ascii=False)},
        timeout=10)


# ========== 第二步：接入大模型（P6~P7） ==========
def ask(messages):
    """带着工具清单去问大模型，返回整条 message（可能含 tool_calls）"""
    log("请求大模型...")
    resp = requests.post(LLM_BASE_URL + "/chat/completions",
        headers={"Authorization": "Bearer " + LLM_API_KEY},
        json={"model": LLM_MODEL, "messages": messages, "tools": TOOLS},
        timeout=180)
    data = resp.json()
    if "choices" not in data:
        raise RuntimeError("大模型返回异常: " + json.dumps(data, ensure_ascii=False)[:300])
    return data["choices"][0]["message"]


# ========== 第三步：短期记忆（P8~P9） ==========
HISTORY = {}   # chat_id -> messages 列表

def load_memory(chat_id):
    if chat_id not in HISTORY:
        path = f"memory/{chat_id}.json"
        HISTORY[chat_id] = json.load(open(path, encoding="utf-8")) \
            if os.path.exists(path) else []
    return HISTORY[chat_id]


def save_memory(chat_id):
    msgs = HISTORY[chat_id][-20:]        # 滑动窗口：只留最近 20 条
    HISTORY[chat_id] = msgs
    json.dump(msgs, open(f"memory/{chat_id}.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


# ========== 第四步：长期记忆（P10~P11） ==========
def log_memory(role, text):
    day = time.strftime("%Y-%m-%d")
    now = time.strftime("%H:%M")
    with open(f"memory/{day}.txt", "a", encoding="utf-8") as f:
        f.write(f"[{now}] {role}: {text}\n")


# ========== 第五步：第一个工具 run_cmd（P12~P13） ==========
def run_cmd(cmd):
    """执行命令（Windows 走 cmd，Mac/Linux 走 bash），供模型 grep memory/ 等。
    把当前解释器目录放到 PATH 最前，防 WindowsApps 的 python 占位 stub 抢道。"""
    log(f"执行命令[{OS_NAME}]: {cmd}")
    if os.name == "nt":
        py_dir = os.path.dirname(sys.executable)
        cmd = f"set PATH={py_dir};{py_dir}\\Scripts;%PATH% && {cmd}"
    try:
        res = subprocess.run(SHELL + [cmd],
            capture_output=True, text=True, errors="replace", timeout=120)
        return (res.stdout + res.stderr).strip()[:8000] or "命令已执行，无输出"
    except Exception as e:
        return "命令执行失败: " + str(e)


# ========== 第六步：读写文件专用工具（P14~P15） ==========
def read_file(path):
    log("读文件: " + path)
    if not os.path.exists(path):
        return "文件不存在: " + path
    return open(path, encoding="utf-8", errors="replace").read()[:8000]


def edit_file(path, old_text, new_text):
    """old_text 留空就是新建（或整文件重写）；改文件时只替换唯一一段原文，省 token"""
    if old_text == "":
        log("新建文件: " + path)
        open(path, "w", encoding="utf-8").write(new_text)
        return "已写入 " + path
    log("改文件: " + path)
    if not os.path.exists(path):
        return "文件不存在: " + path
    old = open(path, encoding="utf-8").read()
    if old.count(old_text) != 1:
        return "没找到这段原文（或它出现了多次），先 read_file 看看，多贴几行上下文"
    open(path, "w", encoding="utf-8").write(old.replace(old_text, new_text))
    return "已修改 " + path


# ========== 第七步：发送文件工具（P16~P17） ==========
def send_file(path):
    """把本地文件发回飞书：先上传拿 file_key，再发一条文件消息；CLI 模式只报路径"""
    log("发送文件: " + path)
    if not os.path.exists(path):
        return "文件不存在: " + path
    if CLI_MODE:
        return f"已发送（CLI 模式无飞书通道，文件在 {os.path.abspath(path)}）"
    with open(path, "rb") as f:
        up = requests.post(FEISHU_API + "/im/v1/files",
            headers={"Authorization": "Bearer " + get_tenant_token()},
            data={"file_type": "stream", "file_name": os.path.basename(path)},
            files={"file": f}, timeout=30).json()
    if up.get("code") != 0:
        return "文件上传失败: " + str(up.get("msg"))
    resp = requests.post(FEISHU_API + "/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": "Bearer " + get_tenant_token(),
                 "Content-Type": "application/json; charset=utf-8"},
        json={"receive_id": CURRENT_CHAT, "msg_type": "file",
              "content": json.dumps({"file_key": up["data"]["file_key"]})},
        timeout=10).json()
    if resp.get("code") != 0:
        return "文件发送失败: " + str(resp.get("msg"))
    return "已发送 " + path


# ---------- 工具清单：JSON Schema 登记给模型（P13） ----------
TOOLS = [
    {"type": "function", "function": {
        "name": "run_cmd", "description": f"执行命令（{OS_NAME} 上是 {'cmd' if os.name == 'nt' else 'bash'} 语法）",
        "parameters": {"type": "object",
            "properties": {"cmd": {"type": "string", "description": "要执行的命令"}},
            "required": ["cmd"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "读取文件内容（最多 8000 字符）",
        "parameters": {"type": "object",
            "properties": {"path": {"type": "string", "description": "文件路径"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "edit_file", "description": "新建或修改文件：old_text 留空表示新建/整文件重写；否则把唯一出现的一段 old_text 替换为 new_text",
        "parameters": {"type": "object",
            "properties": {"path": {"type": "string", "description": "文件路径"},
                           "old_text": {"type": "string", "description": "要替换的原文（留空=新建或整写）"},
                           "new_text": {"type": "string", "description": "新内容"}},
            "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {
        "name": "send_file", "description": "把本地文件发回给用户（飞书文件消息）",
        "parameters": {"type": "object",
            "properties": {"path": {"type": "string", "description": "要发送的本地文件路径"}},
            "required": ["path"]}}},
]
TOOL_FUNCS = {"run_cmd": run_cmd, "read_file": read_file,
              "edit_file": edit_file, "send_file": send_file}


# ========== 第八步：AGENTS.md 规矩 + skills 索引（P18~P19） ==========
RULES = open("AGENTS.md", encoding="utf-8").read() if os.path.exists("AGENTS.md") else ""

def skills_index():
    """skills/ 下每个子目录是一个技能；索引只读 SKILL.md 开头的名字和简介"""
    index = []
    for path in sorted(glob.glob("skills/*/SKILL.md")):
        text = open(path, encoding="utf-8").read()
        name = text.split("name:")[1].splitlines()[0].strip()
        desc = text.split("description:")[1].splitlines()[0].strip()
        index.append(f"- {name} ({path})：{desc}")
    return "\n".join(index) or "（暂无技能）"

SYSTEM_PROMPT = (
    f"你是用户的私人 Agent，住在用户的 {OS_NAME} 电脑上，通过{'终端' if CLI_MODE else '飞书'}和用户聊天。\n"
    "你可以调用四个工具：run_cmd 执行命令、read_file 读文件、"
    "edit_file 新建/改文件、send_file 把文件发回给用户。\n"
    "长期记忆：每天聊过的事都追加记在 memory/日期.txt（如 memory/2026-10-21.txt）。\n"
    "想不起用户以前说过什么时，自己用 run_cmd 跑 findstr/grep 关键词 memory/ 去找。\n"
    "回答简明。涉及文件的任务，做完后一定要用 send_file 把成品发回去。\n\n"
    "已装技能（任务相关时，先 read_file 读它的 SKILL.md 全文再做）：\n"
    + skills_index() + "\n\n" + RULES)


# ========== Agent 主循环（P13 的 handle） ==========
def handle(chat_id, text):
    global CURRENT_CHAT
    with CHAT_LOCK:                        # 一次只处理一个任务
        CURRENT_CHAT = chat_id
        history = load_memory(chat_id)
        history.append({"role": "user", "content": text})
        log_memory("用户", text)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        try:
            for _ in range(10):            # 最多连续调 10 轮工具，防死循环
                msg = ask(messages)
                messages.append(msg)
                if not msg.get("tool_calls"):
                    reply = msg["content"] or ""
                    history.append({"role": "assistant", "content": reply})
                    save_memory(chat_id)
                    log_memory("我", reply)
                    reply_text(chat_id, reply)
                    return
                for call in msg["tool_calls"]:
                    args = json.loads(call["function"]["arguments"])
                    log(f"调用工具 {call['function']['name']}: "
                        + json.dumps(args, ensure_ascii=False)[:120])
                    out = TOOL_FUNCS[call["function"]["name"]](**args)
                    messages.append({"role": "tool",
                        "tool_call_id": call["id"], "content": str(out)})
            reply_text(chat_id, "工具调用太多次了，我先歇会儿，你换个问法试试")
        except Exception as e:
            reply_text(chat_id, "出错了: " + str(e))
        finally:
            CURRENT_CHAT = None


# ========== 飞书长连接入口（第一步的事件回调，P5） ==========
def run_feishu():
    import lark_oapi as lark

    def on_message_receive(data: "lark.im.v1.P2ImMessageReceiveV1"):
        msg = data.event.message
        if data.event.sender.sender_type != "user":
            return                     # 忽略机器人自己的消息，防死循环
        if msg.message_type != "text":
            reply_text(msg.chat_id, "我现在只看得懂文字消息哦～")
            return
        text = json.loads(msg.content).get("text", "").strip()
        log("收到: " + text)
        threading.Thread(target=handle, args=(msg.chat_id, text), daemon=True).start()

    handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(on_message_receive).build()
    client = lark.ws.Client(FEISHU_APP_ID, FEISHU_APP_SECRET,
                            event_handler=handler, log_level=lark.LogLevel.INFO)
    log("飞书长连接已启动，等待消息...")
    client.start()


# ========== CLI 入口：无飞书凭据时在终端跑同一条 Agent 循环 ==========
def run_cli():
    log("CLI 模式（未检测到飞书凭据或指定 --cli），chat_id=cli-local，输入 quit 退出")
    while True:
        try:
            text = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text or text.lower() in ("quit", "exit"):
            break
        handle("cli-local", text)


if __name__ == "__main__":
    if not LLM_API_KEY:
        print("缺少大模型密钥：请配置 llm_key.txt 或环境变量 LLM_API_KEY")
        sys.exit(1)
    if CLI_MODE:
        run_cli()
    else:
        run_feishu()
