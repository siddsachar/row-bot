// HEVC-with-alpha cuts of the landing page's Buddy clips, for Safari and iOS,
// which drop WebM alpha. macOS only (AVFoundation). Make the ProRes 4444
// master from a reviewed WebM state first, keeping its alpha:
//   ffmpeg -c:v libvpx-vp9 -i buddy/idle.webm -c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le -alpha_bits 16 -vendor apl0 -an idle-master.mov
// then: swiftc -swift-version 5 -O buddy_hevc_alpha.swift -o buddy_hevc_alpha
//   buddy_hevc_alpha idle-master.mov buddy/idle.mov 900000
//   buddy_hevc_alpha --frame buddy/idle.mov 1.0 idle.png   (check one frame's alpha)
import AVFoundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import VideoToolbox

let args = CommandLine.arguments

if args.count == 5 && args[1] == "--frame" {
    let asset = AVURLAsset(url: URL(fileURLWithPath: args[2]))
    let generator = AVAssetImageGenerator(asset: asset)
    generator.requestedTimeToleranceBefore = .zero
    generator.requestedTimeToleranceAfter = .zero
    let image = try! generator.copyCGImage(at: CMTime(seconds: Double(args[3])!, preferredTimescale: 600), actualTime: nil)
    let destination = CGImageDestinationCreateWithURL(URL(fileURLWithPath: args[4]) as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(destination, image, nil)
    CGImageDestinationFinalize(destination)
    print("frame \(image.width)x\(image.height) alphaInfo=\(image.alphaInfo.rawValue)")
    exit(0)
}

let source = URL(fileURLWithPath: args[1])
let target = URL(fileURLWithPath: args[2])
let bitrate = Int(args[3])!
try? FileManager.default.removeItem(at: target)

let asset = AVURLAsset(url: source)
let track = asset.tracks(withMediaType: .video)[0]
let reader = try! AVAssetReader(asset: asset)
let output = AVAssetReaderTrackOutput(track: track, outputSettings: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
])
reader.add(output)

let writer = try! AVAssetWriter(outputURL: target, fileType: .mov)
let size = track.naturalSize
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.hevcWithAlpha,
    AVVideoWidthKey: Int(size.width),
    AVVideoHeightKey: Int(size.height),
    AVVideoCompressionPropertiesKey: [
        AVVideoAverageBitRateKey: bitrate,
        AVVideoExpectedSourceFrameRateKey: 24,
        kVTCompressionPropertyKey_TargetQualityForAlpha as String: 0.75,
    ],
])
input.expectsMediaDataInRealTime = false
writer.add(input)

reader.startReading()
writer.startWriting()
writer.startSession(atSourceTime: .zero)
let done = DispatchSemaphore(value: 0)
input.requestMediaDataWhenReady(on: DispatchQueue(label: "encode")) {
    while input.isReadyForMoreMediaData {
        if let sample = output.copyNextSampleBuffer() {
            input.append(sample)
        } else {
            input.markAsFinished()
            writer.finishWriting { done.signal() }
            return
        }
    }
}
done.wait()
if writer.status == .completed {
    print("ok \(target.lastPathComponent)")
} else {
    print("failed \(String(describing: writer.error))")
    exit(1)
}
