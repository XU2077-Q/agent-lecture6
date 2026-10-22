---
name: ppt-maker
description: 用 python-pptx 生成中文 PPT 并保存到 workspace/
---

# PPT 制作技能

当用户要求做 PPT（演示文稿、幻灯片）时，按本技能执行。

## 步骤

1. 先想清楚大纲：封面页 + 每页一个要点，一般 5~8 页，中文内容。
2. 在 workspace/ 下用 edit_file 新建一个生成脚本（如 workspace/make_xxx_ppt.py），
   用 python-pptx 库（环境中已安装）编写，要点：
   - `from pptx import Presentation`；`prs = Presentation()`（默认 4:3，可设 16:9）。
   - 用 `prs.slide_layouts[0]` 做封面（标题+副标题），`prs.slide_layouts[1]` 做内容页（标题+要点框）。
   - 要点框 `tf = shape.text_frame`，第一行用 `tf.text`，后续 `tf.add_paragraph()`。
   - **中文字体必须显式设置**（下面这段代码已在 python-pptx 1.0.2 实测通过，直接照抄，
     不要自己另造——docx 的 `rFonts.set(qn('w:eastAsia'),…)` 写法在 pptx 里不存在）：
     ```python
     from pptx.oxml.ns import qn

     def set_font(run, size, bold=False, font="微软雅黑"):
         run.font.size = Pt(size)
         run.font.name = font
         run.font.bold = bold
         rPr = run.font._element
         rPr.get_or_add_latin().set('typeface', font)
         ea = rPr.find(qn('a:ea'))
         if ea is None:
             ea = rPr.makeelement(qn('a:ea'), {})
             rPr.find(qn('a:latin')).addnext(ea)
         ea.set('typeface', font)
     ```
   - 标题字号 32~40，正文 18~20，可适当加粗标题。
3. 用 run_cmd 执行脚本（`python workspace/make_xxx_ppt.py`），检查无报错。
4. 成品 .pptx 必须保存在 workspace/ 下，文件名用英文（如 workspace/xxx.pptx）。
5. 用 send_file 把 .pptx 发回给用户，并一句话说明内容结构。

## 注意

- 生成脚本本身也保留在 workspace/，方便用户复用修改。
- 若执行报错，读报错信息改脚本重跑，最多三次，仍失败就把报错如实告诉用户。
