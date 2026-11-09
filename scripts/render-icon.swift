import CoreGraphics
import ImageIO
import Foundation

let size = 1024
let colorSpace = CGColorSpaceCreateDeviceRGB()
let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: size * 4, space: colorSpace, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.setFillColor(CGColor(red: 0.025, green: 0.04, blue: 0.08, alpha: 1)); ctx.fill(CGRect(x: 0, y: 0, width: size, height: size))
let center = CGPoint(x: 512, y: 530); let radius: CGFloat = 330
ctx.setLineCap(.round); ctx.setLineWidth(46)
ctx.setStrokeColor(CGColor(red: 0.18, green: 0.84, blue: 1, alpha: 1)); ctx.addArc(center: center, radius: radius, startAngle: .pi * 1.10, endAngle: .pi * 1.82, clockwise: false); ctx.strokePath()
ctx.setStrokeColor(CGColor(red: 0.62, green: 0.28, blue: 1, alpha: 1)); ctx.addArc(center: center, radius: radius, startAngle: .pi * 1.82, endAngle: .pi * 2.18, clockwise: false); ctx.strokePath()
ctx.setLineWidth(8); ctx.setStrokeColor(CGColor(red: 0.75, green: 0.86, blue: 0.94, alpha: 0.55))
for i in 0...8 { let a = CGFloat.pi * 1.1 + CGFloat(i) * CGFloat.pi * 1.08 / 8; let p1 = CGPoint(x: center.x + cos(a) * 280, y: center.y + sin(a) * 280); let p2 = CGPoint(x: center.x + cos(a) * 310, y: center.y + sin(a) * 310); ctx.move(to: p1); ctx.addLine(to: p2); ctx.strokePath() }
let needleAngle = CGFloat.pi * 1.77; let tip = CGPoint(x: center.x + cos(needleAngle) * 285, y: center.y + sin(needleAngle) * 285)
ctx.setStrokeColor(CGColor(red: 0.98, green: 0.35, blue: 0.72, alpha: 1)); ctx.setLineWidth(22); ctx.move(to: center); ctx.addLine(to: tip); ctx.strokePath()
ctx.setFillColor(CGColor(red: 0.95, green: 0.98, blue: 1, alpha: 1)); ctx.fillEllipse(in: CGRect(x: center.x - 32, y: center.y - 32, width: 64, height: 64))
let image = ctx.makeImage()!; let url = URL(fileURLWithPath: CommandLine.arguments[1]); let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)!; CGImageDestinationAddImage(dest, image, nil); CGImageDestinationFinalize(dest)
