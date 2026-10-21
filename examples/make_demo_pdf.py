"""生成示例课件：python make_demo_pdf.py demo.pdf（3 页，16:9；需要 Noto Sans CJK 字体）。"""
import sys
from PIL import Image, ImageDraw, ImageFont
W, H = 1920, 1080
F = '/usr/share/fonts/opentype/noto/NotoSansCJK-{}.ttc'
def font(w, s): return ImageFont.truetype(F.format(w), s, index=2)   # index 2 = SC
BG, FG, MUT, ACC = (247, 248, 251), (24, 32, 47), (110, 118, 135), (59, 91, 219)
def page(no, total):
    im = Image.new('RGB', (W, H), BG); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 18, H], fill=ACC)
    d.text((W - 120, H - 70), f'{no:02d} / {total:02d}', font=font('Regular', 30), fill=MUT)
    d.text((120, H - 70), '录课视频工坊 · 示例课件', font=font('Regular', 30), fill=MUT)
    return im, d
pages = []
im, d = page(1, 3)
d.text((160, 330), '示例课程 · 第一讲', font=font('Regular', 48), fill=ACC)
d.text((160, 410), '三分钟做一节录课视频', font=font('Bold', 110), fill=FG)
d.rectangle([160, 580, 300, 588], fill=ACC)
d.text((160, 630), '课件 + 讲稿 → 克隆音色配音 → 带字幕视频', font=font('Regular', 48), fill=MUT)
pages.append(im)
im, d = page(2, 3)
d.text((160, 140), '三个步骤', font=font('Bold', 80), fill=FG)
for i, (t, s) in enumerate([('上传材料', '课件导出为 PDF，\n讲稿按模板逐页写好'), ('选择声音', '用内置音色，\n或上传一段录音克隆'), ('生成视频', '逐页配音、自动翻页，\n烧录字幕')]):
    x = 160 + i * 560
    d.rounded_rectangle([x, 380, x + 500, 760], 28, fill=(255, 255, 255), outline=(222, 226, 234), width=3)
    d.ellipse([x + 40, 420, x + 130, 510], fill=ACC); d.text((x + 70, 432), str(i + 1), font=font('Bold', 54), fill=(255, 255, 255))
    d.text((x + 40, 550), t, font=font('Bold', 56), fill=FG)
    d.multiline_text((x + 40, 640), s, font=font('Regular', 34), fill=MUT, spacing=14)
pages.append(im)
im, d = page(3, 3)
d.text((160, 140), '几个小技巧', font=font('Bold', 80), fill=FG)
for i, t in enumerate(['先用“页码范围”只做两三页试听', '读音不对，就在讲稿里改写成想要的读法', '参考录音要清晰安静，文字和录音一字不差']):
    y = 360 + i * 170
    d.ellipse([160, y + 22, 190, y + 52], fill=ACC)
    d.text((230, y), t, font=font('Regular', 58), fill=FG)
pages.append(im)
pages[0].save(sys.argv[1], save_all=True, append_images=pages[1:], resolution=144)
