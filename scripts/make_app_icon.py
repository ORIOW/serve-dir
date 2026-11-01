#!/usr/bin/env python3
"""make_app_icon.py — 把 SF Symbols「server.rack」渲染成 .icns 应用图标（macOS + pyobjc）。

用法：python3 scripts/make_app_icon.py <输出 .icns 路径>

内部按 Apple .iconset 规范生成全套 PNG（16–1024px 含 @2x），再调用系统
iconutil 合成 icns。样式：蓝色圆角底 + 白色 server.rack，与菜单栏图标同源。
任何一步失败都抛异常退出（build_macos_app.sh 会据此跳过，用系统默认图标）。
"""
import os
import subprocess
import sys
import tempfile

SYM = "server.rack"
SIZES = [  # (画布像素, iconset 文件名)
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
]


def _render_glyph_layer(size, pt):
    """透明底「白色符号」图层：先画黑色符号再 SourceIn 染白（保住 pointSize）。"""
    import AppKit
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(SYM, None)
    if img is None:
        raise RuntimeError("SF Symbols 符号不可用: %s" % SYM)
    img = img.imageWithSymbolConfiguration_(
        AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            pt, AppKit.NSFontWeightMedium))
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False,
        AppKit.NSDeviceRGBColorSpace, 0, 0)
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)
    try:
        s = img.size()
        gx, gy = (size - s.width) / 2.0, (size - s.height) / 2.0
        img.drawInRect_fromRect_operation_fraction_(
            ((gx, gy), (s.width, s.height)), ((0, 0), (0, 0)),
            AppKit.NSCompositingOperationSourceOver, 1.0)
        # 符号默认黑 → 用 SourceIn 依 alpha 蒙版整体染白
        AppKit.NSColor.whiteColor().set()
        ctx.setCompositingOperation_(AppKit.NSCompositingOperationSourceIn)
        AppKit.NSBezierPath.bezierPathWithRect_(((0, 0), (size, size))).fill()
        ctx.setCompositingOperation_(AppKit.NSCompositingOperationSourceOver)
    finally:
        ctx.flushGraphics()
        AppKit.NSGraphicsContext.setCurrentContext_(None)
    layer = AppKit.NSImage.alloc().init()
    layer.addRepresentation_(rep)
    return layer


def render_png(size, path):
    import AppKit
    glyph = _render_glyph_layer(size, size * 0.42)  # 符号主尺寸（扣除底板留白后的视觉占比）
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False,
        AppKit.NSDeviceRGBColorSpace, 0, 0)
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)
    try:
        # 圆角底（按 macOS 图标惯例向内留 10% 透明边，圆角半径约 22.5%）
        m = size * 0.10
        side = size - 2 * m
        radius = side * 0.225
        bg = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((m, m), (side, side)), radius, radius)
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.10, 0.36, 0.92, 1.0).set()
        bg.fill()
        # 白色符号居中叠加（矢量，任意尺寸渲染都清晰）
        glyph.drawInRect_fromRect_operation_fraction_(
            ((0, 0), (size, size)), ((0, 0), (0, 0)),
            AppKit.NSCompositingOperationSourceOver, 1.0)
    finally:
        ctx.flushGraphics()
        AppKit.NSGraphicsContext.setCurrentContext_(None)
    data = rep.representationUsingType_properties_(
        AppKit.NSBitmapImageFileTypePNG, {})
    if not data.writeToFile_atomically_(path, True):
        raise RuntimeError("PNG 写入失败: %s" % path)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = os.path.abspath(sys.argv[1])
    with tempfile.TemporaryDirectory() as td:
        iconset = os.path.join(td, "AppIcon.iconset")
        os.makedirs(iconset)
        for size, name in SIZES:
            render_png(size, os.path.join(iconset, name))
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)
    print("已生成 icns: %s" % out)


if __name__ == "__main__":
    main()
