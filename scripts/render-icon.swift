import CoreGraphics
import ImageIO
import Foundation

let size = 1024
let colorSpace = CGColorSpaceCreateDeviceRGB()
let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
                   bytesPerRow: size * 4, space: colorSpace,
                   bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let rect = CGRect(x: 0, y: 0, width: size, height: size)

// Deep navy field with a subtle inset panel for a crisp macOS dark-mode icon.
ctx.setFillColor(CGColor(red: 0.025, green: 0.043, blue: 0.078, alpha: 1))
ctx.fill(rect)
ctx.setFillColor(CGColor(red: 0.055, green: 0.082, blue: 0.125, alpha: 1))
ctx.fillEllipse(in: CGRect(x: 82, y: 82, width: 860, height: 860))

let center = CGPoint(x: 512, y: 490)
let radius: CGFloat = 312
let start = CGFloat.pi * 1.14
let end = CGFloat.pi * 1.86

// Instrument bezel and bright cyan performance arc.
ctx.setLineCap(.round)
ctx.setLineWidth(54)
ctx.setStrokeColor(CGColor(red: 0.15, green: 0.22, blue: 0.31, alpha: 1))
ctx.addArc(center: center, radius: radius, startAngle: start, endAngle: end, clockwise: false)
ctx.strokePath()
ctx.setStrokeColor(CGColor(red: 0.18, green: 0.86, blue: 0.91, alpha: 1))
ctx.addArc(center: center, radius: radius, startAngle: start, endAngle: CGFloat.pi * 1.72, clockwise: false)
ctx.strokePath()
ctx.setStrokeColor(CGColor(red: 0.98, green: 0.56, blue: 0.31, alpha: 1))
ctx.addArc(center: center, radius: radius, startAngle: CGFloat.pi * 1.72, endAngle: end, clockwise: false)
ctx.strokePath()

// Fine white-blue gauge marks.
for i in 0...10 {
    let angle = start + CGFloat(i) * (end - start) / 10
    let outer = CGPoint(x: center.x + cos(angle) * 270, y: center.y + sin(angle) * 270)
    let inner = CGPoint(x: center.x + cos(angle) * (i % 5 == 0 ? 222 : 238),
                        y: center.y + sin(angle) * (i % 5 == 0 ? 222 : 238))
    ctx.setLineWidth(i % 5 == 0 ? 12 : 7)
    ctx.setStrokeColor(CGColor(red: 0.77, green: 0.87, blue: 0.95, alpha: i % 5 == 0 ? 0.95 : 0.64))
    ctx.move(to: inner)
    ctx.addLine(to: outer)
    ctx.strokePath()
}

// Needle points into the fast, healthy range.
let needleAngle = CGFloat.pi * 1.69
let tip = CGPoint(x: center.x + cos(needleAngle) * 220, y: center.y + sin(needleAngle) * 220)
ctx.setLineCap(.round)
ctx.setLineWidth(25)
ctx.setStrokeColor(CGColor(red: 0.98, green: 0.97, blue: 0.91, alpha: 1))
ctx.move(to: center)
ctx.addLine(to: tip)
ctx.strokePath()
ctx.setFillColor(CGColor(red: 0.98, green: 0.56, blue: 0.31, alpha: 1))
ctx.fillEllipse(in: CGRect(x: center.x - 35, y: center.y - 35, width: 70, height: 70))
ctx.setFillColor(CGColor(red: 0.055, green: 0.082, blue: 0.125, alpha: 1))
ctx.fillEllipse(in: CGRect(x: center.x - 13, y: center.y - 13, width: 26, height: 26))

guard CommandLine.arguments.count > 1 else {
    fatalError("Usage: render-icon.swift output.png")
}
let image = ctx.makeImage()!
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let destination = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)!
CGImageDestinationAddImage(destination, image, nil)
CGImageDestinationFinalize(destination)
