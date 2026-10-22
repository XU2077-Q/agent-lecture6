# agent-lecture6 —— 组装自己的 Agent（第六讲课后作业）

中山大学课程第六讲《组装自己的 Agent》课后任务：把前三讲的零件（外部通讯、记忆管理、工具调用）
按课件 P4~P19 的八个步骤总装成一个完整的私人 Agent，并让它实际完成一件事 ——
**为周杰伦生成一份音乐生涯 PPT 并发回**（课件 P23 课后任务示例）。

## 文件结构

```
agent.py                  主程序：八步全组装（飞书长连接 + 大模型 + 双记忆 + 四工具 + 技能）
AGENTS.md                 工作规矩（启动时读进系统提示词）
skills/ppt-maker/SKILL.md PPT 制作技能（索引进系统提示词，全文按需读取——渐进披露）
memory/                   短期记忆 {chat_id}.json（滑动窗口 20 条）+ 长期记忆 日期.txt
workspace/                用户文件与成品产出区
docs/                     讲解 Word、Agent 功能说明 Word、演示留痕
llm_base.txt / llm_model.txt / llm_key.txt   大模型三项配置（密钥 gitignore 不入库）
feishu_app_id.txt / feishu_app_secret.txt    飞书自建应用凭据（可选，gitignore 不入库）
```

## 运行

```bash
pip install -r requirements.txt

python agent.py          # 配好飞书凭据时：长连接接入飞书，等用户发消息
python agent.py --cli    # 无飞书凭据：终端里跑同一条 handle() Agent 循环
```

## 八步总装对照（课件页码）

| 步骤 | 课件 | agent.py 对应 |
|---|---|---|
| ① 接入飞书 | P4~P5 | `run_feishu()` / `on_message_receive` / `reply_text`（忽略机器人自己的消息防死循环） |
| ② 接入大模型 | P6~P7 | `ask()`：OpenAI 兼容接口，地址/密钥/型号三项配置 |
| ③ 短期记忆 | P8~P9 | `load_memory` / `save_memory`，滑动窗口 20 条，落盘可恢复 |
| ④ 长期记忆 | P10~P11 | `log_memory()`：`[HH:MM] 角色: 内容` 按天追加，只写不读 |
| ⑤ 读取记忆 | P12~P13 | `run_cmd` 工具 + `handle()` Agent 循环（最多连续 10 轮） |
| ⑥ 读写文件 | P14~P15 | `read_file`（8000 字截断）/ `edit_file`（唯一段替换省 token） |
| ⑦ 发送文件 | P16~P17 | `send_file`：先上传拿 file_key 再发文件消息；`CURRENT_CHAT` + 锁 |
| ⑧ 装上技能 | P18~P19 | `AGENTS.md` 规矩 + `skills_index()` 渐进披露 + `workspace/` |

## 课后任务留痕

CLI 模式实跑 DeepSeek：让 Agent 为周杰伦生成中文 PPT——它自己读 SKILL.md → 写生成脚本 →
跑 python-pptx → send_file「发回」。全程工具调用日志见 `docs/demo_transcript.txt`，
成品 `workspace/jay_chou.pptx` 与生成脚本同目录。
