"""课件 → 每页 1920x1080 PNG（p-01.png…）。PPTX/PPT 先用 LibreOffice 转 PDF；非 16:9 的页面用页面边缘色补边。"""
import re, shutil, subprocess, pathlib

def page_count(pdf):
    r = subprocess.run(['pdfinfo', str(pdf)], capture_output=True, text=True)
    m = re.search(r'^Pages:\s+(\d+)', r.stdout, re.M)
    return int(m.group(1)) if m else None

def to_pdf(src, out_dir, log=None):
    src, out_dir = pathlib.Path(src), pathlib.Path(out_dir)
    if src.suffix.lower() == '.pdf':
        return src
    profile = out_dir / 'lo-profile'
    r = subprocess.run(['soffice', f'-env:UserInstallation=file://{profile}', '--headless', '--convert-to', 'pdf',
                        '--outdir', str(out_dir), str(src)], capture_output=True, text=True, timeout=600)
    shutil.rmtree(profile, ignore_errors=True)
    if log:
        log.write(r.stdout + r.stderr)
    pdf = out_dir / (src.stem + '.pdf')
    if not pdf.exists():
        raise RuntimeError('PPTX 转 PDF 失败，请在 PowerPoint 里“另存为 PDF”后上传 PDF')
    return pdf

def to_images(pdf, img_dir, size=(1920, 1080)):
    from PIL import Image
    img_dir = pathlib.Path(img_dir); img_dir.mkdir(parents=True, exist_ok=True)
    raw = img_dir / '_raw'; raw.mkdir(exist_ok=True)
    subprocess.run(['pdftoppm', '-png', '-scale-to-x', str(size[0]), '-scale-to-y', '-1', str(pdf), str(raw / 'p')], check=True)
    for f in raw.glob('p-*.png'):
        no = int(f.stem.split('-')[-1])
        im = Image.open(f).convert('RGB')
        im.thumbnail(size, Image.LANCZOS)
        if im.size != size:
            bg = Image.new('RGB', size, im.getpixel((2, 2)))
            bg.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
            im = bg
        im.save(img_dir / f'p-{no:02d}.png')
    shutil.rmtree(raw)

def thumbnail(png, out, width=480):
    from PIL import Image
    im = Image.open(png).convert('RGB')
    im.thumbnail((width, width), Image.LANCZOS)
    im.save(out, quality=82)
